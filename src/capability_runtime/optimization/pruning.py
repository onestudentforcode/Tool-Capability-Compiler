from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from ..core.errors import ValidationGateError
from .counterfactual import CounterfactualResult
from .candidate import PruningConfig
from ..regression.coverage import CoverageStatus


class GateVerdict(str, Enum):
    PASS = "pass"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class GateFailure:
    domain: str
    key: str
    reason: str
    before: int
    after: int

    def __post_init__(self) -> None:
        if not self.domain.strip() or not self.key.strip():
            raise ValidationGateError("GateFailure requires non-empty domain and key")


@dataclass(frozen=True, slots=True)
class FastGateResult:
    """Verdict + evidence from a Fast Validation Gate pass.

    The gate is a decision point, but its decision can be overridden by a
    human or an upstream policy (Phase 4 never auto-accepts on its own).
    """

    verdict: GateVerdict
    failures: tuple[GateFailure, ...] = ()

    @property
    def passed(self) -> bool:
        return self.verdict is GateVerdict.PASS


class FastValidationGate:
    """Three-domain fast gate: global, per-category, and sentinel scenarios.

    - **global**: covered count must not drop below `max_fast_coverage_drop`
      (default 0 → any covered loss rejects, §125).
    - **category**: every category must also hold (a global PASS with a single
      category collapse is still dangerous).
    - **sentinel**: any sentinel scenario whose coverage worsens rejects the
      candidate immediately (§126, higher bar than category).
    """

    def __init__(
        self,
        config: PruningConfig | None = None,
        sentinel_scenarios: Iterable[str] = (),
    ) -> None:
        self._config = config or PruningConfig()
        self._sentinels = frozenset(sentinel_scenarios)

    def evaluate(self, counterfactual: CounterfactualResult) -> FastGateResult:
        failures: list[GateFailure] = []
        before = counterfactual.before
        after = counterfactual.after

        # 1. global covered drop
        max_drop = self._config.max_fast_coverage_drop
        if before.total == 0:
            raise ValidationGateError("Fast gate cannot evaluate an empty suite")
        drop_count = before.covered - after.covered
        if drop_count > 0:
            drop_ratio = drop_count / before.total
            if drop_ratio > max_drop:
                failures.append(
                    GateFailure(
                        domain="global",
                        key="coverage",
                        reason=(
                            f"global covered dropped by {drop_count} "
                            f"({drop_ratio:.2%}), exceeding {max_drop:.2%}"
                        ),
                        before=before.covered,
                        after=after.covered,
                    )
                )

        # 2. per-category drop
        before_by_cat = {c.category: c for c in before.categories}
        after_by_cat = {c.category: c for c in after.categories}
        for category in sorted(before_by_cat):
            before_cat = before_by_cat[category]
            after_cat = after_by_cat.get(category)
            if after_cat is None:
                failures.append(
                    GateFailure(
                        domain="category",
                        key=category,
                        reason="category disappeared from after-report",
                        before=before_cat.covered,
                        after=0,
                    )
                )
                continue
            drop = before_cat.covered - after_cat.covered
            if drop <= 0:
                continue
            drop_ratio = drop / before_cat.covered if before_cat.covered else 0.0
            if drop_ratio > max_drop:
                failures.append(
                    GateFailure(
                        domain="category",
                        key=category,
                        reason=(
                            f"category covered dropped by {drop} "
                            f"({drop_ratio:.2%}), exceeding {max_drop:.2%}"
                        ),
                        before=before_cat.covered,
                        after=after_cat.covered,
                    )
                )

        # 3. sentinel scenarios: any single regression rejects the batch
        for check in counterfactual.checks:
            if check.scenario_id not in self._sentinels:
                continue
            if check.regressed:
                failures.append(
                    GateFailure(
                        domain="sentinel",
                        key=check.scenario_id,
                        reason=(
                            f"sentinel scenario {check.scenario_id} regressed "
                            f"{check.before_status.value} -> {check.after_status.value}"
                        ),
                        before=int(check.before_status == CoverageStatus.COVERED),
                        after=int(check.after_status == CoverageStatus.COVERED),
                    )
                )

        verdict = GateVerdict.PASS if not failures else GateVerdict.REJECTED
        return FastGateResult(verdict=verdict, failures=tuple(failures))


def summarize_by_category(
    counterfactual: CounterfactualResult,
) -> dict[str, dict[str, int]]:
    """Per-category before/after covered counts — helper for report/rendering."""
    before = {c.category: c.covered for c in counterfactual.before.categories}
    after = {c.category: c.covered for c in counterfactual.after.categories}
    categories = sorted(set(before) | set(after))
    out: dict[str, dict[str, int]] = {}
    for category in categories:
        out[category] = {
            "before": before.get(category, 0),
            "after": after.get(category, 0),
            "delta": after.get(category, 0) - before.get(category, 0),
        }
    return out


@dataclass(frozen=True, slots=True)
class SlowGateResult:
    """Verdict from Slow Validation Gate (Step 9): business + quality + errors."""

    verdict: GateVerdict
    failures: tuple[GateFailure, ...] = ()

    @property
    def passed(self) -> bool:
        return self.verdict is GateVerdict.PASS


