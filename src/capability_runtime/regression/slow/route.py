from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from ...core.errors import ExecutionError
from .trace import ExecutionTrace


@dataclass(frozen=True, slots=True)
class RouteSegment:
    """One layer's tool set inside an ObservedRoute."""

    layer: str
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservedRoute:
    """The structural path an agent actually walked during a Trial.

    Route captures structure only; execution outcome is left to Evaluation
    ($50). Layer tool sets are ASC-stable so sibling order never changes the id.
    """

    segments: tuple[RouteSegment, ...]

    @classmethod
    def from_layers(
        cls, layers: Sequence[tuple[str, Sequence[str]]]
    ) -> ObservedRoute:
        normalized = tuple(
            RouteSegment(layer=layer, tools=tuple(sorted(tools)))
            for layer, tools in layers
            if tools
        )
        if not normalized:
            raise ExecutionError("cannot build an ObservedRoute without any tools")
        return cls(segments=normalized)

    @property
    def canonical(self) -> str:
        return "\n".join(
            f"{segment.layer}:[{','.join(segment.tools)}]"
            for segment in self.segments
        )

    @property
    def route_id(self) -> str:
        return _route_id(self.canonical)


def _route_id(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def extract_observed_route(trace: ExecutionTrace) -> ObservedRoute:
    """Derive the observed structure from a trace's layer selections ($51)."""
    return ObservedRoute.from_layers(
        (layer.layer, layer.selected_tools) for layer in trace.layers
    )