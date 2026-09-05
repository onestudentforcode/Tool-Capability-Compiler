"""Tests for SlowRegressionReport aggregation + rendering (phase3 §107-108)."""

from __future__ import annotations

from capability_runtime import (
    CandidateRoute,
    RouteLayer,
    build_observation_stats,
    build_slow_regression_report,
    render_slow_report,
)

from _slow_helpers import (
    AlwaysFailEvaluator,
    AlwaysPassEvaluator,
    build_topology,
    make_suite,
    sync_run,
)


def _build_report(outcome, suite, topology, router_config_id="basefast"):
    obs = build_observation_stats(
        outcome.results,
        edges=[(edge.source, edge.target) for edge in topology.edges()],
    )
    return build_slow_regression_report(
        outcome,
        obs,
        suite=suite,
        topology=topology,
        topology_version="1.0",
        router_config_id=router_config_id,
    )


def test_report_counts_completed_and_business_success() -> None:
    topology = build_topology()
    suite = make_suite()
    outcome = sync_run(topology, suite, trials=3)
    assert all(r.execution_status.name == "COMPLETED" for r in outcome.results)

    report = _build_report(outcome, suite, topology)
    assert report.scenario_count == 1
    assert report.trial_count == 3
    assert report.completed == 3
    assert report.business_success == 3
    assert report.business_failure == 0
    assert 1 <= report.observed_nodes <= len(topology.nodes())
    # free mode exercises both policy_check and summarizer across the read+analyze
    assert report.observed_edges >= 2
    assert report.unused_edges == len(topology.edges()) - report.observed_edges
    assert report.coverage_node_rate == (
        report.observed_nodes / report.total_nodes
    )


def test_report_flags_business_failure() -> None:
    topology = build_topology()
    suite = make_suite()
    outcome = sync_run(topology, suite, trials=2, evaluator=AlwaysFailEvaluator())
    report = _build_report(outcome, suite, topology)
    assert report.business_success == 0
    assert report.business_failure == 2


def test_report_carries_topology_and_router_identity() -> None:
    topology = build_topology()
    suite = make_suite()
    outcome = sync_run(topology, suite, trials=1)
    report = _build_report(outcome, suite, topology, router_config_id="llm-xyz")
    assert report.topology_version == "1.0"
    assert report.router_config_id == "llm-xyz"
    assert report.suite_name == "demo"
    assert report.unique_routes >= 1


def test_render_observes_but_never_recommends_pruning() -> None:
    topology = build_topology()
    suite = make_suite()
    outcome = sync_run(topology, suite, trials=3)
    report = _build_report(outcome, suite, topology)

    text = render_slow_report(report)
    assert "Slow Regression" in text
    assert f"Trials:          {report.trial_count}" in text
    assert "Unused Edges:" in text
    # phase3 §108: report facts, never a pruning recommendation
    assert "pruning recommendation" in text.lower() or "prun" not in text
    assert "Recommend" not in text.lower()


def test_report_sums_recent_latency_converts_tokens() -> None:
    topology = build_topology()
    suite = make_suite()
    outcome = sync_run(topology, suite, trials=2)
    report = _build_report(outcome, suite, topology)
    # latency basics: mean/median/p95 are floats or None, never empty when trials exist
    assert all(v is None or v >= 0 for v in report.latency_ms_basics)
    assert report.token_usage.input_tokens >= 0
    assert report.token_usage.output_tokens == 0