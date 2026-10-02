from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..core.errors import InvalidToolSelectionError
from .models import RoutingAction, RoutingContext, RoutingDecision


@dataclass(slots=True)
class FakeRouter:
    """Deterministic LayerRouter for tests and reproducible exploration.

    A per-layer plan selects exactly the named tools; an empty plan at a layer
    means FINISH. Layers without a plan fall back to the first `default_max_tools`
    available tools in sorted order.
    """

    layer_selections: Mapping[str, Sequence[str]] = field(default_factory=dict)
    default_max_tools: int = 1

    async def route(self, context: RoutingContext) -> RoutingDecision:
        available = tuple(summary.name for summary in context.available_tools)
        planned = self.layer_selections.get(context.current_layer)
        if planned is None:
            selected = tuple(sorted(available)[: max(1, self.default_max_tools)])
            action = RoutingAction.EXECUTE if selected else RoutingAction.FINISH
            return RoutingDecision(action=action, selected_tools=selected)
        if not planned:
            return RoutingDecision(
                action=RoutingAction.FINISH, selected_tools=(), reason="fake finish"
            )
        selected = tuple(planned)
        unknown = sorted(set(selected) - set(available))
        if unknown:
            raise InvalidToolSelectionError(
                f"FakeRouter selected tools not available: {', '.join(unknown)}"
            )
        return RoutingDecision(action=RoutingAction.EXECUTE, selected_tools=selected)


@dataclass(slots=True)
class ScenarioScriptedRouter:
    """Per-scenario scripted router for offline deterministic discovery.

    Binds ONE scenario's table (``{layer: [tools]}``); a layer without an
    entry means FINISH, an entry with tools means EXECUTE exactly those
    tools (the runner validates them against the available pool, so pool-
    illegal scripts surface as ROUTING_ERROR — which discovery records).
    Used by ``seeds discover --scripted-router`` and offline tests; the
    discovery flow replays every discovered chain deterministically before
    freezing, so scripted output is treated exactly like model output.
    """

    routing: Mapping[str, Sequence[str]] = field(default_factory=dict)

    async def route(self, context: RoutingContext) -> RoutingDecision:
        planned = self.routing.get(context.current_layer)
        if planned is None:
            return RoutingDecision(
                action=RoutingAction.FINISH,
                reason="scripted: layer not listed",
            )
        selected = tuple(dict.fromkeys(str(tool) for tool in planned))
        if not selected:
            return RoutingDecision(
                action=RoutingAction.FINISH,
                reason="scripted: empty plan",
            )
        return RoutingDecision(action=RoutingAction.EXECUTE, selected_tools=selected)