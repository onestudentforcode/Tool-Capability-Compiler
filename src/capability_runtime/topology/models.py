from __future__ import annotations

from dataclasses import dataclass

from ..core.errors import ToolNotFoundError
from ..core.layer import Layer
from ..core.tool import ToolNode


@dataclass(frozen=True, slots=True)
class ToolEdge:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class TopologyValidationWarning:
    code: str
    message: str
    source: str
    target: str


class Topology:
    """The declared maximum search space. It does not choose an execution path."""

    def __init__(
        self,
        layers: tuple[Layer, ...],
        nodes: dict[str, ToolNode],
        edges: tuple[ToolEdge, ...],
        warnings: tuple[TopologyValidationWarning, ...] = (),
    ) -> None:
        self._layers = tuple(sorted(layers))
        self._nodes = dict(nodes)
        self._edges = tuple(sorted(edges, key=lambda edge: (edge.source, edge.target)))
        self._warnings = tuple(
            sorted(warnings, key=lambda item: (item.source, item.target, item.code))
        )
        self._incoming: dict[str, list[ToolEdge]] = {name: [] for name in nodes}
        self._outgoing: dict[str, list[ToolEdge]] = {name: [] for name in nodes}
        for edge in self._edges:
            self._incoming[edge.target].append(edge)
            self._outgoing[edge.source].append(edge)
        # O(1) membership index; route search probes has_edge millions of
        # times on battlefield-sized topologies.
        self._edge_index = frozenset(self._edges)

    def layers(self) -> tuple[Layer, ...]:
        return self._layers

    def nodes(self) -> tuple[str, ...]:
        return tuple(sorted(self._nodes))

    def nodes_in_layer(self, layer: str) -> tuple[str, ...]:
        return tuple(
            sorted(name for name, node in self._nodes.items() if node.spec.layer == layer)
        )

    def node(self, name: str) -> ToolNode:
        try:
            return self._nodes[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Tool not found in topology: {name}") from exc

    def edges(self) -> tuple[ToolEdge, ...]:
        return self._edges

    def warnings(self) -> tuple[TopologyValidationWarning, ...]:
        return self._warnings

    def predecessors(self, tool_name: str) -> tuple[str, ...]:
        self.node(tool_name)
        return tuple(sorted(edge.source for edge in self._incoming[tool_name]))

    def successors(self, tool_name: str) -> tuple[str, ...]:
        self.node(tool_name)
        return tuple(sorted(edge.target for edge in self._outgoing[tool_name]))

    def has_edge(self, source: str, target: str) -> bool:
        return ToolEdge(source, target) in self._edge_index
