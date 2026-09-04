from __future__ import annotations

from typing import Protocol

from .models import RoutingContext, RoutingDecision


class LayerRouter(Protocol):
    """Decides which tools to run for one layer. Must never call tools itself."""

    async def route(self, context: RoutingContext) -> RoutingDecision: ...