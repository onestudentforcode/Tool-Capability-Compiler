import pytest

from capability_runtime import (
    CategoryCoverage,
    CoverageReport,
    CoverageStatus,
    FastGateResult,
    FastRegressionResult,
    FastValidationGate,
    GateFailure,
    GateVerdict,
    PruningConfig,
    ScenarioCounterfactual,
    TopologyPatch,
    ValidationGateError,
    summarize_by_category,
)
from capability_runtime.optimization.counterfactual import CounterfactualResult


def _fast_result(scenario_id, category, status):
    return FastRegressionResult(
        scenario_id=scenario_id,
        category=category,
        status=status,
        reason=None,
        required_capabilities=(),
        covered_capabilities=(),
        missing_capabilities=(),
        candidate_routes=(),
        confidence=1.0,
        reason_detail="",
    )


def _report(covered, uncovered, categories, results, *, version="v1"):
    return CoverageReport(
        suite_name="s",
        suite_version="1.0",
        topology_version=version,
        total=covered + uncovered,
        covered=covered,
        uncertain=0,
        uncovered=uncovered,
        results=results,
        categories=categories,
        missing_capabilities=(),
        topology_gaps=(),
    )


def _counterfactual(before, after, checks, *, base_version="v1"):
    return CounterfactualResult(
        base_version=base_version,
        patch=TopologyPatch(),
        before=before,
        after=after,
        checks=checks,
    )


def test_gate_passes_when_coverage_unchanged() -> None:
    results = tuple(
        _fast_result(f"s{i}", "a", CoverageStatus.COVERED) for i in range(3)
    )
    categories = (CategoryCoverage(category="a", total=3, covered=3, uncertain=0, uncovered=0),)
    report = _report(3, 0, categories, results)
    checks = tuple(
        ScenarioCounterfactual(
            scenario_id=f"s{i}",
            before_status=CoverageStatus.COVERED,
            after_status=CoverageStatus.COVERED,
            before_capabilities=(),
            after_capabilities=(),
        )
        for i in range(3)
    )
    cf = _counterfactual(report, report, checks)
    gate = FastValidationGate()
    result = gate.evaluate(cf)
    assert result.verdict is GateVerdict.PASS
    assert result.passed
    assert result.failures == ()


def test_gate_rejects_global_coverage_drop() -> None:
    before_results = tuple(
        _fast_result(f"s{i}", "a", CoverageStatus.COVERED) for i in range(10)
    )
    after_results = tuple(
        _fast_result(f"s{i}", "a", CoverageStatus.COVERED if i < 8 else CoverageStatus.UNCOVERED)
        for i in range(10)
    )
    before_cat = (CategoryCoverage(category="a", total=10, covered=10, uncertain=0, uncovered=0),)
    after_cat = (CategoryCoverage(category="a", total=10, covered=8, uncertain=0, uncovered=2),)
    before = _report(10, 0, before_cat, before_results)
    after = _report(8, 2, after_cat, after_results)
    checks = tuple(
        ScenarioCounterfactual(
            scenario_id=f"s{i}",
            before_status=CoverageStatus.COVERED,
            after_status=CoverageStatus.COVERED if i < 8 else CoverageStatus.UNCOVERED,
            before_capabilities=(),
            after_capabilities=(),
        )
        for i in range(10)
    )
    cf = _counterfactual(before, after, checks)
    gate = FastValidationGate()  # default: 0% drop allowed
    result = gate.evaluate(cf)
    assert result.verdict is GateVerdict.REJECTED
    assert not result.passed
    assert any(f.domain == "global" for f in result.failures)
    assert any(f.domain == "category" for f in result.failures)


