"""Route evaluation, ranking and tiering (phase5)."""

from .eligibility import (
    EligibilityStatus,
    RankConfig,
    RankingEligibility,
    check_eligibility,
    ranked_ids,
)
from .family import RouteFamily, build_families
from .pareto import (
    DEFAULT_OBJECTIVES,
    Domination,
    Objective,
    ParetoFrontier,
    build_frontier,
)
from .profile import RouteProfile, TrialRow, build_profiles, rows_from_results
from .report import (
    CategoryRanking,
    RunMeta,
    RouteRankingReport,
    build_ranking_report,
    render,
    rows_from_run,
    to_json,
)
from .stats import statistical_tie, wilson_interval, z_score
from .tier import RouteTier, RouteTierAssignment, TierConfig, assign_tiers

__all__ = [
    "DEFAULT_OBJECTIVES",
    "CategoryRanking",
    "Domination",
    "EligibilityStatus",
    "Objective",
    "ParetoFrontier",
    "RankConfig",
    "RankingEligibility",
    "RouteFamily",
    "RouteProfile",
    "RouteRankingReport",
    "RouteTier",
    "RouteTierAssignment",
    "RunMeta",
    "TierConfig",
    "TrialRow",
    "assign_tiers",
    "build_families",
    "build_frontier",
    "build_profiles",
    "build_ranking_report",
    "check_eligibility",
    "ranked_ids",
    "render",
    "rows_from_results",
    "rows_from_run",
    "statistical_tie",
    "to_json",
    "wilson_interval",
    "z_score",
]
