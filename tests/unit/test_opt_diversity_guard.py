import pytest

from capability_runtime import (
    DiversityGuardResult,
    GateVerdict,
    RouteDiversityGuard,
    ValidationGateError,
)
from capability_runtime.regression.slow.stats import RouteObservationStats


def _stat(*, success=1, failure=0):
    return RouteObservationStats(
        route_id="dummy",
        usage_count=success + failure,
        completed_count=success + failure,
        business_success_count=success,
        business_failure_count=failure,
    )


def test_diversity_guard_passes_when_enough_families_remain() -> None:
    before = {"r1": _stat(success=5), "r2": _stat(success=3), "r3": _stat(success=2)}
    after = {"r1": _stat(success=5), "r2": _stat(success=3)}
    guard = RouteDiversityGuard(min_successful_route_families=2)
    result = guard.evaluate(before_routes=before, after_routes=after)
    assert result.verdict is GateVerdict.PASS
    assert result.passed
    assert result.before_families == 3
    assert result.after_families == 2
    assert result.lost_families == ("r3",)


def test_diversity_guard_rejects_when_too_few_remain() -> None:
    before = {"r1": _stat(success=5), "r2": _stat(success=3)}
    after = {"r1": _stat(success=5)}
    guard = RouteDiversityGuard(min_successful_route_families=2)
    result = guard.evaluate(before_routes=before, after_routes=after)
    assert result.verdict is GateVerdict.REJECTED
    assert not result.passed
    assert result.after_families == 1
    assert result.lost_families == ("r2",)


def test_diversity_guard_ignores_routes_without_success() -> None:
    before = {"r1": _stat(success=5), "r2": _stat(success=0, failure=3)}
    after = {"r1": _stat(success=5)}
    guard = RouteDiversityGuard(min_successful_route_families=1)
    result = guard.evaluate(before_routes=before, after_routes=after)
    assert result.verdict is GateVerdict.PASS
    assert result.before_families == 1
    assert result.after_families == 1
    assert result.lost_families == ()


def test_diversity_guard_invalid_min_rejected() -> None:
    with pytest.raises(ValidationGateError):
        RouteDiversityGuard(min_successful_route_families=0)


def test_diversity_guard_new_route_families_are_counted() -> None:
    before = {"r1": _stat(success=1)}
    after = {"r1": _stat(success=1), "r2": _stat(success=1)}
    guard = RouteDiversityGuard(min_successful_route_families=2)
    result = guard.evaluate(before_routes=before, after_routes=after)
    # After has 2 successful families, which meets the minimum — PASS.
    assert result.verdict is GateVerdict.PASS
    assert result.after_families == 2


def test_diversity_guard_result_is_diversity_guard_result() -> None:
    before = {"r1": _stat(success=1)}
    after = {"r1": _stat(success=1)}
    result = RouteDiversityGuard().evaluate(before_routes=before, after_routes=after)
    assert isinstance(result, DiversityGuardResult)
    assert result.min_families == 2
