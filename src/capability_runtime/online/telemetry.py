"""Online telemetry: record facts, close the loop (phase6 §11).

Every request becomes one JSONL record; ``online_results_to_trials`` converts
served results back into Phase 3 ``TrialResult`` objects so online evidence
flows into the next offline regression/optimization round unchanged. Telemetry
never touches the catalog or ranking — recording is strictly append-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.errors import OnlineRoutingError
from ..core.metrics import TokenUsage
from ..regression.slow.route import ObservedRoute
from ..regression.slow.stats import summarize
from ..regression.slow.trace import ExecutionTrace
from ..regression.slow.trial import (
    Trial,
    TrialExecutionStatus,
    TrialResult,
)
from .runtime import OnlineResult, OnlineStatus

_STATUS_TO_TRIAL = {
    OnlineStatus.SERVED: TrialExecutionStatus.COMPLETED,
    OnlineStatus.ROUTE_FAILED: TrialExecutionStatus.LAYER_ERROR,
    OnlineStatus.NO_CANDIDATE: TrialExecutionStatus.ROUTING_ERROR,
}


@dataclass(frozen=True, slots=True)
class OnlineRecord:
    request_id: str
    timestamp: str
    category: str | None
    tier_preference: str | None
    selected_route_id: str | None
    status: str
    latency_ms: float
    cost: float | None
    input_tokens: int
    output_tokens: int
    fallback_depth: int


@dataclass(frozen=True, slots=True)
class RouteUsageStats:
    """Per-route online facts, aligned with Phase 3 observation stats."""

    route_id: str
    usage_count: int
    served_count: int
    failed_count: int
    latency_mean: float | None
    latency_median: float | None
    latency_p95: float | None
    cost_mean: float | None


def record_of(result: OnlineResult) -> OnlineRecord:
    return OnlineRecord(
        request_id=result.request_id,
        timestamp=datetime.now().isoformat(),
        category=result.category,
        tier_preference=result.tier_preference,
        selected_route_id=result.selected_route_id,
        status=result.status.value,
        latency_ms=result.latency_ms,
        cost=result.cost,
        input_tokens=result.token_usage.input_tokens,
        output_tokens=result.token_usage.output_tokens,
        fallback_depth=result.fallback_depth,
    )


class OnlineTelemetry:
    """Append-only collector + JSONL writer + per-route aggregation."""

    def __init__(self) -> None:
        self._records: list[OnlineRecord] = []

    def append(self, result: OnlineResult) -> OnlineRecord:
        record = record_of(result)
        self._records.append(record)
        return record

    @property
    def records(self) -> tuple[OnlineRecord, ...]:
        return tuple(self._records)

    def write_jsonl(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as file:
            for record in self._records:
                file.write(json.dumps(_to_json(record)) + "\n")
        return target

    def usage_stats(self) -> tuple[RouteUsageStats, ...]:
        by_route: dict[str, list[OnlineRecord]] = {}
        for record in self._records:
            if record.selected_route_id is None:
                continue
            by_route.setdefault(record.selected_route_id, []).append(record)
        stats: list[RouteUsageStats] = []
        for route_id in sorted(by_route):
            members = by_route[route_id]
            latencies = [item.latency_ms for item in members]
            costs = [item.cost for item in members if item.cost is not None]
            mean, median, p95 = summarize(latencies)
            cost_mean, _cost_median, _p95c = summarize(costs)
            stats.append(
                RouteUsageStats(
                    route_id=route_id,
                    usage_count=len(members),
                    served_count=sum(
                        1 for item in members if item.status == "served"
                    ),
                    failed_count=sum(
                        1 for item in members if item.status != "served"
                    ),
                    latency_mean=mean,
                    latency_median=median,
                    latency_p95=p95,
                    cost_mean=cost_mean,
                )
            )
        return tuple(stats)


def online_results_to_trials(
    results, *, scenario_suite_version: str = "online", router_config_id: str = "online"
) -> tuple[TrialResult, ...]:
    """Convert online results into regression-grade trial results.

    This is the loop closure: online observations become input evidence for
    the next slow regression / optimization round. Status mapping per
    phase6-plan §4; evaluation stays None (business judging is offline).
    """
    converted: list[TrialResult] = []
    for index, result in enumerate(results):
        trial = Trial(
            id=result.request_id,
            scenario_id=result.category or "online",
            trial_index=index,
            topology_version=result.trace.topology_version
            if result.trace is not None
            else "unknown",
            scenario_suite_version=scenario_suite_version,
            router_config_id=router_config_id,
        )
        converted.append(
            TrialResult(
                trial=trial,
                execution_status=_STATUS_TO_TRIAL[result.status],
                route=_route_of(result),
                trace=result.trace
                if result.trace is not None
                else ExecutionTrace(
                    trial_id=trial.id,
                    scenario_id=trial.scenario_id,
                    topology_version=trial.topology_version,
                ),
                evaluation=None,
                latency_ms=result.latency_ms,
                token_usage=result.token_usage,
                cost=result.cost,
            )
        )
    return tuple(converted)


def _route_of(result: OnlineResult) -> ObservedRoute | None:
    trace = result.trace
    if trace is None:
        return None
    layers = [
        (layer.layer, layer.selected_tools)
        for layer in trace.layers
        if layer.selected_tools
    ]
    if not layers:
        return None
    return ObservedRoute.from_layers(layers)


def _to_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, OnlineRecord):
        return {
            field: getattr(value, field)
            for field in (
                "request_id", "timestamp", "category", "tier_preference",
                "selected_route_id", "status", "latency_ms", "cost",
                "input_tokens", "output_tokens", "fallback_depth",
            )
        }
    raise OnlineRoutingError(f"cannot serialize telemetry record {type(value)!r}")
