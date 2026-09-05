from datetime import datetime

import pytest

from capability_runtime import (
    EdgeEvidence,
    EdgeObservationStats,
    EvaluationResult,
    EvidenceAggregator,
    EvidenceError,
    EvidenceReport,
    ExecutionTrace,
    LayerExecution,
    NodeEvidence,
    NodeObservationStats,
    ObservationReport,
    SelectionEvent,
    Trial,
    TrialExecutionStatus,
    TrialResult,
    TokenUsage,
    build_observation_stats,
)


def _layer(name: str, available, selected) -> LayerExecution:
    now = datetime.now()
    return LayerExecution(
        layer=name,
        available_tools=tuple(sorted(available)),
        selected_tools=tuple(sorted(selected)),
        routing_decision=None,
        tool_executions=(),
        started_at=now,
        ended_at=now,
    )


def _result(
    trial_id: str,
    scenario: str,
    layers: list[LayerExecution],
    success: bool,
) -> TrialResult:
    trial = Trial(
        id=trial_id, scenario_id=scenario, trial_index=0, topology_version="1.0",
        scenario_suite_version="1.0", router_config_id="cfg",
    )
    trace = ExecutionTrace(
        trial_id=trial_id, scenario_id=scenario, topology_version="1.0",
        layers=tuple(layers),
    )
    return TrialResult(
        trial=trial,
        execution_status=TrialExecutionStatus.COMPLETED,
        route=None,
        trace=trace,
        evaluation=EvaluationResult(
            success=success, quality_score=1.0 if success else 0.0, reason="ok"
        ),
        latency_ms=10.0,
        token_usage=TokenUsage(),
        cost=0.0,
    )


def _obs_report(node, edge) -> ObservationReport:
    return ObservationReport(
        scenario_count=2,
        trial_count=4,
        node_stats={n.tool: n for n in node},
        edge_stats={f"{e.source}->{e.target}": e for e in edge},
        route_stats={},
        selection_events=(),
        scenario_route_distribution={},
        unique_route_count=0,
    )


def test_maps_node_and_edge_fields() -> None:
    report = _obs_report(
        node=(NodeObservationStats(tool="a", opportunity_count=10, selected_count=4),),
        edge=(EdgeObservationStats(source="a", target="b", opportunity_count=8,
                                   observed_count=2, successful_trial_count=1,
                                   failed_trial_count=1),
              EdgeObservationStats(source="x", target="y", opportunity_count=0),
              ),
    )
    ev = EvidenceAggregator().build(
        report=report, results=(), edges=(("a", "b"), ("x", "y"))
    )

    assert isinstance(ev.node_evidence["a"], NodeEvidence)
    a = ev.node_evidence["a"]
    assert a.available_count == 10 and a.selected_count == 4
    assert a.success_trial_count == 0 and a.failed_trial_count == 0
    assert a.selection_rate == pytest.approx(0.4)

    eb = ev.edge_evidence["a->b"]
    assert isinstance(eb, EdgeEvidence)
    assert eb.opportunity_count == 8 and eb.observed_count == 2
    assert eb.successful_trial_count == 1 and eb.failed_trial_count == 1
    assert eb.usage_rate == pytest.approx(0.25)
    assert eb.has_evidence is True
    assert eb.zero_observed is False

    exy = ev.edge_evidence["x->y"]
    assert exy.opportunity_count == 0 and exy.observed_count == 0
    assert exy.has_evidence is False and exy.usage_rate == 0.0


def test_high_opportunity_zero_observed_kept_as_evidence() -> None:
    # Unused != Useless: high opportunity / zero observed is a strong pruning
    # signal but must NOT be silently dropped.
    report = _obs_report(
        node=(NodeObservationStats(tool="a", opportunity_count=100, selected_count=90),),
        edge=(EdgeObservationStats(source="a", target="b",
                                   opportunity_count=100, observed_count=0),),
    )
    ev = EvidenceAggregator().build(
        report=report, results=(), edges=(("a", "b"),)
    )
    eb = ev.edge_evidence["a->b"]
    assert eb.opportunity_count == 100
    assert eb.observed_count == 0
    assert eb.usage_rate == 0.0
    assert eb.has_evidence is True


