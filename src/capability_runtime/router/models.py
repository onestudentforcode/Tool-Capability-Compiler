from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..core.errors import (
    ExecutionError,
    InvalidRoutingDecisionError,
    InvalidToolSelectionError,
    RoutingError,
)
from ..core.tool import ToolNode

EXPLORATION_MODES = frozenset({"free", "guided", "replay"})


class RoutingAction(str, Enum):
    EXECUTE = "execute"
    FINISH = "finish"


@dataclass(frozen=True, slots=True)
class ToolSummary:
    """A slice of tool metadata exposed to the router."""

    name: str
    layer: str
    description: str = ""
    capabilities: frozenset[str] = frozenset()

    @classmethod
    def from_tool_node(cls, node: ToolNode) -> ToolSummary:
        return cls(
            name=node.spec.name,
            layer=node.spec.layer,
            description=node.spec.description,
            capabilities=frozenset(node.spec.capabilities),
        )


@dataclass(frozen=True, slots=True)
class RoutingContext:
    """Everything a router may consult to pick tools for one layer."""

    query: str
    current_layer: str
    available_tools: tuple[ToolSummary, ...]
    topology_version: str
    state_summary: Any = None
    # String forward ref: LayerExecution lives in regression.slow.trace to avoid
    # a router.models <-> slow.trace import cycle; record type must line up.
    previous_layers: tuple["LayerExecution", ...] = ()


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    action: RoutingAction
    selected_tools: tuple[str, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, RoutingAction):
            raise RoutingError("action must be a RoutingAction")
        if not isinstance(self.selected_tools, tuple) or not all(
            isinstance(name, str) and name.strip() for name in self.selected_tools
        ):
            raise RoutingError("selected_tools must be a tuple of non-empty strings")
        if len(set(self.selected_tools)) != len(self.selected_tools):
            raise InvalidRoutingDecisionError("selected_tools cannot contain duplicates")


@dataclass(frozen=True, slots=True)
class RouterConfig:
    model: str
    temperature: float
    max_tools_per_layer: int
    exploration_mode: str = "free"
    prompt_version: str = "1"

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise RoutingError("RouterConfig model must be a non-empty string")
        if isinstance(self.temperature, bool) or not (
            0 <= self.temperature <= 2
        ):
            raise RoutingError("RouterConfig temperature must be within [0, 2]")
        if isinstance(self.max_tools_per_layer, bool) or self.max_tools_per_layer < 1:
            raise RoutingError("RouterConfig max_tools_per_layer must be positive")
        if self.exploration_mode not in EXPLORATION_MODES:
            raise RoutingError(
                f"RouterConfig exploration_mode must be one of "
                f"{sorted(EXPLORATION_MODES)}"
            )
        if not isinstance(self.prompt_version, str) or not self.prompt_version.strip():
            raise RoutingError("RouterConfig prompt_version must be a non-empty string")


def validate_decision(
    decision: RoutingDecision,
    available_tool_names: tuple[str, ...],
    max_tools_per_layer: int,
) -> None:
    """Enforce the routing contract the runtime depends on."""
    if decision.action is RoutingAction.FINISH:
        if decision.selected_tools:
            raise InvalidRoutingDecisionError("FINISH cannot carry selected tools")
        return
    if not decision.selected_tools:
        raise InvalidRoutingDecisionError("EXECUTE requires at least one selected tool")
    if len(decision.selected_tools) > max_tools_per_layer:
        raise InvalidRoutingDecisionError(
            f"selected {len(decision.selected_tools)} tools exceeds "
            f"max_tools_per_layer={max_tools_per_layer}"
        )
    known = set(available_tool_names)
    unknown = sorted(set(decision.selected_tools) - known)
    if unknown:
        raise InvalidToolSelectionError(
            f"selected tools not available: {', '.join(unknown)}"
        )


@dataclass(frozen=True, slots=True)
class ExpansionPlan:
    """How one layer is explored atop a fast CandidateRoute seed (basefast).

    baseline_tools is the seed's selection for this layer; candidate_sets are the
    full tool-sets a Trial may run here, starting from the baseline (index 0)
    and one per added sibling. Every set obeys max_tools_per_layer.
    """

    layer: str
    baseline_tools: tuple[str, ...] = ()
    candidate_sets: tuple[tuple[str, ...], ...] = ()

    @property
    def variants(self) -> int:
        return max(0, len(self.candidate_sets) - 1)


def build_expansion_plan(
    *,
    layer: str,
    available_tools: tuple[str, ...],
    seed_tools: tuple[str, ...] = (),
    max_tools_per_layer: int,
) -> ExpansionPlan:
    """Derive the exploration for one layer from a seed selection.

    The baseline is the seed's tools intersected with what is actually
    reachable; each additional sibling of the seed becomes a separate candidate
    set. Sets are sorted and capped at max_tools_per_layer.
    """
    if isinstance(max_tools_per_layer, bool) or max_tools_per_layer < 1:
        raise ExecutionError("max_tools_per_layer must be a positive integer")
    baseline = tuple(sorted(tool for tool in seed_tools if tool in available_tools))
    siblings = tuple(tool for tool in available_tools if tool not in baseline)
    candidates: list[tuple[str, ...]] = [baseline]
    for sibling in siblings:
        extended = tuple(sorted(set(baseline) | {sibling}))[:max_tools_per_layer]
        if extended not in candidates:
            candidates.append(extended)
    return ExpansionPlan(
        layer=layer,
        baseline_tools=baseline,
        candidate_sets=tuple(candidates),
    )