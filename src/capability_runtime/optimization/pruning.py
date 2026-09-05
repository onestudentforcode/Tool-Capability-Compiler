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