def test_gate_allows_drop_within_configured_threshold() -> None:
    before_results = tuple(
        _fast_result(f"s{i}", "a", CoverageStatus.COVERED) for i in range(10)
    )
    after_results = tuple(
        _fast_result(f"s{i}", "a", CoverageStatus.COVERED if i < 9 else CoverageStatus.UNCOVERED)
        for i in range(10)
    )
    before_cat = (CategoryCoverage(category="a", total=10, covered=10, uncertain=0, uncovered=0),)
    after_cat = (CategoryCoverage(category="a", total=10, covered=9, uncertain=0, uncovered=1),)
    before = _report(10, 0, before_cat, before_results)
    after = _report(9, 1, after_cat, after_results)
    checks = tuple(
        ScenarioCounterfactual(
            scenario_id=f"s{i}",
            before_status=CoverageStatus.COVERED,
            after_status=CoverageStatus.COVERED if i < 9 else CoverageStatus.UNCOVERED,
            before_capabilities=(),
            after_capabilities=(),
        )
        for i in range(10)
    )
    cf = _counterfactual(before, after, checks)
    # Allow up to 20% drop — one of ten (10%) passes.
    gate = FastValidationGate(config=PruningConfig(max_fast_coverage_drop=0.20))
    result = gate.evaluate(cf)
    assert result.verdict is GateVerdict.PASS


def test_gate_rejects_sentinel_regression_even_when_global_passes() -> None:
    # Global stays at 10/10 because s9 is NOT a sentinel and drops,
    # but sentinel s0 regresses -> REJECTED.
    before_results = tuple(
        _fast_result(f"s{i}", "a", CoverageStatus.COVERED) for i in range(10)
    )
    after_results = tuple(
        _fast_result(f"s{i}", "a", CoverageStatus.COVERED if i != 0 else CoverageStatus.UNCERTAIN)
        for i in range(10)
    )
    before_cat = (CategoryCoverage(category="a", total=10, covered=10, uncertain=0, uncovered=0),)
    after_cat = (CategoryCoverage(category="a", total=10, covered=9, uncertain=1, uncovered=0),)
    before = _report(10, 0, before_cat, before_results)
    after = _report(9, 0, after_cat, after_results)
    # 9 covered still passes global+category at default 0%? No — default 0% means
    # any drop rejects, so we need a threshold that permits the 10% drop.
    checks = tuple(
        ScenarioCounterfactual(
            scenario_id=f"s{i}",
            before_status=CoverageStatus.COVERED,
            after_status=CoverageStatus.COVERED if i != 0 else CoverageStatus.UNCERTAIN,
            before_capabilities=(),
            after_capabilities=(),
        )
        for i in range(10)
    )
    cf = _counterfactual(before, after, checks)
    config = PruningConfig(max_fast_coverage_drop=0.20)
    gate = FastValidationGate(config=config, sentinel_scenarios=["s0"])
    result = gate.evaluate(cf)
    assert result.verdict is GateVerdict.REJECTED
    sentinel_failures = [f for f in result.failures if f.domain == "sentinel"]
    assert len(sentinel_failures) == 1
    assert sentinel_failures[0].key == "s0"
    assert "regressed" in sentinel_failures[0].reason


def test_gate_empty_suite_rejected() -> None:
    empty = _report(0, 0, (), ())
    cf = _counterfactual(empty, empty, ())
    with pytest.raises(ValidationGateError):
        FastValidationGate().evaluate(cf)


def test_gate_failure_requires_valid_fields() -> None:
    with pytest.raises(ValidationGateError):
        GateFailure(domain="", key="x", reason="r", before=1, after=0)


def test_summarize_by_category_computes_deltas() -> None:
    before_cat = (
        CategoryCoverage(category="a", total=10, covered=10, uncertain=0, uncovered=0),
        CategoryCoverage(category="b", total=5, covered=3, uncertain=0, uncovered=2),
    )
    after_cat = (
        CategoryCoverage(category="a", total=10, covered=9, uncertain=0, uncovered=1),
        CategoryCoverage(category="b", total=5, covered=3, uncertain=0, uncovered=2),
    )
    before = _report(13, 3, before_cat, ())
    after = _report(12, 4, after_cat, ())
    checks = ()
    cf = _counterfactual(before, after, checks)
    summary = summarize_by_category(cf)
    assert summary["a"]["delta"] == -1
    assert summary["b"]["delta"] == 0
    assert summary["a"]["before"] == 10 and summary["a"]["after"] == 9
