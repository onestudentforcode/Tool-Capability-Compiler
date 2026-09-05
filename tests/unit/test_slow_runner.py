from dataclasses import dataclass
import asyncio

import pytest

from capability_runtime import (
    CandidateRoute,
    EvaluationResult,
    ExecutionError,
    LayerRegistry,
    RouteLayer,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    ToolRegistry,
    TopologyBuilder,
    TrialExecutionStatus,
    build_expansion_plan,
    tool,
)


class AlwaysPassEvaluator:
    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        return EvaluationResult(success=True, quality_score=1.0,
                                reason="ok")


@dataclass(frozen=True)
class Order:
    order_id: str
    refund_allowed: bool


@dataclass(frozen=True)
class PolicyDecision:
    decision: str


@dataclass(frozen=True)
class Digest:
    text: str


@tool(layer="read", produces=[Order])
async def db() -> Order:
    return Order(order_id="123", refund_allowed=True)


@tool(layer="analyze", consumes=[Order], produces=[PolicyDecision])
async def policy_check(order: Order) -> PolicyDecision:
    return PolicyDecision(decision="refund")


@tool(layer="analyze", consumes=[Order], produces=[Digest])
async def summarizer(order: Order) -> Digest:
    return Digest(text="digest:" + order.order_id)


def make_demo_topology():
    layers = LayerRegistry()
    layers.register("read", 0)
    layers.register("analyze", 1)
    tools = ToolRegistry()
    for node in (db, policy_check, summarizer):
        tools.register(node)
    return TopologyBuilder(layers, tools).build()


def make_suite() -> ScenarioSuite:
    return ScenarioSuite(
        name="demo",
        version="1.0",
        description="demo",
        scenarios=(
            Scenario(id="s1", query="expense order", expected_capabilities=()),
        ),
    )


# ---- ExpansionPlan builder ----


def test_plan_baseline_is_seed_intersect_available() -> None:
    plan = build_expansion_plan(
        layer="analyze",
        available_tools=("a", "b", "c"),
        seed_tools=("a", "x"),
        max_tools_per_layer=3,
    )
    assert plan.baseline_tools == ("a",)
    assert plan.candidate_sets == (("a",), ("a", "b"), ("a", "c"))
    assert plan.variants == 2


def test_plan_respects_max_tools_cap() -> None:
    plan = build_expansion_plan(
        layer="analyze",
        available_tools=("a", "b", "c", "d"),
        seed_tools=("a",),
        max_tools_per_layer=2,
    )
    assert all(len(candidate) <= 2 for candidate in plan.candidate_sets)
    assert plan.candidate_sets == (("a",), ("a", "b"), ("a", "c"), ("a", "d"))


def test_plan_no_seed_offers_singletons() -> None:
    plan = build_expansion_plan(
        layer="read", available_tools=("d1", "d2"), seed_tools=(),
        max_tools_per_layer=4,
    )
    assert plan.baseline_tools == ()
    assert plan.candidate_sets == ((), ("d1",), ("d2",))


def test_plan_does_not_drop_baseline_tools() -> None:
    plan = build_expansion_plan(
        layer="analyze",
        available_tools=("a", "b"),
        seed_tools=("a",),
        max_tools_per_layer=1,
    )
    assert plan.candidate_sets == (("a",),)


def test_plan_rejects_bad_cap() -> None:
    with pytest.raises(ExecutionError):
        build_expansion_plan(
            layer="x", available_tools=(), seed_tools=(), max_tools_per_layer=0,
        )


# ---- SlowRegressionRunner ----


def test_runner_free_mode_flags_seed_missing() -> None:
    topology = make_demo_topology()
    suite = make_suite()
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=AlwaysPassEvaluator(),
        trials_per_scenario=3,
        max_tools_per_layer=2,
        topology_version="1.0",
    )
    outcome = asyncio.run(runner.run(suite))
    assert len(outcome.results) == 3
    assert outcome.seed_missing_scenarios == ("s1",)
    for result in outcome.results:
        assert result.execution_status is TrialExecutionStatus.COMPLETED
        assert result.trial.scenario_id == "s1"
        assert result.evaluation is not None and result.evaluation.success


def test_runner_basefast_emits_baseline_and_variants() -> None:
    topology = make_demo_topology()
    suite = make_suite()
    seed = CandidateRoute(
        layers=(
            RouteLayer("read", ("db",)),
            RouteLayer("analyze", ("policy_check",)),
        ),
        capabilities=frozenset(),
    )
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=AlwaysPassEvaluator(),
        seeds={"s1": seed},
        trials_per_scenario=3,
        max_tools_per_layer=2,
        topology_version="1.0",
    )
    outcome = asyncio.run(runner.run(suite))
    assert outcome.seed_missing_scenarios == ()
    assert len(outcome.results) == 3
    routes = [result.route for result in outcome.results if result.route]
    # trial0 = baseline (policy_check only); trial1 = +summarizer variant
    assert len(set(routes)) >= 2
    assert {result.execution_status for result in outcome.results} == {
        TrialExecutionStatus.COMPLETED
    }


def test_runner_records_trial_ids_and_versions() -> None:
    topology = make_demo_topology()
    suite = make_suite()
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=AlwaysPassEvaluator(),
        trials_per_scenario=2,
        topology_version="9.0",
    )
    outcome = asyncio.run(runner.run(suite))
    ids = [t.trial.id for t in outcome.results]
    assert ids == ["s1#000", "s1#001"]
    assert all(t.trial.topology_version == "9.0" for t in outcome.results)
    # scenario_suite_version is taken from the suite itself
    assert all(t.trial.scenario_suite_version == "1.0" for t in outcome.results)