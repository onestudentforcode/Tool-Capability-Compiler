"""Bounded, de-duplicated fallback chains (phase6 §9).

The chain is the initial group's remainder plus all later groups, de-duped
by route_id and truncated to ``max_fallbacks``. A route already attempted in
this request never reappears; exhausted chains surface as a complete,
replayable history on the result.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import RouteEntry


@dataclass(frozen=True, slots=True)
class FallbackStep:
    from_route_id: str
    to_route_id: str
    cause: str


def build_fallback_chain(
    tier_groups,
    initial: RouteEntry,
    *,
    max_fallbacks: int,
) -> tuple[RouteEntry, ...]:
    """Candidates to try after ``initial`` fails, in deterministic order.

    ``tier_groups`` is the ordered ``(tier, entries)`` sequence from
    :func:`candidate_groups`; the chain is the initial group's remainder
    plus all later groups, de-duped by route_id and truncated.
    """
    if max_fallbacks <= 0:
        return ()
    seen = {initial.route_id}
    chain: list[RouteEntry] = []
    for _tier, entries in tier_groups:
        for entry in sorted(entries, key=lambda item: item.route_id):
            if entry.route_id in seen:
                continue
            seen.add(entry.route_id)
            chain.append(entry)
            if len(chain) >= max_fallbacks:
                return tuple(chain)
    return tuple(chain)