def test_low_opportunity() -> None:
    report = _obs_report(
        node=(NodeObservationStats(tool="a", opportunity_count=2),),
        edge=(EdgeObservationStats(source="a", target="b",
                                   opportunity_count=2, observed_count=1),),
    )
    ev = EvidenceAggregator().build(
        report=report, results=(), edges=(("a", "b"),)
    )
    eb = ev.edge_evidence["a->b"]
    assert eb.opportunity_count == 2 and eb.observed_count == 1
    assert eb.usage_rate == pytest.approx(0.5)


def test_successful_route_support_reaggregated_from_trace() -> None:
    def run(tid: str, scenario: str, read: str, success: bool) -> TrialResult:
        return _result(
            tid,
            scenario,
            [_layer("read", ["r1", "r2"], [read]),
             _layer("analyze", ["a1", "a2"], ["a1"]),
             _layer("action", ["x1", "x2"], ["x1"])],
            success,
        )

    results = [
        run("t1", "s1", "r1", True),
        run("t2", "s2", "r1", True),
        run("t3", "s1", "r2", False),
        run("t4", "s1", "r1", False),
    ]
    report = build_observation_stats(
        results,
        edges=(("r1", "a1"), ("a1", "x1")),
    )
    ev = EvidenceAggregator().build(
        report=report,
        results=results,
        edges=(("r1", "a1"), ("a1", "x1")),
        protected_edges=frozenset({("a1", "x1")}),
    )

    a1x1 = ev.edge_evidence["a1->x1"]
    # walked by all 4 trials; only s1(T1) and s2(T2) were business-successful
    assert a1x1.observed_count == 4
    assert a1x1.successful_trial_count == 4  # all completed
    assert a1x1.successful_route_count == 2  # business success through the edge
    assert a1x1.scenario_count == 2          # {s1, s2}
    assert a1x1.protected is True

    r1a1 = ev.edge_evidence["r1->a1"]
    # r1 selected in T1(s1,success) / T2(s2,success) / T4(s1,fail); T3 uses r2
    assert r1a1.observed_count == 3
    assert r1a1.successful_route_count == 2  # T1, T2
    assert r1a1.scenario_count == 2          # {s1, s2}


def test_node_availability_selection_rates() -> None:
    report = _obs_report(
        node=(NodeObservationStats(tool="busy", opportunity_count=8, selected_count=7),
              NodeObservationStats(tool="idle", opportunity_count=8, selected_count=0),
              NodeObservationStats(tool="ghost", opportunity_count=0),
              ),
        edge=(),
    )
    ev = EvidenceAggregator().build(
        report=report, results=(), edges=(("busy", "idle"),)
    )
    assert ev.node_evidence["busy"].selection_rate == pytest.approx(0.875)
    assert ev.node_evidence["idle"].selection_rate == 0.0
    assert ev.node_evidence["ghost"].never_available is True
    assert ev.node_evidence["ghost"].selection_rate == 0.0


def test_empty_edges_rejected() -> None:
    report = _obs_report(node=(), edge=())
    with pytest.raises(EvidenceError):
        EvidenceAggregator().build(report=report, results=(), edges=())


def test_negative_opportunity_rejected() -> None:
    report = ObservationReport(
        scenario_count=0,
        trial_count=0,
        node_stats={"bad": NodeObservationStats(tool="bad", opportunity_count=-1)},
        edge_stats={},
        route_stats={},
        selection_events=(),
        scenario_route_distribution={},
        unique_route_count=0,
    )
    with pytest.raises(EvidenceError):
        EvidenceAggregator().build(report=report, results=(), edges=(("a", "b"),))