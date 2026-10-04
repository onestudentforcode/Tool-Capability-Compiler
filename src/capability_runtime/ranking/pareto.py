"""Pareto frontier over the four-dimension route vector (phase5 §10).

Objectives: success_rate and quality_mean maximize; latency_median and
cost_mean minimize. A dimension that is ``None`` on either side is not
comparable and drops out of that pairwise judgement — the route is flagged
in ``partial_comparisons`` instead of being silently faked. Dominated routes
keep their dominators so the report can explain *why*.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Objective(str, Enum):
    SUCCESS = "success"    # maximize business_success_rate
    QUALITY = "quality"    # maximize quality_mean
    LATENCY = "latency"    # minimize latency_median
    COST = "cost"          # minimize cost_mean


DEFAULT_OBJECTIVES = (
    Objective.SUCCESS,
    Objective.QUALITY,
    Objective.LATENCY,
    Objective.COST,
)


@dataclass(frozen=True, slots=True)
class Domination:
    route_id: str
    dominated_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParetoFrontier:
    frontier: tuple[str, ...]
    dominated: tuple[Domination, ...]
    objectives: tuple[str, ...]
    partial_comparisons: tuple[str, ...]

    def dominators_of(self, route_id: str) -> tuple[str, ...]:
        for entry in self.dominated:
            if entry.route_id == route_id:
                return entry.dominated_by
        return ()


def _value(profile, objective: Objective) -> float | None:
    if objective is Objective.SUCCESS:
        return profile.business_success_rate
    if objective is Objective.QUALITY:
        return profile.quality_mean
    if objective is Objective.LATENCY:
        return profile.latency_median
    return profile.cost_mean


def _maximizes(objective: Objective) -> bool:
    return objective in (Objective.SUCCESS, Objective.QUALITY)


def _dominates(
    first, second, objectives
) -> tuple[bool, bool]:
    """Return (dominates, was_partial) for first vs second."""
    strictly_better = False
    partial = False
    comparable = 0
    for objective in objectives:
        left = _value(first, objective)
        right = _value(second, objective)
        if left is None or right is None:
            partial = True
            continue
        comparable += 1
        if _maximizes(objective):
            better, worse = left > right, left < right
        else:
            better, worse = left < right, left > right
        if worse:
            return (False, partial)
        if better:
            strictly_better = True
    if comparable == 0:
        # no shared dimension: not comparable, neither dominates
        return (False, True)
    return (strictly_better, partial)


def build_frontier(
    profiles, objectives=DEFAULT_OBJECTIVES
) -> ParetoFrontier:
    """Frontier members are pairwise non-dominated; dominated keep attribution."""
    items = sorted(profiles, key=lambda item: item.route_id)
    dominated_by: dict[str, list[str]] = {profile.route_id: [] for profile in items}
    partial_flags: set[str] = set()

    for index, first in enumerate(items):
        for second in items[index + 1 :]:
            first_wins, partial_a = _dominates(first, second, objectives)
            second_wins, partial_b = _dominates(second, first, objectives)
            if first_wins:
                dominated_by[second.route_id].append(first.route_id)
            elif second_wins:
                dominated_by[first.route_id].append(second.route_id)
            if partial_a or partial_b:
                partial_flags.update((first.route_id, second.route_id))

    frontier = tuple(
        profile.route_id
        for profile in items
        if not dominated_by[profile.route_id]
    )
    dominated = tuple(
        Domination(
            route_id=profile.route_id,
            dominated_by=tuple(sorted(dominated_by[profile.route_id])),
        )
        for profile in items
        if dominated_by[profile.route_id]
    )
    return ParetoFrontier(
        frontier=frontier,
        dominated=dominated,
        objectives=tuple(objective.value for objective in objectives),
        partial_comparisons=tuple(sorted(partial_flags)),
    )
