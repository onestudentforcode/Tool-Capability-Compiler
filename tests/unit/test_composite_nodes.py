"""Composite nodes: spec factory, bounded runtime, metering rollup, flattener.

Offline acceptance for the composite-nodes milestone. The two-iteration
refine machinery (holdover sibling + cross-iteration gate) mirrors
examples/composite_refund; the single-iteration and budget-exhaustion cases
use a minimal inner world defined here.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from capability_runtime import (
    CompositeSpec,
    CompositeSpecError,
    ExecutionEnvironment,
    ExecutionContext,
    ExecutionState,
    ExecutionTrace,
    FakeRouter,
    LayerExecution,
    LayerRegistry,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    TokenUsage,
    ToolExecutionStatus,
    ToolExecutor,
    ToolRegistry,
    TopologyBuilder,
    Trial,
    TrialExecutionStatus,
    TrialResult,
    build_composite_node,
    build_observation_stats,
    flatten_composite_results,
    serialize_trial_result,
)
from capability_runtime.core.errors import TopologyBuildError
from capability_runtime.resources import InMemoryStore

_PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT / "examples" / "slow_refund"))


# ---- minimal inner world -------------------------------------------------------


@dataclass(frozen=True)
class Mark:
    pass


@dataclass(frozen=True)
class Other:
    pass


@dataclass(frozen=True)
class Ping:
    n: int


GATE = InMemoryStore("gate_store")


def _emit_tool():
    from capability_runtime import tool

    @tool(layer="only", produces=[Mark], cost_per_call=0.001)
    async def emit() -> Mark:
        return Mark()

    return emit


def _never_tool():
    from capability_runtime import tool

    @tool(layer="only", produces=[Other], cost_per_call=0.001)
    async def never() -> Other:
        return Other()

    return never


def _single_layer_topology(node):
    layers = LayerRegistry()
    layers.register("only", 0)
    tools = ToolRegistry()
    tools.register(node)
    return TopologyBuilder(layers, tools).build()


def _spec(inner, route, *, stop_when=("mark",), iterations=2, **kwargs):
    return CompositeSpec(
        name=kwargs.pop("name", "comp"),
        layer="act",
        topology=inner,
        route=route,
        stop_when=stop_when,
        max_iterations=iterations,
        **kwargs,
    )


def _execute(node, state=None):
    executor = ToolExecutor(
        ExecutionContext(environment=ExecutionEnvironment.SANDBOX)
    )
    return asyncio.run(
        executor.execute(node, state or ExecutionState(query="q"))
    )


# ---- spec validation (Step 1) -----------------------------------------------------


def test_spec_rejects_invalid_declarations() -> None:
    inner = _single_layer_topology(_emit_tool())
    good = (("emit",),)

    with pytest.raises(CompositeSpecError, match="max_iterations"):
        _spec(inner, good, iterations=0)
    with pytest.raises(CompositeSpecError, match="stop_when"):
        _spec(inner, good, stop_when=())
    with pytest.raises(CompositeSpecError, match="route"):
        _spec(inner, ())
    with pytest.raises(CompositeSpecError, match="route"):
        _spec(inner, (("ghost_tool",),))
    with pytest.raises(CompositeSpecError, match="twice"):
        _spec(inner, good, produces=(Mark, Mark))
    with pytest.raises(CompositeSpecError, match="types"):
        _spec(inner, good, consumes=("not-a-type",))


def test_spec_rejects_self_reference_and_excess_depth() -> None:
    inner = _single_layer_topology(_emit_tool())
    level1 = build_composite_node(_spec(inner, (("emit",),), name="level1"))

    layers = LayerRegistry()
    layers.register("only", 0)
    layers.register("act", 1)
    tools = ToolRegistry()
    tools.register(level1)
    level1_topology = TopologyBuilder(layers, tools).build()

    level2 = build_composite_node(
        CompositeSpec(
            name="level2",
            layer="act",
            topology=level1_topology,
            route=(("level1",),),
            stop_when=("mark",),
            max_iterations=1,
        )
    )

    tools2 = ToolRegistry()
    tools2.register(level2)
    level2_topology = TopologyBuilder(layers, tools2).build()
    with pytest.raises(CompositeSpecError, match="depth"):
        CompositeSpec(
            name="level3",
            layer="act",
            topology=level2_topology,
            route=(("level2",),),
            stop_when=("mark",),
            max_iterations=1,
        )
    with pytest.raises(CompositeSpecError, match="self-reference"):
        CompositeSpec(
            name="level1",
            layer="act",
            topology=level1_topology,
            route=(("level1",),),
            stop_when=("mark",),
            max_iterations=1,
        )


def test_factory_registers_and_builds_edges_like_any_tool() -> None:
    import refund as sandbox

    node = build_composite_node(
        _spec(
            _single_layer_topology(_emit_tool()),
            (("emit",),),
            name="mini_comp",
            capabilities={"x.y"},
        )
    )
    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "act")):
        layers.register(name, order)
    tools = ToolRegistry()
    for item in (sandbox.order_db, sandbox.policy_check, node):
        tools.register(item)
    topology = TopologyBuilder(layers, tools).build()
    assert "mini_comp" in topology.nodes()
    assert topology.has_edge("policy_check", "mini_comp")


# ---- runtime semantics (Step 2-3) ---------------------------------------------------


def test_single_iteration_stop_and_output_extraction() -> None:
    node = build_composite_node(
        _spec(
            _single_layer_topology(_emit_tool()),
            (("emit",),),
            produces=(Mark,),
        )
    )
    execution = _execute(node)
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.composite_detail is not None
    assert len(execution.composite_detail) == 1
    assert isinstance(execution.output_summary, Mark)


def test_budget_exhaustion_fails_with_detail_kept() -> None:
    node = build_composite_node(
        _spec(_single_layer_topology(_never_tool()), (("never",),), iterations=2)
    )
    execution = _execute(node)
    assert execution.status is ToolExecutionStatus.ERROR
    assert len(execution.composite_detail) == 2  # both iterations recorded
    assert "unmet" in str(execution.error)


def test_inner_layer_failure_fails_composite_but_bills() -> None:
    from capability_runtime import RoutingAction, RoutingDecision, tool

    @tool(layer="only", cost_per_call=0.005)
    async def boom() -> None:
        raise RuntimeError("inner boom")

    node = build_composite_node(
        _spec(
            _single_layer_topology(boom),
            (("boom",),),
            iterations=3,
            cost_per_call=0.005,
        )
    )
    execution = _execute(node)
    assert execution.status is ToolExecutionStatus.ERROR
    assert execution.cost == pytest.approx(0.005)  # declared outer cost
    assert len(execution.composite_detail) == 1


def test_detail_survives_serialization() -> None:
    node = build_composite_node(
        _spec(_single_layer_topology(_emit_tool()), (("emit",),))
    )
    execution = _execute(node)
    payload = serialize_trial_result(_trial_result_with(execution))
    assert payload["trace"]["layers"][0]["tool_executions"][0][
        "composite_detail"
    ]


def _trial_result_with(execution) -> TrialResult:
    from capability_runtime import RoutingAction, RoutingDecision

    trial = Trial(
        id="t1",
        scenario_id="s1",
        trial_index=0,
        topology_version="v1",
        scenario_suite_version="1",
        router_config_id="fake",
    )
    layer = LayerExecution(
        layer="act",
        available_tools=("comp",),
        selected_tools=("comp",),
        routing_decision=RoutingDecision(
            action=RoutingAction.EXECUTE, selected_tools=("comp",)
        ),
        tool_executions=(execution,),
        started_at=datetime.now(),
        ended_at=datetime.now(),
    )
    trace = ExecutionTrace(
        trial_id="t1", scenario_id="s1", topology_version="v1", layers=(layer,)
    )
    return TrialResult(
        trial=trial,
        execution_status=TrialExecutionStatus.COMPLETED,
        route=None,
        trace=trace,
        evaluation=None,
        latency_ms=1.0,
        token_usage=TokenUsage(),
        cost=None,
    )


# ---- metering rollup (Step 3) -------------------------------------------------------


def test_inner_metering_rolls_up_to_outer_execution() -> None:
    from capability_runtime import tool

    GATE.clear()

    @tool(layer="only", produces=[Ping], cost_per_call=0.001)
    async def counted() -> Ping:
        count = (await GATE.get("n")) or 0
        await GATE.put("n", count + 1)
        return Ping(n=count + 1)

    node = build_composite_node(
        _spec(
            _single_layer_topology(counted),
            (("counted",),),
            stop_when=("ping",),
            iterations=1,
        )
    )
    execution = _execute(node)
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.access_counts == {
        "gate_store[read]": 1,
        "gate_store[write]": 1,
    }
    assert execution.metering_source.value == "measured"


# ---- flattener (Step 4) --------------------------------------------------------------


def _build_demo_world():
    sys.path.insert(0, str(_PROJECT / "examples" / "composite_refund"))
    import inner_tools
    import refund as sandbox
    from fixtures import SandboxFixtureManager

    import facts

    inner = inner_tools.build_inner_topology()
    spec = CompositeSpec(
        name="refund_handler",
        layer="act",
        topology=inner,
        route=(("verify",), ("holdover", "issue_refund")),
        stop_when=("refund_result",),
        max_iterations=3,
        consumes=(facts.Order, facts.PolicyDecision),
        produces=(facts.RefundResult,),
        capabilities=frozenset({"refund.handle"}),
        cost_per_call=0.02,
    )
    node = build_composite_node(spec)
    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "act")):
        layers.register(name, order)
    tools = ToolRegistry()
    for item in (sandbox.order_db, sandbox.policy_check, node):
        tools.register(item)
    return TopologyBuilder(layers, tools).build(), inner, spec, inner_tools


def test_flattener_feeds_inner_evidence_aggregator() -> None:
    import store
    from capability_runtime import EvidenceAggregator
    from fixtures import SandboxFixtureManager

    topology, inner, spec, inner_tools = _build_demo_world()

    class ResetFixture(SandboxFixtureManager):
        async def setup(self, scenario, trial):
            inner_tools.reset_inner_store()
            return await super().setup(scenario, trial)

    store.STORE.reset("eligible")
    suite = ScenarioSuite(
        name="flat",
        version="1.0",
        description="flat",
        scenarios=(
            Scenario(
                id="s1",
                query="q",
                category="refund",
                metadata={"fixture": "eligible"},
            ),
        ),
    )
    router = FakeRouter(
        layer_selections={
            "read": ["order_db"],
            "analyze": ["policy_check"],
            "action": ["refund_handler"],
        }
    )
    outcome = asyncio.run(
        SlowRegressionRunner(
            topology=topology,
            evaluator=_build_demo_world_evaluator(),
            fixture_manager=ResetFixture(),
            trials_per_scenario=2,
            topology_version="v8",
            router_config_id="fake",
            router=router,
        ).run(suite)
    )

    pseudo = flatten_composite_results(outcome.results, {"refund_handler": spec})
    # 2 trials x 2 iterations (double confirmation) = 4 inner pseudo trials
    assert len(pseudo) == 4
    inner_edges = [(edge.source, edge.target) for edge in inner.edges()]
    obs = build_observation_stats(pseudo, edges=inner_edges)
    evidence = EvidenceAggregator().build(
        report=obs, results=pseudo, edges=inner_edges
    )
    assert set(evidence.node_evidence) == {"verify", "holdover", "issue_refund"}
    # inner edges never entered the outer stats
    outer_obs = build_observation_stats(outcome.results, edges=inner_edges)
    assert "verify" not in outer_obs.node_stats


def _build_demo_world_evaluator():
    import refund as sandbox

    return sandbox.build_evaluator()


# ---- loader (Step 5) --------------------------------------------------------------------


def _inner_json(tmp_path: Path) -> Path:
    inner = tmp_path / "inner.json"
    inner.write_text(
        json.dumps(
            {
                "version": "1.0",
                "layers": [{"name": "only", "order": 0}],
                "tools": [
                    {
                        "name": "fetch",
                        "layer": "only",
                        "implementation": (
                            "tests.unit._binding_tools:fetch"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return inner


def test_loader_builds_composite_from_json(tmp_path) -> None:
    from capability_runtime import TopologyLoader, unbound_tool_names

    inner = _inner_json(tmp_path)
    doc = {
        "version": "1.0",
        "layers": [{"name": "act", "order": 0}],
        "tools": [
            {
                "name": "macro",
                "layer": "act",
                "kind": "composite",
                "inner": str(inner),
                "route": [{"layer": "only", "tools": ["fetch"]}],
                "stop_when": ["note"],
                "max_iterations": 2,
                "capabilities": ["demo.macro"],
                "cost_per_call": 0.01,
            }
        ],
    }
    topology = TopologyLoader().load_data(doc)
    assert "macro" in topology.nodes()
    assert unbound_tool_names(topology) == ()


def test_loader_rejects_bad_composite_entries(tmp_path) -> None:
    from capability_runtime import TopologyLoader

    inner = _inner_json(tmp_path)
    base = {
        "version": "1.0",
        "layers": [{"name": "act", "order": 0}],
        "tools": [
            {
                "name": "macro",
                "layer": "act",
                "kind": "composite",
                "inner": str(inner),
                "route": [{"layer": "only", "tools": ["fetch"]}],
                "stop_when": ["note"],
                "max_iterations": 1,
            }
        ],
    }

    bad_providers = json.loads(json.dumps(base))
    bad_providers["tools"][0]["providers"] = ["x"]
    with pytest.raises(TopologyBuildError, match="unknown fields"):
        TopologyLoader().load_data(bad_providers)

    unbound_inner = tmp_path / "unbound.json"
    unbound_inner.write_text(
        json.dumps(
            {
                "version": "1.0",
                "layers": [{"name": "only", "order": 0}],
                "tools": [{"name": "ghost", "layer": "only"}],
            }
        ),
        encoding="utf-8",
    )
    bad_inner = json.loads(json.dumps(base))
    bad_inner["tools"][0]["inner"] = str(unbound_inner)
    with pytest.raises(TopologyBuildError, match="executable implementation"):
        TopologyLoader().load_data(bad_inner)

    bad_budget = json.loads(json.dumps(base))
    bad_budget["tools"][0]["max_iterations"] = 0
    with pytest.raises(TopologyBuildError, match="positive integer"):
        TopologyLoader().load_data(bad_budget)


# ---- online compatibility (Step 6) ------------------------------------------------------


def test_online_catalog_and_runtime_serve_composite() -> None:
    import store
    from capability_runtime import (
        OnlineRequest,
        OnlineRuntime,
        OnlineStatus,
        build_catalog,
    )

    topology, _inner, spec, inner_tools = _build_demo_world()

    ranking = {
        "topology_version": "v9",
        "router_config_id": "fake",
        "profiles": [
            {
                "route_id": "r1",
                "canonical": (
                    "read:[order_db]\nanalyze:[policy_check]\n"
                    "act:[refund_handler]"
                ),
                "categories": ["refund"],
                "trial_count": 5,
                "business_success_rate": 1.0,
                "success_confidence_interval": [0.5, 1.0],
                "latency_median": 10.0,
                "cost_mean": 0.02,
                "quality_mean": 0.9,
            }
        ],
        "eligibility": [{"route_id": "r1", "status": "ranked"}],
        "pareto": {"frontier": ["r1"]},
        "tier_assignments": [{"route_id": "r1", "tiers": ["fast"]}],
    }
    catalog = build_catalog(topology, ranking, topology_version="v9")
    runtime = OnlineRuntime(catalog=catalog)
    inner_tools.reset_inner_store()
    store.STORE.reset("eligible")
    result = asyncio.run(
        runtime.serve(OnlineRequest(query="refund", category="refund"))
    )
    assert result.status is OnlineStatus.SERVED
    assert result.selected_route_id == "r1"
    # inner metering rolled up into the online result's trace
    assert result.trace is not None
    accesses: dict[str, int] = {}
    for layer in result.trace.layers:
        for execution in layer.tool_executions:
            for key, count in (execution.access_counts or {}).items():
                accesses[key] = accesses.get(key, 0) + count
    assert "composite_confirm[write]" in accesses
