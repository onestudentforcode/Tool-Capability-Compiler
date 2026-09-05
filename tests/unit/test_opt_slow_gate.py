import pytest

from capability_runtime import (
    GateVerdict,
    PruningConfig,
    SlowGateResult,
    SlowValidationGate,
    ValidationGateError,
)
from capability_runtime.core.metrics import TokenUsage
from capability_runtime.regression.slow.report import SlowRegressionReport


def _report(
    *,
    completed=100,
    business_success=80,
    business_failure=20,
    routing_error=2,
    execution_failed=1,
    fixture_error=1,
    trial_count=110,
    scenario_count=5,
    unique_routes=3,
    observed_nodes=5,
    total_nodes=8,
    observed_edges=6,
    total_edges=12,
    unused_edges=6,
    evaluation_error=0,
    topology_version="v1",
):
    return SlowRegressionReport(
        suite_name="s",
        suite_version="1.0",
        topology_version=topology_version,
        router_config_id="basefast",
        scenario_count=scenario_count,
        trial_count=trial_count,
        completed=completed,
        routing_error=routing_error,
        execution_failed=execution_failed,
        evaluation_error=evaluation_error,
        fixture_error=fixture_error,
        business_success=business_success,
        business_failure=business_failure,
        unique_routes=unique_routes,
        observed_nodes=observed_nodes,
        total_nodes=total_nodes,
        observed_edges=observed_edges,
        total_edges=total_edges,
        latency_ms_basics=(10.0, 9.5, 15.0),
        token_usage=TokenUsage(input_tokens=100, output_tokens=50),
        unused_edges=unused_edges,
    )


def test_slow_gate_passes_when_metrics_unchanged() -> None:
    before = _report()
    after = _report()
    result = SlowValidationGate().evaluate(before=before, after=after)
    assert result.verdict is GateVerdict.PASS
    assert result.passed
    assert result.failures == ()


def test_slow_gate_rejects_success_rate_drop() -> None:
    before = _report(business_success=90, business_failure=10, completed=100)
    after = _report(business_success=70, business_failure=30, completed=100)
    # Default max_success_rate_drop = 0.01 (1%).
    # Drop is 20% -> REJECTED.
    result = SlowValidationGate().evaluate(before=before, after=after)
    assert result.verdict is GateVerdict.REJECTED
    failures = [f for f in result.failures if f.key == "success_rate"]
    assert len(failures) == 1


def test_slow_gate_allows_success_rate_drop_within_threshold() -> None:
    before = _report(business_success=90, business_failure=10, completed=100)
    after = _report(business_success=85, business_failure=15, completed=100)
    # 5% drop with 10% threshold -> PASS.
    config = PruningConfig(max_success_rate_drop=0.10)
    result = SlowValidationGate(config=config).evaluate(before=before, after=after)
    assert result.verdict is GateVerdict.PASS


def test_slow_gate_rejects_quality_drop() -> None:
    before = _report()
    after = _report()
    config = PruningConfig(max_quality_drop=0.01)
    result = SlowValidationGate(config=config).evaluate(
        before=before, after=after, before_quality=0.95, after_quality=0.90
    )
    assert result.verdict is GateVerdict.REJECTED
    failures = [f for f in result.failures if f.key == "quality"]
    assert len(failures) == 1


def test_slow_gate_skips_quality_when_not_provided() -> None:
    before = _report()
    after = _report()
    # Only after quality provided — quality check skipped because before is None.
    result = SlowValidationGate().evaluate(before=before, after=after, after_quality=0.5)
    assert result.verdict is GateVerdict.PASS


def test_slow_gate_rejects_error_rate_increase() -> None:
    before = _report(
        routing_error=1, execution_failed=0, fixture_error=0, trial_count=100
    )
    after = _report(
        routing_error=5, execution_failed=2, fixture_error=1, trial_count=100
    )
    result = SlowValidationGate().evaluate(before=before, after=after)
    assert result.verdict is GateVerdict.REJECTED
    failures = [f for f in result.failures if f.key == "error_rate"]
    assert len(failures) == 1


def test_slow_gate_allows_error_rate_increase_within_threshold() -> None:
    before = _report(
        routing_error=1, execution_failed=0, fixture_error=0, trial_count=100
    )
    after = _report(
        routing_error=2, execution_failed=1, fixture_error=0, trial_count=100
    )
    # Allow up to 5% increase (absolute).
    result = SlowValidationGate(max_error_rate_increase=0.05).evaluate(
        before=before, after=after
    )
    assert result.verdict is GateVerdict.PASS


def test_slow_gate_zero_completed_before_raises() -> None:
    before = _report(completed=0, business_success=0, business_failure=0)
    after = _report()
    with pytest.raises(ValidationGateError):
        SlowValidationGate().evaluate(before=before, after=after)


def test_slow_gate_invalid_input_raises() -> None:
    with pytest.raises(ValidationGateError):
        SlowValidationGate().evaluate(before="not-a-report", after=_report())
