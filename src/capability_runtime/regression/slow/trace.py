from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ...execution.executor import ToolExecution
from ...router.models import RoutingDecision

__all__ = ["LayerExecution", "ExecutionTrace"]


@dataclass(slots=True)
class LayerExecution:
    """Complete record of one executed layer."""

    layer: str
    available_tools: tuple[str, ...]
    selected_tools: tuple[str, ...]
    routing_decision: RoutingDecision
    tool_executions: tuple[ToolExecution, ...]
    started_at: datetime
    ended_at: datetime


@dataclass(slots=True)
class ExecutionTrace:
    """Per-trial record of every executed layer in order."""

    trial_id: str
    scenario_id: str
    topology_version: str
    layers: tuple[LayerExecution, ...] = ()
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: datetime | None = None

    def add_layer(self, layer: LayerExecution) -> None:
        self.layers = (*self.layers, layer)
        self.ended_at = layer.ended_at