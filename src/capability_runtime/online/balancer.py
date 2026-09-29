"""Same-tier load balancing (phase6 §7).

MVP is deterministic round-robin over the route_id-sorted candidates. The
balancer holds per-catalog state only; fallback picks never consume a
rotation slot.
"""

from __future__ import annotations

from typing import Protocol

from ..core.errors import RouteSelectionError
from .catalog import RouteEntry


class LoadBalancer(Protocol):
    def pick(self, candidates: tuple[RouteEntry, ...]) -> RouteEntry: ...


class RoundRobinBalancer:
    """Deterministic rotation; identical state produces identical picks."""

    def __init__(self) -> None:
        self._served = 0

    @property
    def served(self) -> int:
        return self._served

    def pick(self, candidates: tuple[RouteEntry, ...]) -> RouteEntry:
        if not candidates:
            raise RouteSelectionError("cannot balance an empty candidate set")
        ordered = sorted(candidates, key=lambda item: item.route_id)
        chosen = ordered[self._served % len(ordered)]
        self._served += 1
        return chosen
