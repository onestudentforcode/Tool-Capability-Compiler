from __future__ import annotations

import asyncio
import json

import pytest

from capability_runtime import (
    Baseline,
    BaselineLoadError,
    BaselineSaveError,
    BaselineStore,
    CoverageReport,
    CoverageStatus,
    FastRegressionResult,
    FastRegressionRunner,
    FailureReason,
    LayerRegistry,
    RegressionDiff,
    Scenario,
    ScenarioSuite,
    StatusChange,
    ToolRegistry,
    TopologyBuilder,
    compute_diff,
    tool,
)


@tool(
    layer="read",
    workers=["policy"],
    capabilities={"order.read"},
)
async def db():
    raise AssertionError("Fast Regression must not invoke tools")


@tool(
    layer="analyze",
    providers=["db"],
    capabilities={"refund.policy.check"},
)
async def policy():
    raise AssertionError("Fast Regression must not invoke tools")


def build_topology():
    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze")):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in (db, policy):
        tools.register(node)
    return TopologyBuilder(layers, tools).build()


def run_suite(expected_by_id):
    scenarios = tuple(
        Scenario(
            id=scenario_id,
            query=query,
            category="refund",
            expected_capabilities=capabilities,
        )
        for scenario_id, (query, capabilities) in expected_by_id.items()
    )
    suite = ScenarioSuite(name="customer-service", version="v1", scenarios=scenarios)
    return asyncio.run(
        FastRegressionRunner().run(suite, build_topology(), topology_version="v1")
    )


def make_report(statuses, *, reason=None, version="v1"):
    results = tuple(
        FastRegressionResult(
            scenario_id=scenario_id,
            category=None,
            status=status,
            reason=reason,
            required_capabilities=(),
            covered_capabilities=(),
            missing_capabilities=(),
            candidate_routes=(),
            confidence=1.0,
            reason_detail="",
        )
        for scenario_id, status in sorted(statuses.items())
    )
    counts = {CoverageStatus.COVERED: 0, CoverageStatus.UNCERTAIN: 0, CoverageStatus.UNCOVERED: 0}
    for result in results:
        counts[result.status] += 1
    return CoverageReport(
        suite_name="s",
        suite_version="v1",
        topology_version=version,
        total=len(results),
        covered=counts[CoverageStatus.COVERED],
        uncertain=counts[CoverageStatus.UNCERTAIN],
        uncovered=counts[CoverageStatus.UNCOVERED],
        results=results,
        categories=(),
        missing_capabilities=(),
        topology_gaps=(),
    )


def test_baseline_round_trip_preserves_statuses(tmp_path) -> None:
    report = run_suite(
        {
            "a_ok": ("query a", ("order.read",)),
            "b_ok": ("query b", ("order.read", "refund.policy.check")),
            "c_missing": ("query c", ("invoice.send",)),
        }
    )
    baseline = Baseline.from_report(report)
    assert baseline.coverage_rate == pytest.approx(2 / 3)
    assert baseline.status_of("c_missing") == CoverageStatus.UNCOVERED

    path = tmp_path / "baseline.json"
    store = BaselineStore()
    store.save(baseline, path)
    loaded = store.load(path)
    assert loaded == baseline
    # scenario ordering is normalized ascending for determinism
    assert [item.scenario_id for item in loaded.results] == [
        "a_ok",
        "b_ok",
        "c_missing",
    ]


def test_baseline_save_and_load_are_stable_json(tmp_path) -> None:
    report = run_suite({"a_ok": ("query a", ("order.read",))})
    store = BaselineStore()
    store.save(Baseline.from_report(report), tmp_path / "b.json")
    raw = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))
    assert raw["kind"] == "fast-regression-baseline"
    assert raw["coverage_rate"] == 1.0
    assert raw["results"] == [{"scenario_id": "a_ok", "status": "covered"}]


def test_load_rejects_unknown_format(tmp_path) -> None:
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"kind": "other", "stuff": 1}), encoding="utf-8")
    with pytest.raises(BaselineLoadError):
        BaselineStore().load(path)


def test_load_rejects_invalid_json(tmp_path) -> None:
    path = tmp_path / "b.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(BaselineLoadError):
        BaselineStore().load(path)


