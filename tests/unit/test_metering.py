"""Battlefield Hardening Batch A: cost / token metering (battlefield-hardening §2).

Covers the execution-cost pipeline end to end: declared tool costs, router
metering, evaluation audit cost, and their aggregation into TrialResult /
RouteObservationStats. Absence of a declaration must stay None — never a
fabricated 0.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest

from capability_runtime import (
    ArtifactValue,
    EvaluationResult,
    Evaluator,
    ExecutionState,
    ExecutionContext,
    ExecutionEnvironment,
    LayerRegistry,
    RegistrationError,
    RouterConfig,
    RoutingAction,
    RoutingContext,
    RoutingDecision,
    RoutingError,
    ToolExecutionStatus,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
    ToolSummary,
    TopologyBuilder,
    TokenUsage,
    build_observation_stats,
    serialize_trial_result,
    tool,
)
from capability_runtime.core.errors import TopologyBuildError
from capability_runtime.core.tool import NodeSelector
from capability_runtime.router.llm_router import LLMRouter
from capability_runtime.topology.loader import TopologyLoader


# ---- metered domain ---------------------------------------------------------


@dataclass(frozen=True)
class Order:
    order_id: str


@dataclass(frozen=True)
class Decision:
    ok: bool


@tool(layer="read", produces=[Order], cost_per_call=0.01)
async def metered_db() -> Order:
    return Order("1")


@tool(layer="analyze", consumes=[Order], produces=[Decision], cost_per_call=0.02)
async def metered_policy(order: Order) -> Decision:
    return Decision(True)


@tool(layer="analyze", consumes=[Order], produces=[Decision])
async def unmetered_policy(order: Order) -> Decision:
    return Decision(True)


@dataclass(frozen=True)
class NeverProduced:
    pass


@tool(layer="analyze", consumes=[NeverProduced], produces=[Decision], cost_per_call=0.5)
async def unreachable_policy(missing: NeverProduced) -> Decision:
    return Decision(True)


@tool(layer="read", produces=[Order], cost_per_call=0.3)
async def slow_db() -> Order:
    await asyncio.sleep(0.2)
    return Order("slow")


@tool(layer="read", produces=[Order], cost_per_call=0.4)
async def broken_db() -> Order:
    raise RuntimeError("backend down")


# ---- declaration validation --------------------------------------------------


def test_tool_rejects_negative_cost() -> None:
    with pytest.raises(RegistrationError):
        tool(layer="read", cost_per_call=-0.1)


def test_tool_rejects_bool_cost() -> None:
    with pytest.raises(RegistrationError):
        tool(layer="read", cost_per_call=True)


def test_spec_rejects_negative_cost() -> None:
    selector = NodeSelector(all_nodes=True)
    with pytest.raises(RegistrationError):
        ToolSpec(
            name="x", layer="read", providers=selector, workers=selector,
            cost_per_call=-1.0,
        )


# ---- executor billing --------------------------------------------------------


def _context(timeout: float | None = None) -> ExecutionContext:
    return ExecutionContext(
        environment=ExecutionEnvironment.SANDBOX,
        max_concurrency=1,
        per_tool_timeout_seconds=timeout,
    )


def test_executor_bills_declared_cost_on_success() -> None:
    execution = asyncio.run(
        ToolExecutor(_context()).execute(metered_db, ExecutionState(query="q"))
    )
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.cost == pytest.approx(0.01)


def test_executor_keeps_cost_none_when_undeclared() -> None:
    state = ExecutionState(query="q")
    state.add_artifact(
        "order",
        ArtifactValue(value=Order("1"), source_tool="db", layer="read"),
    )
    execution = asyncio.run(
        ToolExecutor(_context()).execute(unmetered_policy, state)
    )
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.cost is None


def test_executor_bills_cost_on_tool_error() -> None:
    execution = asyncio.run(
        ToolExecutor(_context()).execute(broken_db, ExecutionState(query="q"))
    )
    assert execution.status is ToolExecutionStatus.ERROR
    assert execution.cost == pytest.approx(0.4)


def test_executor_bills_cost_on_timeout() -> None:
    execution = asyncio.run(
        ToolExecutor(_context(timeout=0.02)).execute(
            slow_db, ExecutionState(query="q")
        )
    )
    assert execution.status is ToolExecutionStatus.ERROR
    assert execution.cost == pytest.approx(0.3)


def test_executor_does_not_bill_when_arguments_unresolved() -> None:
    # The call never reached the tool, so its cost was never incurred.
    execution = asyncio.run(
        ToolExecutor(_context()).execute(unreachable_policy, ExecutionState(query="q"))
    )
    assert execution.status is ToolExecutionStatus.ERROR
    assert execution.cost is None


# ---- topology loader ----------------------------------------------------------


def _tool_entry(**extra):
    base = {"name": "t1", "layer": "read"}
    base.update(extra)
    return {"layers": [{"name": "read", "order": 0}], "tools": [base]}


def test_loader_parses_cost_per_call() -> None:
    topology = TopologyLoader().load_data(_tool_entry(cost_per_call=0.02))
    assert topology.node("t1").spec.cost_per_call == pytest.approx(0.02)


def test_loader_rejects_negative_cost() -> None:
    with pytest.raises(TopologyBuildError, match="cost_per_call"):
        TopologyLoader().load_data(_tool_entry(cost_per_call=-1))


def test_loader_rejects_non_numeric_cost() -> None:
    with pytest.raises(TopologyBuildError, match="cost_per_call"):
        TopologyLoader().load_data(_tool_entry(cost_per_call="free"))


# ---- routing decision metering ------------------------------------------------


def test_decision_rejects_negative_routing_cost() -> None:
    with pytest.raises(RoutingError):
        RoutingDecision(
            action=RoutingAction.EXECUTE,
            selected_tools=("db",),
            routing_cost=-0.1,
        )


def test_decision_rejects_wrong_token_type() -> None:
    with pytest.raises(RoutingError):
        RoutingDecision(
            action=RoutingAction.EXECUTE, selected_tools=("db",), token_usage=12
        )


def _router_config(**kwargs) -> RouterConfig:
    return RouterConfig(model="m", temperature=0.1, max_tools_per_layer=2, **kwargs)


def _routing_context() -> RoutingContext:
    return RoutingContext(
        query="q",
        current_layer="read",
        available_tools=(ToolSummary("db", "read"),),
        topology_version="1.0",
    )


def _body(content: dict, usage: object) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": usage,
    }


def test_llm_router_reads_openai_style_usage() -> None:
    router = LLMRouter(
        router_config=_router_config(),
        _http=lambda payload: _body(
            {"action": "execute", "selected_tools": ["db"]},
            {"prompt_tokens": 100, "completion_tokens": 20},
        ),
    )
    decision = asyncio.run(router.route(_routing_context()))
    assert decision.token_usage == TokenUsage(input_tokens=100, output_tokens=20)
    assert decision.routing_cost is None  # no pricing declared


def test_llm_router_reads_ollama_native_usage() -> None:
    router = LLMRouter(
        router_config=_router_config(),
        _http=lambda payload: _body(
            {"action": "execute", "selected_tools": ["db"]},
            {"prompt_eval_count": 7, "eval_count": 3},
        ),
    )
    decision = asyncio.run(router.route(_routing_context()))
    assert decision.token_usage == TokenUsage(input_tokens=7, output_tokens=3)


def test_llm_router_prices_usage_when_configured() -> None:
    router = LLMRouter(
        router_config=_router_config(
            input_cost_per_1k=0.5, output_cost_per_1k=1.0
        ),
        _http=lambda payload: _body(
            {"action": "execute", "selected_tools": ["db"]},
            {"prompt_tokens": 100, "completion_tokens": 20},
        ),
    )
    decision = asyncio.run(router.route(_routing_context()))
    assert decision.routing_cost == pytest.approx(0.05 + 0.02)


def test_llm_router_tolerates_garbage_usage() -> None:
    router = LLMRouter(
        router_config=_router_config(),
        _http=lambda payload: _body(
            {"action": "execute", "selected_tools": ["db"]}, "lots"
        ),
    )
    decision = asyncio.run(router.route(_routing_context()))
    assert decision.selected_tools == ("db",)
    assert decision.token_usage is None


def test_llm_router_string_fake_still_works_without_usage() -> None:
    content = json.dumps({"action": "execute", "selected_tools": ["db"]})
    router = LLMRouter(
        router_config=_router_config(), _http=lambda payload: content
    )
    decision = asyncio.run(router.route(_routing_context()))
    assert decision.selected_tools == ("db",)
    assert decision.token_usage is None
    assert decision.routing_cost is None


def test_router_config_rejects_negative_price() -> None:
    with pytest.raises(RoutingError):
        _router_config(input_cost_per_1k=-1.0)


# ---- trial-level aggregation ---------------------------------------------------


class MeteredRouter:
    """Picks the first available tool and reports fixed router metering."""

    async def route(self, context: RoutingContext) -> RoutingDecision:
        names = tuple(summary.name for summary in context.available_tools)
        if not names:
            return RoutingDecision(action=RoutingAction.FINISH)
        return RoutingDecision(
            action=RoutingAction.EXECUTE,
            selected_tools=names[:1],
            token_usage=TokenUsage(input_tokens=10, output_tokens=5),
            routing_cost=0.001,
        )


class MeteredEvaluator(Evaluator):
    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        return EvaluationResult(success=True, quality_score=0.9, cost=0.01)


class PlainEvaluator(Evaluator):
    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        return EvaluationResult(success=True)


def _metered_topology():
    layers = LayerRegistry()
    layers.register("read", 0)
    layers.register("analyze", 1)
    tools = ToolRegistry()
    for node in (metered_db, metered_policy, unmetered_policy):
        tools.register(node)
    return TopologyBuilder(layers, tools).build()


def _run(topology, evaluator, router):
    from capability_runtime import Scenario, ScenarioSuite, SlowRegressionRunner

    suite = ScenarioSuite(
        name="metered",
        version="1.0",
        description="metering",
        scenarios=(Scenario(id="s1", query="refund order 1"),),
    )
    return asyncio.run(
        SlowRegressionRunner(
            topology=topology,
            evaluator=evaluator,
            trials_per_scenario=1,
            topology_version="1.0",
            router_config_id="metered-router",
            router=router,
        ).run(suite)
    )


def test_trial_aggregates_tool_routing_and_evaluation_costs() -> None:
    outcome = _run(_metered_topology(), MeteredEvaluator(), MeteredRouter())
    result = outcome.results[0]
    # read (0.01) + analyze (0.02), two routed layers at 0.001 each
    assert result.tool_cost == pytest.approx(0.03)
    assert result.routing_cost == pytest.approx(0.002)
    assert result.cost == pytest.approx(0.032)
    assert result.evaluation_cost == pytest.approx(0.01)
    # router tokens (2 layers x 10/5) flow into the trial total
    assert result.token_usage == TokenUsage(input_tokens=20, output_tokens=10)


def test_trial_stays_unmetered_when_nothing_declared() -> None:
    from tests.unit._slow_helpers import build_topology, make_suite, sync_run

    outcome = sync_run(build_topology(), make_suite("s1"), evaluator=PlainEvaluator())
    result = outcome.results[0]
    assert result.tool_cost is None
    assert result.routing_cost is None
    assert result.cost is None
    assert result.evaluation_cost is None


def test_route_stats_now_carry_costs_end_to_end() -> None:
    outcome = _run(_metered_topology(), MeteredEvaluator(), MeteredRouter())
    topology = _metered_topology()
    obs = build_observation_stats(
        outcome.results,
        edges=[(e.source, e.target) for e in topology.edges()],
    )
    assert obs.route_stats
    for stat in obs.route_stats.values():
        assert stat.costs == (pytest.approx(0.032),)


def test_serialized_trial_carries_metering_fields() -> None:
    outcome = _run(_metered_topology(), MeteredEvaluator(), MeteredRouter())
    payload = serialize_trial_result(outcome.results[0])
    assert payload["tool_cost"] == pytest.approx(0.03)
    assert payload["routing_cost"] == pytest.approx(0.002)
    assert payload["cost"] == pytest.approx(0.032)
    assert payload["evaluation_cost"] == pytest.approx(0.01)
