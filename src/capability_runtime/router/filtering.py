from __future__ import annotations

from typing import Any

from ..core.tool import ToolNode
from ..execution.state import ExecutionState
from ..topology.models import Topology


class TopologyFilter:
    """Compute the tools available to a router at a given layer.

    A tool is available when it is reachable from the previous layer's
    selection (OR reachability), its provider/worker allow-lists admit the
    arriving edge, and its declared `consumes` inputs are satisfiable from
    the current state.
    """

    def __init__(self, topology: Topology) -> None:
        self._topology = topology

    def available_tools(
        self,
        current_layer: str,
        previous_selected_tools: tuple[str, ...] = (),
        state: ExecutionState | None = None,
    ) -> tuple[str, ...]:
        candidates = self._topology.nodes_in_layer(current_layer)
        available: list[str] = []
        for name in candidates:
            tool = self._topology.node(name)
            if not self._schema_satisfiable(tool, state):
                continue
            if not previous_selected_tools:
                # entry layer: nothing was routed before, source tools are eligible
                available.append(name)
            elif any(self._reachable_from(prev, name) for prev in previous_selected_tools):
                available.append(name)
        return tuple(sorted(available))

    def _reachable_from(self, source: str, target: str) -> bool:
        source_node = self._topology.node(source)
        if not source_node.spec.workers.allows(target):
            return False
        target_node = self._topology.node(target)
        if not target_node.spec.providers.allows(source):
            return False
        return self._topology.has_edge(source, target)

    @staticmethod
    def _schema_satisfiable(tool: ToolNode, state: ExecutionState | None) -> bool:
        required = tool.spec.consumes
        if not required or Any in required:
            return True
        if state is None:
            # no state to inspect: no evidence a required input is missing
            return True
        values = [a.value for name in state.names() for a in state.get_artifacts(name)]
        return all(any(isinstance(value, expected) for value in values) for expected in required)