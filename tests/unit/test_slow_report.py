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

# ---- output polish (P2/P3/P5) -------------------------------------------------


def _fake_result(trial_id, category, reason=None):
    """Minimal TrialResult double for the renderer's samples section."""
    from types import SimpleNamespace

    return SimpleNamespace(
        trial=SimpleNamespace(id=trial_id),
        failure_category=category,
        evaluation=SimpleNamespace(reason=reason) if reason else None,
        execution_status=SimpleNamespace(value="layer_error"),
    )


def test_render_slow_report_adds_glosses_and_samples() -> None:
    from capability_runtime import TokenUsage
    from capability_runtime.core.failure import TrialFailureCategory
    from capability_runtime.regression.slow.report import (
        SlowRegressionReport,
        render_slow_report,
    )

    report = SlowRegressionReport(
        suite_name="s", suite_version="1", topology_version="t",
        router_config_id="free", scenario_count=2, trial_count=2,
        completed=0, routing_error=0, execution_failed=2,
        evaluation_error=0, fixture_error=0,
        business_success=0, business_failure=2,
        latency_ms_basics=(None, None, None),
        token_usage=TokenUsage(),
        unique_routes=1,
        observed_nodes=1, total_nodes=2,
        observed_edges=0, total_edges=2, unused_edges=2,
        failure_by_category=(
            ("answer_error", 2), ("timeout", 1),
        ),
    )
    failures = [
        _fake_result("b#001", TrialFailureCategory.TIMEOUT),
        _fake_result("a#000", TrialFailureCategory.ANSWER_ERROR,
                     "missing artifacts: review_report, file_spec"),
        _fake_result("b#000", TrialFailureCategory.ANSWER_ERROR, "quality too low"),
    ]
    text = render_slow_report(report, failures=failures)

    assert "answer_error" in text and "# business evaluation failed" in text
    assert "timeout" in text and "# a tool exceeded the per-tool timeout" in text
    assert "Failure Samples (up to 2 per category):" in text
    # deterministic order: a#000 before b#000; two samples max per category
    assert "a#000 - missing artifacts: review_report, file_spec" in text
    assert "b#000 - quality too low" in text
    assert "b#001 - layer_error" in text  # no evaluation reason -> status
    samples = text.split("Failure Samples")[1]
    assert samples.index("a#000") < samples.index("b#000")


def test_render_slow_report_without_failures_unchanged() -> None:
    from capability_runtime import TokenUsage
    from capability_runtime.regression.slow.report import (
        SlowRegressionReport,
        render_slow_report,
    )

    report = SlowRegressionReport(
        suite_name="s", suite_version="1", topology_version="t",
        router_config_id="free", scenario_count=1, trial_count=1,
        completed=1, routing_error=0, execution_failed=0,
        evaluation_error=0, fixture_error=0,
        business_success=1, business_failure=0,
        latency_ms_basics=(1.0, 1.0, 1.0),
        token_usage=TokenUsage(),
        unique_routes=1,
        observed_nodes=1, total_nodes=1,
        observed_edges=0, total_edges=0, unused_edges=0,
        failure_by_category=(),
    )
    text = render_slow_report(report)
    assert "Failure Samples" not in text
    assert "(Phase 3 observes only" in text


# ---- output polish P7: metering section -------------------------------------


def test_report_aggregates_metering_costs_and_accesses() -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    topology = build_topology()
    suite = make_suite()
    outcome = sync_run(topology, suite, trials=2)
    # Overlay metering evidence onto real trial results (the office domain
    # carries costs; the unit helper topology does not).
    patched = [
        replace(
            result,
            tool_cost=0.5,
            routing_cost=0.25 if index == 0 else None,
            evaluation_cost=None,
            access_counts={"docs[read]": 2, "fs[read]": 1},
        )
        for index, result in enumerate(outcome.results)
    ]
    outcome = SimpleNamespace(results=patched)
    report = _build_report(outcome, suite, topology)

    assert report.cost_tool == 1.0          # 0.5 per trial, both trials
    assert report.cost_routing == 0.25      # None contributes nothing
    assert report.cost_evaluation is None   # all-None stays None
    assert report.access_total == 6         # 3 per trial, both trials
    assert report.access_resources == 2

    text = render_slow_report(report)
    assert "Metering:" in text
    assert "Tool Cost:           1.0000" in text
    assert "Routing Cost:        0.2500" in text
    assert "Evaluation Cost:     -" in text
    assert "Resource Accesses:   6 (2 resources)" in text


def test_report_unmetered_run_renders_dashes() -> None:
    topology = build_topology()
    suite = make_suite()
    outcome = sync_run(topology, suite, trials=1)
    report = _build_report(outcome, suite, topology)

    assert report.cost_tool is None
    assert report.access_total is None
    text = render_slow_report(report)
    assert "Metering:" in text
    assert "Tool Cost:           -" in text
    assert "Resource Accesses:   -" in text
    # token counters always render (they default to zero, not absence)
    assert "Tokens (in / out):" in text