def test_load_rejects_duplicate_scenario_id(tmp_path) -> None:
    payload = {
        "kind": "fast-regression-baseline",
        "suite_name": "s",
        "suite_version": "v1",
        "topology_version": "v1",
        "total": 2,
        "covered": 2,
        "uncertain": 0,
        "uncovered": 0,
        "results": [
            {"scenario_id": "dup", "status": "covered"},
            {"scenario_id": "dup", "status": "uncovered"},
        ],
    }
    path = tmp_path / "b.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BaselineLoadError):
        BaselineStore().load(path)


def test_load_rejects_invalid_status(tmp_path) -> None:
    payload = {
        "kind": "fast-regression-baseline",
        "suite_name": "s",
        "suite_version": "v1",
        "topology_version": "v1",
        "total": 1,
        "covered": 0,
        "uncertain": 0,
        "uncovered": 1,
        "results": [{"scenario_id": "x", "status": "maybe"}],
    }
    path = tmp_path / "b.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BaselineLoadError):
        BaselineStore().load(path)


def test_load_rejects_missing_non_empty_string(tmp_path) -> None:
    payload = {
        "kind": "fast-regression-baseline",
        "suite_name": "s",
        "suite_version": "v1",
        "topology_version": "  ",
        "total": 1,
        "covered": 0,
        "uncertain": 0,
        "uncovered": 1,
        "results": [],
    }
    path = tmp_path / "b.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BaselineLoadError):
        BaselineStore().load(path)


def test_save_reports_unwritable_path(tmp_path) -> None:
    baseline = Baseline.from_report(run_suite({"a": ("q", ("order.read",))}))
    store = BaselineStore()
    with pytest.raises(BaselineSaveError):
        store.save(baseline, tmp_path / "no" / "such" / "dir" / "b.json")


def test_diff_classifies_regressions_and_gains() -> None:
    baseline = Baseline.from_report(
        make_report(
            {
                "covered_before": CoverageStatus.COVERED,
                "regressed": CoverageStatus.COVERED,
                "now_covered": CoverageStatus.UNCOVERED,
                "still_missing": CoverageStatus.UNCOVERED,
            }
        )
    )
    current = make_report(
        {
            "covered_before": CoverageStatus.COVERED,
            "regressed": CoverageStatus.UNCOVERED,
            "now_covered": CoverageStatus.COVERED,
            "still_missing": CoverageStatus.UNCOVERED,
        }
    )
    diff = compute_diff(baseline, current)

    assert diff.baseline_topology_version == "v1"
    assert diff.current_topology_version == "v1"
    assert diff.newly_covered == ("now_covered",)
    assert diff.newly_uncovered == ("regressed",)
    assert diff.still_uncovered == ("still_missing",)
    assert diff.still_covered == ("covered_before",)
    assert diff.gained_cover == 1
    assert diff.lost_cover == 1
    assert {change.scenario_id for change in diff.status_changed} == {
        "regressed",
        "now_covered",
    }


def test_diff_status_changed_contains_before_after_and_reason() -> None:
    baseline_report = make_report(
        {
            "regressed": CoverageStatus.COVERED,
            "recovered": CoverageStatus.UNCOVERED,
        }
    )
    current_report = make_report(
        {
            "regressed": CoverageStatus.UNCOVERED,
            "recovered": CoverageStatus.COVERED,
        },
        reason=FailureReason.MISSING_CAPABILITY,
    )
    diff = compute_diff(Baseline.from_report(baseline_report), current_report)
    changes: dict[str, StatusChange] = {
        change.scenario_id: change for change in diff.status_changed
    }
    assert changes["regressed"].before == CoverageStatus.COVERED
    assert changes["regressed"].after == CoverageStatus.UNCOVERED
    assert "missing_capability" in changes["regressed"].reason_after
    assert changes["recovered"].before == CoverageStatus.UNCOVERED
    assert changes["recovered"].after == CoverageStatus.COVERED


def test_diff_empty_when_identical() -> None:
    statuses = {
        "a": CoverageStatus.COVERED,
        "b": CoverageStatus.UNCOVERED,
    }
    report = make_report(statuses)
    diff = compute_diff(Baseline.from_report(report), report)
    assert diff.newly_covered == ()
    assert diff.newly_uncovered == ()
    assert diff.status_changed == ()


def test_diff_ignores_scenario_missing_from_current() -> None:
    baseline_report = make_report({"gone": CoverageStatus.COVERED})
    current_report = make_report({})
    diff = compute_diff(Baseline.from_report(baseline_report), current_report)
    assert diff.status_changed == ()
    assert diff.newly_covered == ()
    assert diff.newly_uncovered == ()