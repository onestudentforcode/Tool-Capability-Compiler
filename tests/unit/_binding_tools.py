"""Arg-less async handlers referenced by JSON topology ``implementation`` entry
points in the batch-B binding tests.

Slow regression can execute these without any cross-layer schema wiring: they
take no positional arguments, so argument resolution always succeeds and the
handler's real return value shows up in the trace (proving the bound function
actually ran, unlike the null placeholder).
"""

from __future__ import annotations

from dataclasses import dataclass

from capability_runtime import tool


@dataclass(frozen=True)
class Note:
    text: str


async def fetch() -> Note:
    return Note(text="fetched:ORD-1")


async def analyze() -> Note:
    return Note(text="analyzed:eligible")


# a ``@tool``-decorated attribute: JSON ``implementation`` may point at it —
# the loader unwraps ``ToolNode.handler`` instead of rejecting it
@tool(layer="read", produces=[Note])
async def decorated_fetch() -> Note:
    return Note(text="decorated:ORD-1")
