from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..route.models import RouteLayer


@dataclass(frozen=True, slots=True)
class CandidateRoute:
    """A theoretical tool combination found from the declared topology.

    This is NOT an execution plan. It is a candidate that ``CoverageAnalyzer``
    returns to show that at least one valid route exists for a scenario.

    Layers are normalized so that tools within each layer are sorted ASC,
    ensuring ``route_id`` is stable regardless of construction order.
    """

    layers: tuple[RouteLayer, ...]
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "layers",
            tuple(
                RouteLayer(layer.layer, tuple(sorted(layer.tools)))
                for layer in self.layers
            ),
        )

    @property
    def route_id(self) -> str:
        """Stable fingerprint: layer tools sorted ASC, SHA-256 truncated."""
        normalized = "|".join(
            f"{layer.layer}:[{','.join(layer.tools)}]" for layer in self.layers
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
