"""RouteRankingReport: build, render, serialize and load from disk (phase5 §14).

The report keeps the full multi-dimensional vector — no composite score, no
single winner. Statistical ties are listed, never silently ordered; the text
rendering exists for humans, ``to_json`` for Phase 6.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..core.errors import RouteProfileError
from .eligibility import (
    EligibilityStatus,
    RankConfig,
    RankingEligibility,
    check_eligibility,
    ranked_ids,
)
from .family import RouteFamily, build_families
from .pareto import ParetoFrontier, build_frontier
from .profile import RouteProfile, TrialRow, build_profiles
from .stats import statistical_tie
from .tier import RouteTier, RouteTierAssignment, TierConfig, assign_tiers


@dataclass(frozen=True, slots=True)
class CategoryRanking:
    """Per-category scope: best_* values are taken inside the category only."""

    category: str
    route_count: int
    frontier: tuple[str, ...]
    tiers: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class RunMeta:
    """Version binding lifted from a slow-run manifest."""

    run_id: str
    suite_name: str
    suite_version: str
    topology_version: str
    router_config_id: str


@dataclass(frozen=True, slots=True)
class RouteRankingReport:
    topology_version: str
    router_config_id: str
    suite_name: str | None
    suite_version: str | None
    source_run: str | None
    min_trials: int
    profiles: tuple[RouteProfile, ...]
    eligibility: tuple[RankingEligibility, ...]
    pareto: ParetoFrontier
    tier_assignments: tuple[RouteTierAssignment, ...]
    families: tuple[RouteFamily, ...]
    statistical_ties: tuple[tuple[str, str], ...]
    categories: tuple[CategoryRanking, ...]

    @property
    def ranked_profiles(self) -> tuple[RouteProfile, ...]:
        ranked = ranked_ids(self.eligibility)
        return tuple(
            profile for profile in self.profiles if profile.route_id in ranked
        )

    @property
    def insufficient(self) -> tuple[RankingEligibility, ...]:
        return tuple(
            item
            for item in self.eligibility
            if item.status is EligibilityStatus.INSUFFICIENT_EVIDENCE
        )

    def assignment_of(self, route_id: str) -> RouteTierAssignment | None:
        return next(
            (
                item
                for item in self.tier_assignments
                if item.route_id == route_id
            ),
            None,
        )


def build_ranking_report(
    rows: Sequence[TrialRow],
    *,
    category_of: Mapping[str, str] | None = None,
    rank_config: RankConfig | None = None,
    tier_config: TierConfig | None = None,
    suite_name: str | None = None,
    suite_version: str | None = None,
    source_run: str | None = None,
) -> RouteRankingReport:
    """Rows in, full ranking out. Pareto and tiers see RANKED routes only."""
    config = rank_config or RankConfig()
    profiles = build_profiles(
        rows, category_of=category_of, confidence_level=config.confidence_level
    )
    eligibility = check_eligibility(profiles, config)
    ranked = ranked_ids(eligibility)
    ranked_profiles = tuple(
        profile for profile in profiles if profile.route_id in ranked
    )
    return RouteRankingReport(
        topology_version=profiles[0].topology_version,
        router_config_id=profiles[0].router_config_id,
        suite_name=suite_name,
        suite_version=suite_version,
        source_run=source_run,
        min_trials=config.min_trials,
        profiles=profiles,
        eligibility=eligibility,
        pareto=build_frontier(ranked_profiles),
        tier_assignments=assign_tiers(ranked_profiles, tier_config),
        families=build_families(ranked_profiles),
        statistical_ties=_statistical_ties(ranked_profiles),
        categories=_category_rankings(ranked_profiles, tier_config),
    )


def _statistical_ties(
    profiles: Sequence[RouteProfile],
) -> tuple[tuple[str, str], ...]:
    items = sorted(profiles, key=lambda item: item.route_id)
    ties: list[tuple[str, str]] = []
    for index, first in enumerate(items):
        for second in items[index + 1 :]:
            if statistical_tie(
                first.success_confidence_interval,
                second.success_confidence_interval,
            ):
                ties.append((first.route_id, second.route_id))
    return tuple(ties)


def _category_rankings(
    profiles: Sequence[RouteProfile], tier_config: TierConfig | None
) -> tuple[CategoryRanking, ...]:
    categories = sorted(
        {category for profile in profiles for category in profile.categories}
    )
    rankings: list[CategoryRanking] = []
    for category in categories:
        subset = tuple(
            profile
            for profile in profiles
            if category in profile.categories
        )
        tiers = assign_tiers(subset, tier_config)
        grouped: dict[str, list[str]] = {}
        for assignment in tiers:
            for tier in assignment.tiers:
                grouped.setdefault(tier.value, []).append(assignment.route_id)
        rankings.append(
            CategoryRanking(
                category=category,
                route_count=len(subset),
                frontier=build_frontier(subset).frontier,
                tiers=tuple(
                    (tier, tuple(sorted(ids))) for tier, ids in sorted(grouped.items())
                ),
            )
        )
    return tuple(rankings)


# ---- disk adapter (Step 8) ---------------------------------------------------


def rows_from_run(run_dir: str | Path) -> tuple[tuple[TrialRow, ...], RunMeta]:
    """Load ranking rows from a slow-regression artifact directory.

    Requires ``manifest.json`` (version binding) and ``traces.jsonl`` (rows).
    A manifest that contradicts the traces is refused, not merged.
    """
    directory = Path(run_dir)
    manifest_path = directory / "manifest.json"
    traces_path = directory / "traces.jsonl"
    if not manifest_path.is_file() or not traces_path.is_file():
        raise RouteProfileError(
            f"{directory} is not a slow-run directory "
            "(manifest.json and traces.jsonl are required)"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RouteProfileError(f"cannot read manifest.json: {exc}") from exc

    meta = RunMeta(
        run_id=str(manifest.get("run_id", "")),
        suite_name=str(manifest.get("suite_name", "")),
        suite_version=str(manifest.get("suite_version", "")),
        topology_version=str(manifest.get("topology_version", "")),
        router_config_id=str(manifest.get("router_config_id", "")),
    )

    rows: list[TrialRow] = []
    with traces_path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(_row_from_json(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise RouteProfileError(
                    f"traces.jsonl line {line_number} is unreadable: {exc}"
                ) from exc
    if not rows:
        raise RouteProfileError("traces.jsonl contains no trials")

    manifest_versions = (meta.topology_version, meta.router_config_id)
    row_versions = {
        (row.topology_version, row.router_config_id) for row in rows
    }
    if len(row_versions) > 1 or (
        manifest_versions != ("", "") and manifest_versions not in row_versions
    ):
        raise RouteProfileError(
            "manifest and traces disagree on topology/router versions; "
            "refusing to rank mixed evidence"
        )
    return tuple(rows), meta


def _row_from_json(payload: Mapping[str, Any]) -> TrialRow:
    trial = payload["trial"]
    route = payload.get("route")
    evaluation = payload.get("evaluation")
    tokens = payload.get("token_usage") or {}
    return TrialRow(
        scenario_id=str(trial["scenario_id"]),
        route_id=route["route_id"] if route else None,
        canonical=route.get("canonical") if route else None,
        completed=payload["execution_status"] == "completed",
        success=evaluation["success"] if evaluation else None,
        quality=evaluation.get("quality_score") if evaluation else None,
        latency_ms=float(payload["latency_ms"]),
        cost=payload.get("cost"),
        tool_cost=payload.get("tool_cost"),
        routing_cost=payload.get("routing_cost"),
        evaluation_cost=payload.get("evaluation_cost"),
        input_tokens=int(tokens.get("input_tokens", 0)),
        output_tokens=int(tokens.get("output_tokens", 0)),
        segment_tool_counts=(
            tuple(len(segment["tools"]) for segment in route["segments"])
            if route
            else ()
        ),
        topology_version=str(trial["topology_version"]),
        router_config_id=str(trial["router_config_id"]),
        access_counts=dict(payload["access_counts"])
        if payload.get("access_counts")
        else None,
    )


# ---- rendering / serialization ----------------------------------------------


def to_json(report: RouteRankingReport) -> dict[str, Any]:
    """Loss-free JSON view; Phase 6 may consume this directly."""
    return _to_json(report)


def _to_json(value: Any) -> Any:
    # Enum must be checked before the primitives: a `str, Enum` member is a
    # str subclass and would otherwise slip through as the enum object.
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _to_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_to_json(item) for item in value]
    if is_dataclass(value):
        return {
            field.name: _to_json(getattr(value, field.name))
            for field in fields(value)
        }
    return str(value)


def render(report: RouteRankingReport, *, width: int | None = 88) -> str:
    """Human text output (phase5 §14).

    ``width`` wraps route canonical lines at layer-segment boundaries
    with a hanging indent (tools are never elided); None disables
    wrapping (legacy single line).
    """
    lines = [
        "Route Ranking",
        "",
        f"Topology: {report.topology_version}        "
        f"Router: {report.router_config_id}",
    ]
    suite = report.suite_name or "-"
    suite_version = report.suite_version or "-"
    source = report.source_run or "-"
    lines.append(f"Suite: {suite} {suite_version}        Source run: {source}")
    lines.append(f"Min trials: {report.min_trials}")
    lines.append("")
    ranked_count = len(report.ranked_profiles)
    lines.append(f"Ranked Routes: {ranked_count} / {len(report.profiles)}")
    lines.append("")

    profile_of = {profile.route_id: profile for profile in report.profiles}
    frontier = set(report.pareto.frontier)
    for tier in (RouteTier.QUALITY, RouteTier.FAST, RouteTier.BALANCED):
        members = [
            assignment
            for assignment in report.tier_assignments
            if tier in assignment.tiers
        ]
        if not members:
            continue
        lines.append(f"Tier {tier.value.upper()}")
        for assignment in members:
            lines.extend(
                _render_profile(
                    profile_of[assignment.route_id], frontier, width=width
                )
            )
            others = [
                other.value for other in assignment.tiers if other is not tier
            ]
            if others:
                lines.append(f"      also: {', '.join(sorted(others))}")
        lines.append("")

    unassigned = [
        assignment for assignment in report.tier_assignments if assignment.unassigned
    ]
    if unassigned:
        lines.append("UNASSIGNED")
        for assignment in unassigned:
            lines.extend(
                _render_profile(
                    profile_of[assignment.route_id], frontier, width=width
                )
            )
        lines.append("")

    lines.append(
        f"Pareto Frontier: {len(report.pareto.frontier)} members / "
        f"{len(report.pareto.dominated)} dominated "
        f"({len(report.pareto.objectives)} objectives)"
    )
    if report.pareto.partial_comparisons:
        lines.append(
            "Partial comparisons (missing dimensions excluded): "
            + ", ".join(report.pareto.partial_comparisons)
        )
    for domination in report.pareto.dominated:
        lines.append(
            f"  {_display(profile_of[domination.route_id])}  "
            f"dominated by: "
            + ", ".join(
                _display(profile_of[dominator])
                for dominator in domination.dominated_by
            )
        )
    lines.append("")

    if report.statistical_ties:
        lines.append("Statistical Ties (success, intervals overlap):")
        for first, second in report.statistical_ties:
            lines.append(
                f"  {_display(profile_of[first])} ~ {_display(profile_of[second])}"
            )
        lines.append("")

    if report.categories:
        lines.append("Category Breakdown:")
        for category in report.categories:
            tiers = ", ".join(
                f"{tier}=[{', '.join(ids)}]" for tier, ids in category.tiers
            ) or "-"
            lines.append(
                f"  {category.category}: {category.route_count} ranked routes, "
                f"frontier={len(category.frontier)}, tiers: {tiers}"
            )
        lines.append("")

    insufficient = report.insufficient
    lines.append(f"Insufficient Evidence ({len(insufficient)})")
    if insufficient:
        for item in insufficient:
            profile = profile_of[item.route_id]
            lines.append(
                f"  {_display(profile)}  trials {item.trial_count} / "
                f"{item.required_trials}"
            )
    lines.append("")
    lines.append("(Phase 5 ranks only; route selection belongs to Phase 6.)")
    return "\n".join(lines)


def _display(profile: RouteProfile) -> str:
    """Canonical form flattened to one line for report text."""
    return profile.canonical.replace("\n", " ")


def _render_profile(
    profile: RouteProfile, frontier: set[str], *, width: int | None = 88
) -> list[str]:
    low, high = profile.success_confidence_interval
    quality = (
        f"{profile.quality_mean:.2f}" if profile.quality_mean is not None else "-"
    )
    p95 = f"{profile.latency_p95:.0f}ms" if profile.latency_p95 is not None else "-"
    cost = f"${profile.cost_mean:.3f}" if profile.cost_mean is not None else "-"
    route_lines = _wrap_route(profile, width=width)
    return [
        *route_lines,
        f"      success {profile.business_success_rate * 100:.1f}% "
        f"[{low * 100:.1f}, {high * 100:.1f}]  quality {quality}  "
        f"latency {profile.latency_median:.0f}ms (p95 {p95})",
        f"      cost {cost}  trials {profile.trial_count}  "
        f"pareto: {'yes' if profile.route_id in frontier else 'no'}",
    ]


def _wrap_route(
    profile: RouteProfile, *, width: int | None = 88, indent: str = "  "
) -> list[str]:
    """Route canonical as wrapped lines - segment (layer) per boundary.

    Every layer segment stays intact on one line; wrapping never drops
    tools. width=None keeps the legacy single flattened line.
    """
    canonical = _display(profile)
    if width is None or len(indent) + len(canonical) <= width:
        return [f"{indent}{canonical}"]
    segments = canonical.split(" ")
    continuation = f"{indent}    "
    lines: list[str] = []
    current = indent
    for segment in segments:
        if current == indent:
            candidate = current + segment
        elif len(current) + 1 + len(segment) <= width:
            candidate = current + " " + segment
        else:
            lines.append(current)
            current = continuation + segment
            continue
        current = candidate
    lines.append(current)
    return lines
