"""Flatten composite executions into inner pseudo-trials (composite-nodes §7).

This is the loop-closing adapter: every iteration of every composite
execution in the outer trials becomes one inner ``TrialResult`` over the
inner topology, so the exact Phase 3/4 machinery (observation stats,
EvidenceAggregator, CandidateDetector, ranking) applies to the inner world
unchanged. Offline analysis only — outer runs never prune inner edges.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..core.errors import CompositeError
from ..core.metrics import TokenUsage
from ..regression.slow.route import ObservedRoute
from ..regression.slow.trace import ExecutionTrace
from ..regression.slow.trial import (
    Trial,
    TrialExecutionStatus,
    TrialResult,
)
from .spec import CompositeSpec


@dataclass(frozen=True, slots=True)
class InnerObservation:
    """One flattened composite iteration, ready for evidence aggregation."""

    result: TrialResult
    composite_name: str
    iteration: int
    stopped: bool


def flatten_composite_results(
    outer_results: Sequence[TrialResult],
    spec_registry: Mapping[str, CompositeSpec],
) -> tuple[TrialResult, ...]:
    """Every composite iteration -> one inner pseudo-trial (§7)."""
    flattened: list[TrialResult] = []
    for outer in outer_results:
        for layer in outer.trace.layers:
            for execution in layer.tool_executions:
                spec = spec_registry.get(execution.tool_name)
                detail = execution.composite_detail
                if spec is None or not detail:
                    continue
                for iteration_index, layers in enumerate(detail):
                    flattened.append(
                        _pseudo_trial(outer, spec, iteration_index, layers)
                    )
    return tuple(flattened)


def _pseudo_trial(
    outer: TrialResult,
    spec: CompositeSpec,
    iteration_index: int,
    layers,
) -> TrialResult:
    route = _route_of(layers)
    latency = sum(
        (layer.ended_at - layer.started_at).total_seconds() * 1000.0
        for layer in layers
    )
    cost = _sum(execution.cost for layer in layers for execution in layer.tool_executions)
    access: dict[str, int] | None = None
    tokens = TokenUsage()
    failed = any(
        execution.status.value == "error"
        and all(
            sibling.status.value == "error"
            for sibling in layer.tool_executions
        )
        for layer in layers
        for execution in layer.tool_executions
    )
    for layer in layers:
        for execution in layer.tool_executions:
            if execution.access_counts:
                if access is None:
                    access = {}
                for key, count in execution.access_counts.items():
                    access[key] = access.get(key, 0) + count
            if execution.token_usage is not None:
                tokens = TokenUsage(
                    input_tokens=tokens.input_tokens
                    + execution.token_usage.input_tokens,
                    output_tokens=tokens.output_tokens
                    + execution.token_usage.output_tokens,
                )
    trial = Trial(
        id=f"{outer.trial.id}:{spec.name}:{iteration_index}",
        scenario_id=outer.trial.scenario_id,
        trial_index=iteration_index,
        topology_version=f"inner:{spec.name}",
        scenario_suite_version=outer.trial.scenario_suite_version,
        router_config_id=outer.trial.router_config_id,
    )
    trace = ExecutionTrace(
        trial_id=trial.id,
        scenario_id=trial.scenario_id,
        topology_version=trial.topology_version,
        layers=tuple(layers),
    )
    return TrialResult(
        trial=trial,
        execution_status=(
            TrialExecutionStatus.LAYER_ERROR if failed else TrialExecutionStatus.COMPLETED
        ),
        route=route,
        trace=trace,
        evaluation=None,
        latency_ms=latency,
        token_usage=tokens,
        cost=cost,
        tool_cost=cost,
        access_counts=access,
    )


def _route_of(layers) -> ObservedRoute | None:
    groups = [
        (layer.layer, layer.selected_tools)
        for layer in layers
        if layer.selected_tools
    ]
    if not groups:
        return None
    return ObservedRoute.from_layers(groups)


def _sum(values) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


__all__ = [
    "InnerObservation",
    "flatten_composite_results",
    "CompositeError",
]
