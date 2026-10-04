"""Route families (phase5 §12).

MVP: every route is its own family (``family_id == route_id``), matching the
Phase 4 Route Diversity Guard's granularity. Structural clustering stays a
deliberate non-goal until a second consumer needs it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .profile import RouteProfile


@dataclass(frozen=True, slots=True)
class RouteFamily:
    family_id: str
    route_ids: tuple[str, ...]


def build_families(profiles) -> tuple[RouteFamily, ...]:
    """One singleton family per route, ordered by route_id."""
    return tuple(
        RouteFamily(family_id=profile.route_id, route_ids=(profile.route_id,))
        for profile in sorted(profiles, key=lambda item: item.route_id)
    )
