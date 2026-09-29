"""Request shape, serving config and tier-order resolution (phase6 §6)."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.errors import RouteSelectionError
from .catalog import RouteCatalog, RouteEntry

_VALID_TIERS = ("fast", "balanced", "quality")


@dataclass(frozen=True, slots=True)
class OnlineConfig:
    tier_priority: tuple[str, ...] = ("fast", "balanced", "quality")
    allow_global_fallback: bool = False
    max_fallbacks: int = 1

    def __post_init__(self) -> None:
        if not self.tier_priority or any(
            tier not in _VALID_TIERS for tier in self.tier_priority
        ):
            raise RouteSelectionError(
                f"tier_priority must be a permutation of {_VALID_TIERS}"
            )
        if len(set(self.tier_priority)) != len(self.tier_priority):
            raise RouteSelectionError("tier_priority cannot repeat a tier")
        if isinstance(self.max_fallbacks, bool) or self.max_fallbacks < 0:
            raise RouteSelectionError("max_fallbacks must be a non-negative int")


@dataclass(frozen=True, slots=True)
class OnlineRequest:
    query: str
    category: str | None = None
    tier: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise RouteSelectionError("OnlineRequest query must be non-empty")
        if self.tier is not None and self.tier not in _VALID_TIERS:
            raise RouteSelectionError(
                f"unknown tier {self.tier!r}; expected one of {_VALID_TIERS}"
            )


def resolve_tier_order(request: OnlineRequest, config: OnlineConfig) -> tuple[str, ...]:
    """Request preference leads; the configured priority chain follows."""
    order: list[str] = []
    if request.tier is not None:
        order.append(request.tier)
    for tier in config.tier_priority:
        if tier not in order:
            order.append(tier)
    return tuple(order)


def candidate_groups(
    catalog: RouteCatalog,
    request: OnlineRequest,
    config: OnlineConfig,
) -> tuple[tuple[str, tuple[RouteEntry, ...]], ...]:
    """Ordered (tier, entries) groups, empty groups dropped.

    Fail closed: a category with no ranked coverage is an error, not a
    silent global fallback (unless ``allow_global_fallback`` is set, in
    which case unfiltered groups are appended last).
    """
    tier_order = resolve_tier_order(request, config)
    groups = [
        (tier, catalog.candidates(category=request.category, tier=tier))
        for tier in tier_order
    ]
    # ranked-but-unlabelled routes are the last resort: they carry evidence,
    # just no tier label, and must not leave a covered category unservable
    groups.append(
        (
            "unassigned",
            tuple(
                entry
                for entry in catalog.candidates(
                    category=request.category, tier=None
                )
                if not entry.tiers
            ),
        )
    )
    if not any(entries for _tier, entries in groups):
        if request.category is not None and config.allow_global_fallback:
            groups.append(
                ("*", catalog.candidates(category=None, tier=request.category))
            )
            groups.append(("*", catalog.candidates(category=None, tier=None)))
        if not any(entries for _tier, entries in groups):
            raise RouteSelectionError(
                f"no ranked route covers category={request.category!r} "
                f"with tier preference {list(tier_order)} "
                f"(catalog has {len(catalog.entries)} ranked routes)"
            )
    return tuple(
        (tier, entries) for tier, entries in groups if entries
    )
