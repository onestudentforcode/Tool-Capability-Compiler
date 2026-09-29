"""Tier assignment: FAST / BALANCED / QUALITY with explainable rules (phase5 §11).

Judgements are independent (multi-label allowed) and relative to the best
value among RANKED routes, with every threshold concentrated in
:class:`TierConfig`. A route matching no tier keeps an empty ``tiers`` tuple
(UNASSIGNED) — it still appears in the vector table, unlabelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.errors import RankingConfigError
from .profile import RouteProfile


@dataclass(frozen=True, slots=True)
class TierConfig:
    success_tolerance: float = 0.02
    fast_latency_tolerance: float = 0.20
    quality_tolerance: float = 0.05
    balanced_latency_tolerance: float = 0.50
    balanced_quality_tolerance: float = 0.10
    balanced_cost_tolerance: float = 0.50

    def __post_init__(self) -> None:
        for name in (
            "success_tolerance",
            "fast_latency_tolerance",
            "quality_tolerance",
            "balanced_latency_tolerance",
            "balanced_quality_tolerance",
            "balanced_cost_tolerance",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or value < 0.0:
                raise RankingConfigError(
                    f"TierConfig {name} must be a non-negative number"
                )


class RouteTier(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    QUALITY = "quality"


@dataclass(frozen=True, slots=True)
class RouteTierAssignment:
    route_id: str
    tiers: tuple[RouteTier, ...]          # empty == UNASSIGNED
    matched_rules: tuple[str, ...]
    # metrics snapshot at decision time
    success_rate: float
    latency_median: float | None
    quality_mean: float | None
    cost_mean: float | None
    best_success_rate: float
    best_latency_median: float | None
    best_quality_mean: float | None
    best_cost_mean: float | None

    @property
    def unassigned(self) -> bool:
        return not self.tiers


def assign_tiers(
    profiles, config: TierConfig | None = None
) -> tuple[RouteTierAssignment, ...]:
    tier_config = config or TierConfig()
    if not profiles:
        return ()

    items = sorted(profiles, key=lambda item: item.route_id)
    best_success = max(profile.business_success_rate for profile in items)
    best_latency = _best(
        profiles, lambda item: item.latency_median, minimum=True
    )
    best_quality = _best(profiles, lambda item: item.quality_mean, minimum=False)
    best_cost = _best(profiles, lambda item: item.cost_mean, minimum=True)

    assignments: list[RouteTierAssignment] = []
    for profile in items:
        tiers: list[RouteTier] = []
        rules: list[str] = []
        success_ok = (
            profile.business_success_rate
            >= best_success - tier_config.success_tolerance
        )

        if (
            profile.latency_median is not None
            and best_latency is not None
            and success_ok
            and profile.latency_median
            <= best_latency * (1.0 + tier_config.fast_latency_tolerance)
        ):
            tiers.append(RouteTier.FAST)
            rules.append(
                f"fast: latency_median {_fmt(profile.latency_median)}ms <= "
                f"best {_fmt(best_latency)}ms x "
                f"{1.0 + tier_config.fast_latency_tolerance:.2f}"
            )

        if (
            profile.quality_mean is not None
            and best_quality is not None
            and success_ok
            and profile.quality_mean
            >= best_quality - tier_config.quality_tolerance
        ):
            tiers.append(RouteTier.QUALITY)
            rules.append(
                f"quality: quality_mean {_fmt(profile.quality_mean)} >= "
                f"best {_fmt(best_quality)} - {tier_config.quality_tolerance:.2f}"
            )

        # a globally absent dimension never blocks BALANCED; QUALITY above is
        # the only tier that explicitly requires quality to exist (§11.2)
        balanced_latency_ok = (
            best_latency is None
            or (
                profile.latency_median is not None
                and profile.latency_median
                <= best_latency * (1.0 + tier_config.balanced_latency_tolerance)
            )
        )
        balanced_quality_ok = (
            best_quality is None
            or (
                profile.quality_mean is not None
                and profile.quality_mean
                >= best_quality - tier_config.balanced_quality_tolerance
            )
        )
        balanced_cost_ok = (
            profile.cost_mean is None
            or best_cost is None
            or profile.cost_mean
            <= best_cost * (1.0 + tier_config.balanced_cost_tolerance)
        )
        if success_ok and balanced_latency_ok and balanced_quality_ok and balanced_cost_ok:
            tiers.append(RouteTier.BALANCED)
            rules.append(
                "balanced: within tolerance of best on success, latency, "
                "quality and cost"
            )

        assignments.append(
            RouteTierAssignment(
                route_id=profile.route_id,
                tiers=tuple(tiers),
                matched_rules=tuple(rules),
                success_rate=profile.business_success_rate,
                latency_median=profile.latency_median,
                quality_mean=profile.quality_mean,
                cost_mean=profile.cost_mean,
                best_success_rate=best_success,
                best_latency_median=best_latency,
                best_quality_mean=best_quality,
                best_cost_mean=best_cost,
            )
        )
    return tuple(assignments)


def _best(profiles, getter, *, minimum: bool) -> float | None:
    values = [getter(profile) for profile in profiles]
    known = [value for value in values if value is not None]
    if not known:
        return None
    return min(known) if minimum else max(known)


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}".rstrip("0").rstrip(".")
