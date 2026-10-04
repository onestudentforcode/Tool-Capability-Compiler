"""Route profiles: the multi-dimensional evidence vector (phase5 §7).

Everything ranking consumes funnels through :class:`TrialRow` — the minimal
projection of one trial — so the in-process path (``TrialResult`` sequence)
and the on-disk path (``traces.jsonl``) share a single aggregation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..core.errors import RouteProfileError
from ..core.metrics import TokenUsage
from ..regression.slow.stats import summarize
from ..regression.slow.trial import TrialExecutionStatus, TrialResult
from .stats import wilson_interval


@dataclass(frozen=True, slots=True)
class TrialRow:
    """Ranking's view of one trial: structure, outcome, metering, versions."""

    scenario_id: str
    route_id: str | None
    canonical: str | None
    completed: bool
    success: bool | None
    quality: float | None
    latency_ms: float
    cost: float | None
    tool_cost: float | None
    routing_cost: float | None
    evaluation_cost: float | None
    input_tokens: int
    output_tokens: int
    segment_tool_counts: tuple[int, ...]
    topology_version: str
    router_config_id: str
    access_counts: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class RouteProfile:
    """The four-dimension vector for one observed route (phase5 §3).

    ``business_success_rate`` divides by trials that produced an evaluation —
    never by raw trial count. Absent dimensions stay ``None``; absence is
    never faked as zero (battlefield-hardening batch A semantics).
    """

    route_id: str
    canonical: str
    trial_count: int
    completed_count: int
    evaluated_count: int
    scenario_count: int
    categories: tuple[str, ...]
    business_success_count: int
    business_success_rate: float
    success_confidence_interval: tuple[float, float]
    quality_mean: float | None
    quality_median: float | None
    latency_mean: float | None
    latency_median: float | None
    latency_p95: float | None
    cost_mean: float | None
    cost_median: float | None
    token_usage: TokenUsage
    tool_count: int
    layer_count: int
    topology_version: str
    router_config_id: str
    access_counts: dict[str, int] | None = None


def _merge_access(rows) -> dict[str, int] | None:
    merged: dict[str, int] | None = None
    for row in rows:
        if not row.access_counts:
            continue
        if merged is None:
            merged = {}
        for key, count in row.access_counts.items():
            merged[key] = merged.get(key, 0) + count
    return merged


def rows_from_results(results: Sequence[TrialResult]) -> tuple[TrialRow, ...]:
    """Adapt in-process trial results into ranking rows."""
    rows: list[TrialRow] = []
    for result in results:
        route = result.route
        evaluation = result.evaluation
        rows.append(
            TrialRow(
                scenario_id=result.trial.scenario_id,
                route_id=route.route_id if route is not None else None,
                canonical=route.canonical if route is not None else None,
                completed=(
                    result.execution_status is TrialExecutionStatus.COMPLETED
                ),
                success=(
                    evaluation.success if evaluation is not None else None
                ),
                quality=(
                    evaluation.quality_score
                    if evaluation is not None
                    else None
                ),
                latency_ms=result.latency_ms,
                cost=result.cost,
                tool_cost=result.tool_cost,
                routing_cost=result.routing_cost,
                evaluation_cost=result.evaluation_cost,
                input_tokens=result.token_usage.input_tokens,
                output_tokens=result.token_usage.output_tokens,
                segment_tool_counts=(
                    tuple(len(segment.tools) for segment in route.segments)
                    if route is not None
                    else ()
                ),
                topology_version=result.trial.topology_version,
                router_config_id=result.trial.router_config_id,
                access_counts=dict(result.access_counts)
                if result.access_counts
                else None,
            )
        )
    return tuple(rows)


def build_profiles(
    rows: Sequence[TrialRow],
    *,
    category_of: Mapping[str, str] | None = None,
    confidence_level: float = 0.95,
) -> tuple[RouteProfile, ...]:
    """Aggregate rows into per-route profiles; one rankable unit per route_id.

    Rows without an observed route are ignored (nothing to rank). Mixed
    topology/router versions are refused — cross-version comparison is not
    ranking's job (phase5 §5.1).
    """
    if not rows:
        raise RouteProfileError("cannot build profiles from an empty row set")

    versions = {
        (row.topology_version, row.router_config_id) for row in rows
    }
    if len(versions) > 1:
        detail = "; ".join(
            f"topology={topology} router={router}"
            for topology, router in sorted(versions)
        )
        raise RouteProfileError(
            "rows mix incompatible versions, refusing to rank: " + detail
        )
    topology_version, router_config_id = sorted(versions)[0]

    grouped: dict[str, list[TrialRow]] = {}
    for row in rows:
        if row.route_id is None:
            continue
        grouped.setdefault(row.route_id, []).append(row)
    if not grouped:
        raise RouteProfileError(
            "no row carries an observed route; nothing to rank"
        )

    profiles: list[RouteProfile] = []
    for route_id in sorted(grouped):
        members = grouped[route_id]
        with_route = [row for row in members if row.route_id is not None]
        evaluated = [row for row in with_route if row.success is not None]
        successes = sum(1 for row in evaluated if row.success)
        rate = successes / len(evaluated) if evaluated else 0.0
        qualities = [row.quality for row in evaluated if row.quality is not None]
        latencies = [row.latency_ms for row in with_route]
        costs = [row.cost for row in with_route if row.cost is not None]
        quality_mean, quality_median, _ = summarize(qualities)
        latency_mean, latency_median, latency_p95 = summarize(latencies)
        cost_mean, cost_median, _ = summarize(costs)
        scenarios = {row.scenario_id for row in with_route}
        categories = (
            {
                category_of[row.scenario_id]
                for row in with_route
                if row.scenario_id in category_of
            }
            if category_of is not None
            else set()
        )
        segment_tool_counts = next(
            row.segment_tool_counts for row in with_route if row.segment_tool_counts
        )
        profiles.append(
            RouteProfile(
                route_id=route_id,
                canonical=next(
                    (
                        row.canonical
                        for row in with_route
                        if row.canonical is not None
                    ),
                    route_id,
                ),
                trial_count=len(with_route),
                completed_count=sum(1 for row in with_route if row.completed),
                evaluated_count=len(evaluated),
                scenario_count=len(scenarios),
                categories=tuple(sorted(categories)),
                business_success_count=successes,
                business_success_rate=rate,
                success_confidence_interval=wilson_interval(
                    successes, len(evaluated), confidence_level
                ),
                quality_mean=quality_mean,
                quality_median=quality_median,
                latency_mean=latency_mean,
                latency_median=latency_median,
                latency_p95=latency_p95,
                cost_mean=cost_mean,
                cost_median=cost_median,
                token_usage=TokenUsage(
                    input_tokens=sum(row.input_tokens for row in with_route),
                    output_tokens=sum(row.output_tokens for row in with_route),
                ),
                access_counts=_merge_access(with_route),
                tool_count=sum(segment_tool_counts),
                layer_count=len(segment_tool_counts),
                topology_version=topology_version,
                router_config_id=router_config_id,
            )
        )
    return tuple(profiles)