class SlowValidationGate:
    """Validate that a pruned topology does not regress on slow metrics.

    Three checks, all gated by ``PruningConfig``:
    - **success_rate**: business success / completed must not drop more than
      ``max_success_rate_drop``.
    - **quality**: when before/after quality scores are provided, mean quality
      must not drop more than ``max_quality_drop``.
    - **error_rate**: routing + execution + fixture errors must not grow more
      than the allowed relative increase (default: none allowed).

    If before has zero completed trials the gate raises ``ValidationGateError``
    to avoid division-by-zero nonsense.
    """

    def __init__(
        self,
        config: PruningConfig | None = None,
        *,
        max_error_rate_increase: float = 0.0,
    ) -> None:
        if isinstance(max_error_rate_increase, bool) or not (
            0.0 <= max_error_rate_increase <= 1.0
        ):
            raise ValidationGateError(
                "max_error_rate_increase must be a float in [0, 1]"
            )
        self._config = config or PruningConfig()
        self._max_error_increase = max_error_rate_increase

    def evaluate(
        self,
        *,
        before,
        after,
        before_quality: float | None = None,
        after_quality: float | None = None,
    ) -> SlowGateResult:
        from ..regression.slow.report import SlowRegressionReport

        if not isinstance(before, SlowRegressionReport) or not isinstance(
            after, SlowRegressionReport
        ):
            raise ValidationGateError(
                "SlowValidationGate requires SlowRegressionReport inputs"
            )
        if before.completed == 0:
            raise ValidationGateError(
                "Slow gate cannot evaluate: before-report has zero completed trials"
            )

        failures: list[GateFailure] = []

        # 1. business success rate
        before_success_rate = before.business_success / before.completed
        after_success_rate = (
            after.business_success / after.completed if after.completed > 0 else 0.0
        )
        success_drop = before_success_rate - after_success_rate
        if success_drop > self._config.max_success_rate_drop + 1e-9:
            failures.append(
                GateFailure(
                    domain="slow",
                    key="success_rate",
                    reason=(
                        f"business success rate dropped by {success_drop:.2%}, "
                        f"exceeding {self._config.max_success_rate_drop:.2%}"
                    ),
                    before=before.business_success,
                    after=after.business_success,
                )
            )

        # 2. quality (optional)
        if before_quality is not None and after_quality is not None:
            quality_drop = before_quality - after_quality
            if quality_drop > self._config.max_quality_drop + 1e-9:
                failures.append(
                    GateFailure(
                        domain="slow",
                        key="quality",
                        reason=(
                            f"mean quality dropped by {quality_drop:.3f}, "
                            f"exceeding {self._config.max_quality_drop:.3f}"
                        ),
                        before=0,
                        after=0,
                    )
                )

        # 3. error rate
        before_errors = before.routing_error + before.execution_failed + before.fixture_error
        after_errors = after.routing_error + after.execution_failed + after.fixture_error
        before_error_rate = before_errors / before.trial_count if before.trial_count else 0.0
        after_error_rate = after_errors / after.trial_count if after.trial_count else 0.0
        if after_error_rate > before_error_rate + self._max_error_increase + 1e-9:
            failures.append(
                GateFailure(
                    domain="slow",
                    key="error_rate",
                    reason=(
                        f"error rate increased from {before_error_rate:.2%} to "
                        f"{after_error_rate:.2%}, exceeding allowed "
                        f"{self._max_error_increase:.2%} increase"
                    ),
                    before=before_errors,
                    after=after_errors,
                )
            )

        verdict = GateVerdict.PASS if not failures else GateVerdict.REJECTED
        return SlowGateResult(verdict=verdict, failures=tuple(failures))


@dataclass(frozen=True, slots=True)
class DiversityGuardResult:
    """Verdict from the Route Diversity Guard (Step 10)."""

    verdict: GateVerdict
    before_families: int
    after_families: int
    min_families: int
    lost_families: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.verdict is GateVerdict.PASS


class RouteDiversityGuard:
    """Prevent pruning from collapsing the search space into a single route ($129).

    Counts "successful route families": routes that have at least one business
    success in the slow report. If the after-count falls below
    ``min_successful_route_families``, the candidate is rejected.

    The guard also tracks which route families were lost so the report can show
    concrete evidence — the guard never prunes; it only flags a violation.
    """

    def __init__(self, min_successful_route_families: int = 2) -> None:
        if isinstance(min_successful_route_families, bool) or min_successful_route_families < 1:
            raise ValidationGateError(
                "min_successful_route_families must be a positive integer"
            )
        self._min_families = min_successful_route_families

    def evaluate(
        self,
        *,
        before_routes,
        after_routes,
    ) -> DiversityGuardResult:
        before_success = set(
            route_id
            for route_id, stat in before_routes.items()
            if stat.business_success_count > 0
        )
        after_success = set(
            route_id
            for route_id, stat in after_routes.items()
            if stat.business_success_count > 0
        )
        lost = tuple(sorted(before_success - after_success))
        if len(after_success) < self._min_families:
            verdict = GateVerdict.REJECTED
        else:
            verdict = GateVerdict.PASS
        return DiversityGuardResult(
            verdict=verdict,
            before_families=len(before_success),
            after_families=len(after_success),
            min_families=self._min_families,
            lost_families=lost,
        )