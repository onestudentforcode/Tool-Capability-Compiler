from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from .trial import TrialResult, TrialExecutionStatus


def summarize(values: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    """Return (mean, median, p95) for a series of positive measurements."""
    ordered = sorted(values)
    if not ordered:
        return None, None, None
    mean = sum(ordered) / len(ordered)
    median = ordered[len(ordered) // 2]
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return mean, median, ordered[p95_index]


@dataclass(frozen=True, slots=True)
class SelectionEvent:
    """The most primitive exploration record: what was offered vs chosen ($92)."""

    layer: str
    available_tools: tuple[str, ...]
    selected_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NodeObservationStats:
    tool: str
    selected_count: int = 0
    success_trial_count: int = 0
    failed_trial_count: int = 0
    opportunity_count: int = 0

    @property
    def selection_rate(self) -> float:
        if self.opportunity_count == 0:
            return 0.0
        return self.selected_count / self.opportunity_count


@dataclass(frozen=True, slots=True)
class EdgeObservationStats:
    source: str
    target: str
    observed_count: int = 0
    successful_trial_count: int = 0
    failed_trial_count: int = 0
    opportunity_count: int = 0


@dataclass(frozen=True, slots=True)
class RouteObservationStats:
    route_id: str
    usage_count: int = 0
    completed_count: int = 0
    business_success_count: int = 0
    business_failure_count: int = 0
    latencies: tuple[float, ...] = ()
    costs: tuple[float, ...] = ()
    quality_scores: tuple[float, ...] = ()

    @property
    def latency_basics(self) -> tuple[float | None, float | None, float | None]:
        return summarize(self.latencies)

    @property
    def cost_basics(self) -> tuple[float | None, float | None, float | None]:
        return summarize(self.costs)

    @property
    def quality_basics(self) -> tuple[float | None, float | None, float | None]:
        return summarize(self.quality_scores)


@dataclass(frozen=True, slots=True)
class ObservationReport:
    scenario_count: int
    trial_count: int
    node_stats: dict[str, NodeObservationStats]
    edge_stats: dict[str, EdgeObservationStats]
    route_stats: dict[str, RouteObservationStats]
    selection_events: tuple[SelectionEvent, ...]
    scenario_route_distribution: dict[str, dict[str, int]]
    unique_route_count: int


@dataclass(frozen=True, slots=True)
class ExpansionDelta:
    """baseline vs variant difference — evidence for a Phase 4 correction."""

    scenario_id: str
    layer: str
    baseline_tools: tuple[str, ...]
    variant_tools: tuple[str, ...]
    latency_delta_ms: float
    cost_delta: float
    quality_delta: float


def _must_route_edges(edges: Sequence[tuple[str, str]]) -> set[tuple[str, str]]:
    return {(source, target) for (source, target) in edges}


def build_observation_stats(
    results: Sequence[TrialResult],
    *,
    edges: Sequence[tuple[str, str]] = (),
) -> ObservationReport:
    edge_set = _must_route_edges(edges)
    events: list[SelectionEvent] = []
    node_trials: dict[str, set[str]] = defaultdict(set)
    node_opportunity: Counter[str] = Counter()
    edge_trials: dict[tuple[str, str], set[str]] = defaultdict(set)
    edge_opportunity: Counter[tuple[str, str]] = Counter()
    completed_trials: set[str] = set()
    scenario_route: dict[str, Counter[str]] = defaultdict(Counter)
    route_lat: dict[str, list[float]] = defaultdict(list)
    route_cost: dict[str, list[float]] = defaultdict(list)
    route_quality: dict[str, list[float]] = defaultdict(list)
    route_counts: Counter[str] = Counter()
    route_completed: Counter[str] = Counter()
    route_success: Counter[str] = Counter()
    route_failed: Counter[str] = Counter()
    scenarios: set[str] = set()
    observed_routes: set[str] = set()

    for result in results:
        trial_id = result.trial.id
        scenarios.add(result.trial.scenario_id)
        completed = result.execution_status is TrialExecutionStatus.COMPLETED
        if completed:
            completed_trials.add(trial_id)

        layers = result.trace.layers
        for index, layer in enumerate(layers):
            events.append(
                SelectionEvent(
                    layer=layer.layer,
                    available_tools=layer.available_tools,
                    selected_tools=layer.selected_tools,
                )
            )
            for tool in layer.selected_tools:
                node_trials[tool].add(trial_id)
            for tool in layer.available_tools:
                node_opportunity[tool] += 1
            if index + 1 >= len(layers):
                continue
            following = layers[index + 1]
            for source in layer.selected_tools:
                for target in following.available_tools:
                    if (source, target) not in edge_set:
                        continue
                    edge_opportunity[(source, target)] += 1
                    if target in following.selected_tools:
                        edge_trials[(source, target)].add(trial_id)

        if result.route is not None:
            route_id = result.route.route_id
            observed_routes.add(route_id)
            route_counts[route_id] += 1
            if completed:
                route_completed[route_id] += 1
            if result.evaluation is not None:
                if result.evaluation.success:
                    route_success[route_id] += 1
                else:
                    route_failed[route_id] += 1
            route_lat[route_id].append(result.latency_ms)
            if result.cost is not None:
                route_cost[route_id].append(result.cost)
            if result.evaluation is not None and result.evaluation.quality_score is not None:
                route_quality[route_id].append(result.evaluation.quality_score)
            scenario_route[result.trial.scenario_id][route_id] += 1

    node_names = sorted(set(node_trials) | set(node_opportunity))
    node_stats: dict[str, NodeObservationStats] = {}
    for tool in node_names:
        ids = node_trials[tool]
        success = len({i for i in ids if i in completed_trials})
        node_stats[tool] = NodeObservationStats(
            tool=tool,
            selected_count=len(ids),
            success_trial_count=success,
            failed_trial_count=len(ids) - success,
            opportunity_count=node_opportunity[tool],
        )

    edge_keys = sorted(set(edge_trials) | set(edge_opportunity))
    edge_stats: dict[str, EdgeObservationStats] = {}
    for source, target in edge_keys:
        ids = edge_trials[(source, target)]
        success = len({i for i in ids if i in completed_trials})
        edge_stats[f"{source}->{target}"] = EdgeObservationStats(
            source=source,
            target=target,
            observed_count=len(ids),
            successful_trial_count=success,
            failed_trial_count=len(ids) - success,
            opportunity_count=edge_opportunity[(source, target)],
        )

    route_stats: dict[str, RouteObservationStats] = {}
    for route_id in sorted(observed_routes):
        route_stats[route_id] = RouteObservationStats(
            route_id=route_id,
            usage_count=route_counts[route_id],
            completed_count=route_completed[route_id],
            business_success_count=route_success[route_id],
            business_failure_count=route_failed[route_id],
            latencies=tuple(sorted(route_lat[route_id])),
            costs=tuple(sorted(route_cost[route_id])),
            quality_scores=tuple(sorted(route_quality[route_id])),
        )

    distribution = {
        scenario: dict(sorted(routes.items()))
        for scenario, routes in sorted(scenario_route.items())
    }
    return ObservationReport(
        scenario_count=len(scenarios),
        trial_count=len(results),
        node_stats=node_stats,
        edge_stats=edge_stats,
        route_stats=route_stats,
        selection_events=tuple(events),
        scenario_route_distribution=distribution,
        unique_route_count=len(observed_routes),
    )


def _route_tool_count(route) -> int:
    return sum(len(segment.tools) for segment in route.segments)


def compute_expansion_deltas(
    results: Sequence[TrialResult],
) -> tuple[ExpansionDelta, ...]:
    """Compare each observed route against the most minimal baseline route.

    Under basefast the baseline is the seed route (fewest tools per layer), and
    any observed route strictly extending it is a variant. The delta is only
    evidence for a later Phase 4 correction, never a decision itself.
    """
    by_scenario: dict[str, list[TrialResult]] = defaultdict(list)
    for result in results:
        if result.route is None:
            continue
        by_scenario[result.trial.scenario_id].append(result)

    deltas: list[ExpansionDelta] = []
    for scenario_id in sorted(by_scenario):
        group = by_scenario[scenario_id]
        baseline_key = min(
            group,
            key=lambda r: (
                _route_tool_count(r.route),
                r.route.route_id,
            ),
        ).route.route_id
        baseline = [r for r in group if r.route.route_id == baseline_key]
        baseline_count = _route_tool_count(baseline[0].route)
        baseline_lat = _mean(r.latency_ms for r in baseline)
        baseline_cost = _mean_weighted_costs(baseline)
        baseline_quality = _mean_quality(baseline)

        variants = {
            r.route.route_id: r
            for r in group
            if _route_tool_count(r.route) > baseline_count
        }
        for route_id in sorted(variants):
            var = variants[route_id]
            items = [r for r in group if r.route.route_id == route_id]
            delta_path = _first_diff(baseline[0].route, var.route)
            if delta_path is None:
                continue
            deltas.append(
                ExpansionDelta(
                    scenario_id=scenario_id,
                    layer=delta_path[0],
                    baseline_tools=delta_path[1],
                    variant_tools=delta_path[2],
                    latency_delta_ms=_mean(r.latency_ms for r in items) - baseline_lat,
                    cost_delta=_mean_weighted_costs(items) - baseline_cost,
                    quality_delta=_mean_quality(items) - baseline_quality,
                )
            )
    return tuple(deltas)


def _mean(values: Sequence[float]) -> float:
    values = [v for v in values if v is not None]
    return (sum(values) / len(values)) if values else 0.0


def _mean_weighted_costs(items: Sequence) -> float:
    return _mean([r.cost if r.cost is not None else 0.0 for r in items])


def _mean_quality(items: Sequence) -> float:
    scored = [r.evaluation.quality_score for r in items if r.evaluation is not None]
    return _mean([q for q in scored if q is not None])


def _first_diff(baseline_route, variant_route):
    """Return (layer, baseline_tools, variant_tools) of the first differing layer."""
    baseline_segments = {seg.layer: seg.tools for seg in baseline_route.segments}
    variant_segments = {seg.layer: seg.tools for seg in variant_route.segments}
    for layer in sorted(set(baseline_segments) | set(variant_segments)):
        lhs, rhs = baseline_segments.get(layer, ()), variant_segments.get(layer, ())
        if set(lhs) != set(rhs):
            return (layer, tuple(lhs), tuple(rhs))
    return None