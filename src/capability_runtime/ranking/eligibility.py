"""Evidence threshold for ranking eligibility (phase5 §8).

A route with fewer than ``min_trials`` trials has no standing: it is listed
as INSUFFICIENT_EVIDENCE with its shortfall, and never enters Pareto or tier
computation. Neither rewarded nor punished — just not yet eligible to speak.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.errors import RankingConfigError
from .profile import RouteProfile


@dataclass(frozen=True, slots=True)
class RankConfig:
    min_trials: int = 20
    confidence_level: float = 0.95

    def __post_init__(self) -> None:
        if isinstance(self.min_trials, bool) or self.min_trials < 1:
            raise RankingConfigError("min_trials must be a positive integer")
        if (
            isinstance(self.confidence_level, bool)
            or not 0.0 < self.confidence_level < 1.0
        ):
            raise RankingConfigError(
                "confidence_level must be within the open interval (0, 1)"
            )


class EligibilityStatus(str, Enum):
    RANKED = "ranked"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class RankingEligibility:
    route_id: str
    status: EligibilityStatus
    trial_count: int
    required_trials: int

    @property
    def shortfall(self) -> int:
        return max(0, self.required_trials - self.trial_count)


def check_eligibility(
    profiles, config: RankConfig | None = None
) -> tuple[RankingEligibility, ...]:
    """Classify every profile; output order is stable by route_id."""
    rank_config = config or RankConfig()
    return tuple(
        RankingEligibility(
            route_id=profile.route_id,
            status=(
                EligibilityStatus.RANKED
                if profile.trial_count >= rank_config.min_trials
                else EligibilityStatus.INSUFFICIENT_EVIDENCE
            ),
            trial_count=profile.trial_count,
            required_trials=rank_config.min_trials,
        )
        for profile in sorted(profiles, key=lambda item: item.route_id)
    )


def ranked_ids(eligibility) -> frozenset[str]:
    return frozenset(
        item.route_id
        for item in eligibility
        if item.status is EligibilityStatus.RANKED
    )
