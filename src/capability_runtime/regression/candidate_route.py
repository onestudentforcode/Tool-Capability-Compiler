from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..route.models import RouteLayer
from ..topology.models import ToolEdge


@dataclass(frozen=True, slots=True)
class CandidateRoute:
    """A static, topology-valid tool combination, not an execution plan."""

    layers: tuple[RouteLayer, ...]
    capabilities: frozenset[str]
    edges: tuple[ToolEdge, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "layers", tuple(
            RouteLayer(layer.layer, tuple(sorted(layer.tools))) for layer in self.layers
        ))
        object.__setattr__(self, "edges", tuple(
            sorted(self.edges, key=lambda edge: (edge.source, edge.target))
        ))

    @property
    def fingerprint(self) -> str:
        return "|".join(
            f"{layer.layer}:[{','.join(layer.tools)}]" for layer in self.layers
        )

    @property
    def route_id(self) -> str:
        return hashlib.sha256(self.fingerprint.encode("utf-8")).hexdigest()[:16]

    @property
    def tool_count(self) -> int:
        return sum(len(layer.tools) for layer in self.layers)

    @property
    def layer_count(self) -> int:
        return len(self.layers)

    @property
    def route_depth(self) -> int:
        return max(0, self.layer_count - 1)
