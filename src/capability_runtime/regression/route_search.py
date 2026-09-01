from __future__ import annotations

import itertools
from collections import deque

from ..registry.capability_registry import CapabilityRegistry
from ..route.models import RouteLayer
from ..topology.models import Topology
from .candidate_route import CandidateRoute


class RouteSearch:
    """Capability-guided Layer Search.

    Finds candidate routes through a layered topology that cover a set of
    required capabilities. The search is purely static metadata analysis:
    it does not execute tools, call any LLM, or modify the topology.

    Note on constraints from phase2.md Section 28:
    - Constraints 5 (no disabled edge) and 6 (no disabled tool) are satisfied
      by construction in Phase 2. The Topology model does not yet support
      disabled edges/tools (that is a Phase 4 concern). We only traverse
      edges and tools that exist in the declared topology.
    """

    def __init__(
        self,
        topology: Topology,
        capability_registry: CapabilityRegistry,
        max_candidate_routes: int = 20,
        max_bridge_depth: int = 2,
    ) -> None:
        self._topology = topology
        self._capability_registry = capability_registry
        self._max_candidate_routes = max_candidate_routes
        self._max_bridge_depth = max_bridge_depth
        self._layer_order = {layer.name: layer.order for layer in topology.layers()}
        self._order_to_layer = {
            layer.order: layer.name for layer in topology.layers()
        }

    def search(
        self,
        required_capabilities: tuple[str, ...],
    ) -> tuple[CandidateRoute, ...]:
        """Return up to ``max_candidate_routes`` candidate routes covering all capabilities."""
        if not required_capabilities:
            return ()

        provider_map, layer_of = self._index()

        relevant = sorted(
            {
                tool
                for capability in required_capabilities
                for tool in provider_map.get(capability, ())
            }
        )
        if not relevant:
            return ()

        relevant_orders = sorted({layer_of[t] for t in relevant})
        required_set = set(required_capabilities)
        candidates: list[CandidateRoute] = []

        for start_idx, start_order in enumerate(relevant_orders):
            for end_order in relevant_orders[start_idx:]:
                if not self._span_bridge_depth_ok(relevant_orders, start_order, end_order):
                    continue
                self._enumerate_in_span(
                    required_capabilities=required_set,
                    relevant_orders=relevant_orders,
                    start_order=start_order,
                    end_order=end_order,
                    layer_of=layer_of,
                    candidates=candidates,
                )
                if len(candidates) >= self._max_candidate_routes:
                    break
            if len(candidates) >= self._max_candidate_routes:
                break

        return self._deduplicate_and_limit(candidates)

    def _index(self) -> tuple[dict[str, list[str]], dict[str, int]]:
        provider_map: dict[str, list[str]] = {}
        for capability in self._capability_registry.all():
            provider_map[capability] = list(
                self._capability_registry.providers(capability)
            )
        layer_of = {
            name: self._layer_order[self._topology.node(name).spec.layer]
            for name in self._topology.nodes()
        }
        return provider_map, layer_of

    def _span_bridge_depth_ok(
        self,
        relevant_orders: list[int],
        start_order: int,
        end_order: int,
    ) -> bool:
        """Check that no gap between consecutive relevant layers exceeds ``max_bridge_depth``."""
        span_orders = [o for o in relevant_orders if start_order <= o <= end_order]
        for left, right in zip(span_orders, span_orders[1:]):
            bridge_layers = right - left - 1
            if bridge_layers > self._max_bridge_depth:
                return False
        return True

    def _enumerate_in_span(
        self,
        required_capabilities: set[str],
        relevant_orders: list[int],
        start_order: int,
        end_order: int,
        layer_of: dict[str, int],
        candidates: list[CandidateRoute],
    ) -> None:
        all_orders_in_span = sorted(
            o for o in self._layer_order.values() if start_order <= o <= end_order
        )

        layer_candidates: list[tuple[str, list[str]]] = []
        for order in all_orders_in_span:
            layer_name = self._order_to_layer[order]
            relevant_here = sorted(
                t
                for t in self._topology.nodes_in_layer(layer_name)
                if any(
                    cap in required_capabilities
                    for cap in self._topology.node(t).spec.capabilities
                )
            )
            bridge_here = sorted(
                self._bridge_tools(order, start_order, end_order, layer_of)
            )
            tools = sorted(set(relevant_here) | set(bridge_here))
            if not tools:
                return
            layer_candidates.append((layer_name, tools))

        self._enumerate_subsets(
            layer_candidates=layer_candidates,
            layer_index=0,
            current_groups=[],
            required_capabilities=required_capabilities,
            candidates=candidates,
        )

    def _bridge_tools(
        self,
        order: int,
        start_order: int,
        end_order: int,
        layer_of: dict[str, int],
    ) -> list[str]:
        """Find tools in intermediate layers that connect forward and backward."""
        if order == start_order or order == end_order:
            return []
        layer_name = self._order_to_layer[order]
        tools = list(self._topology.nodes_in_layer(layer_name))
        return sorted(
            tool
            for tool in tools
            if any(layer_of.get(pred, -1) >= start_order for pred in self._topology.predecessors(tool))
            and any(layer_of.get(succ, -1) <= end_order for succ in self._topology.successors(tool))
        )

    def _enumerate_subsets(
        self,
        layer_candidates: list[tuple[str, list[str]]],
        layer_index: int,
        current_groups: list[tuple[str, tuple[str, ...]]],
        required_capabilities: set[str],
        candidates: list[CandidateRoute],
    ) -> None:
        if len(candidates) >= self._max_candidate_routes:
            return
        if layer_index == len(layer_candidates):
            if current_groups:
                self._try_add_route(current_groups, required_capabilities, candidates)
            return

        layer_name, tools = layer_candidates[layer_index]
        previous_tools = current_groups[-1][1] if current_groups else None

        for size in range(1, len(tools) + 1):
            for subset in itertools.combinations(tools, size):
                if previous_tools is not None and not self._is_interconnected(
                    previous_tools, subset
                ):
                    continue
                current_groups.append((layer_name, subset))
                self._enumerate_subsets(
                    layer_candidates,
                    layer_index + 1,
                    current_groups,
                    required_capabilities,
                    candidates,
                )
                current_groups.pop()
                if len(candidates) >= self._max_candidate_routes:
                    return

    def _is_interconnected(
        self,
        previous: tuple[str, ...],
        current: tuple[str, ...],
    ) -> bool:
        """Mirror the connectivity rule in ``RoutePlan.from_groups``.

        Every tool in ``previous`` must have an edge to at least one tool in
        ``current``, and every tool in ``current`` must have an edge from at
        least one tool in ``previous``.
        """
        return all(
            any(self._topology.has_edge(prev, cur) for cur in current)
            for prev in previous
        ) and all(
            any(self._topology.has_edge(prev, cur) for prev in previous)
            for cur in current
        )

    def _try_add_route(
        self,
        groups: list[tuple[str, tuple[str, ...]]],
        required_capabilities: set[str],
        candidates: list[CandidateRoute],
    ) -> None:
        if len(candidates) >= self._max_candidate_routes:
            return

        all_capabilities: set[str] = set()
        for _, tools in groups:
            for tool in tools:
                all_capabilities.update(self._topology.node(tool).spec.capabilities)

        if not required_capabilities.issubset(all_capabilities):
            return

        route = CandidateRoute(
            layers=tuple(RouteLayer(name, tools) for name, tools in groups),
            capabilities=frozenset(all_capabilities),
        )
        if route.route_id not in {c.route_id for c in candidates}:
            candidates.append(route)

    def _deduplicate_and_limit(
        self, candidates: list[CandidateRoute]
    ) -> tuple[CandidateRoute, ...]:
        candidates.sort(key=lambda r: (len(r.layers), r.route_id))
        return tuple(candidates[: self._max_candidate_routes])
