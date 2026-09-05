import asyncio
from dataclasses import dataclass

from capability_runtime import (
    CandidateRoute,
    EvaluationResult,
    ExecutionTrace,
    LayerRegistry,
    ObservedRoute,
    RouteLayer,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    ToolRegistry,
    TopologyBuilder,
    TokenUsage,
    Trial,
    TrialExecutionStatus,
    TrialResult,
    build_observation_stats,
    compute_expansion_deltas,
    summarize,
    tool,
)


class PassEvaluator:
    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        return EvaluationResult(
            success=True, quality_score=1.0,
            reason="ok",
        )


@dataclass(frozen=True)
class Order:
    order_id: str


@dataclass(frozen=True)
class Decision:
    allowed: bool


@dataclass(frozen=True)
class Digest:
    text: str


@tool(layer="read", produces=[Order])
async def db() -> Order:
    return Order(order_id="123")


@tool(layer="analyze", consumes=[Order], produces=[Decision])
async def verdict(order: Order) -> Decision:
    return Decision(allowed=True)


@tool(layer="analyze", consumes=[Order], produces=[Digest])
async def explain(order: Order) -> Digest:
    return Digest(text="note")


def demo_topology():
    layers = LayerRegistry()
    layers.register("read", 0)
    layers.register("analyze", 1)
    tools = ToolRegistry()
    for node in (db, verdict, explain):
        tools.register(node)
    return TopologyBuilder(layers, tools).build()


def demo_suite() -> ScenarioSuite:
    return ScenarioSuite(
        name="demo", version="1.0", description="demo",
        scenarios=(Scenario(id="s1", query="check order", expected_capabilities=()),),
    )


def run_basefast(trials: int = 3):
    topology = demo_topology()
    seed = CandidateRoute(
        layers=(RouteLayer("read", ("db",)), RouteLayer("analyze", ("verdict",))),
        capabilities=frozenset(),
    )
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=PassEvaluator(),
        seeds={"s1": seed},
        trials_per_scenario=trials,
        max_tools_per_layer=2,
        topology_version="1.0",
    )
    return asyncio.run(runner.run(demo_suite())).results


def _trial(id_: str, scenario: str, latency: float, cost, quality, completed=True) -> TrialResult:
    trial = Trial(
        id=id_, scenario_id=scenario, trial_index=0, topology_version="1.0",
        scenario_suite_version="1.0", router_config_id="cfg",
    )
    route_ = ObservedRoute.from_layers((("read", ("db",)), ("analyze", ("verdict",))))
    return TrialResult(
        trial=trial,
        execution_status=(
            TrialExecutionStatus.COMPLETED if completed else TrialExecutionStatus.LAYER_ERROR
        ),
        route=route_,
        trace=ExecutionTrace(trial_id=id_, scenario_id=scenario, topology_version="1.0"),
        evaluation=EvaluationResult(success=completed, quality_score=quality),
        latency_ms=latency,
        token_usage=TokenUsage(),
        cost=cost,
    )


def test_summarize_basics() -> None:
    mean, median, p95 = summarize([10.0, 20.0, 30.0, 40.0, 50.0])
    assert mean == 30.0
    assert median == 30.0
    assert p95 == 50.0
    assert summarize([]) == (None, None, None)


def test_stats_nodes_and_selection_events() -> None:
    results = run_basefast(trials=4)  # 2 baseline + 2 variant
    stats = build_observation_stats(results, edges=())
    nodes = stats.node_stats
    # db always selected (both layers offer it)
    assert nodes["db"].selected_count == 4
    assert nodes["db"].opportunity_count == 4
    # verdict selected in all; explain only in variants (2 trials)
    assert nodes["verdict"].selected_count == 4
    assert nodes["explain"].selected_count == 2
    assert stats.scenario_route_distribution["s1"]
    assert stats.trial_count == 4
    assert stats.scenario_count == 1
    assert stats.unique_route_count == 2
    assert stats.selection_events


def test_stats_edges_use_topology() -> None:
    topology = demo_topology()
    edges = [(e.source, e.target) for e in topology.edges()]
    results = run_basefast(trials=4)
    stats = build_observation_stats(results, edges=edges)
    assert "db->verdict" in stats.edge_stats
    # db->verdict opportunity and observed
    db_v = stats.edge_stats["db->verdict"]
    assert db_v.observed_count == 4
    assert db_v.opportunity_count == 4


def test_route_stats_record_business_outcome() -> None:
    a = _trial("a", "s1", 10.0, 0.1, 0.8)
    b = _trial("b", "s1", 20.0, 0.2, 0.9)
    c = _trial("c", "s1", 30.0, 0.3, 0.5, completed=False)
    report = build_observation_stats((a, b, c), edges=())
    rid = a.route.route_id
    rs = report.route_stats[rid]
    assert rs.usage_count == 3
    assert rs.completed_count == 2
    assert rs.business_success_count == 2
    assert rs.business_failure_count == 1
    assert rs.latencies == (10.0, 20.0, 30.0)
    assert rs.quality_scores == (0.5, 0.8, 0.9)


def test_expansion_deltas_compare_baseline_vs_variant() -> None:
    results = run_basefast(trials=4)
    deltas = compute_expansion_deltas(results)
    assert deltas
    # the only difference is the analyze layer adding `explain`
    assert deltas[0].layer == "analyze"
    assert all(d.baseline_tools == ("verdict",) for d in deltas)
    assert all(d.variant_tools == ("explain", "verdict") for d in deltas)
    assert deltas[0].scenario_id == "s1"