from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import combinations, product
from typing import AbstractSet

from ..core.capability import validate_capability_name
from ..core.errors import RouteSearchError
from ..route import RouteLayer
from ..topology import ToolEdge, Topology


@dataclass(frozen=True, slots=True)
class CandidateRoute:
    """A theoretical topology-constrained route; it is not an execution plan."""

    layers: tuple[RouteLayer, ...]
    edges: tuple[ToolEdge, ...]
    capabilities: frozenset[str]

    @property
    def fingerprint(self) -> str:
        return "|".join(
            f"{layer.layer}:[{','.join(layer.tools)}]" for layer in self.layers
        )

    @property
    def route_id(self) -> str:
        return hashlib.sha256(self.fingerprint.encode("utf-8")).hexdigest()[:16]

    @property
    def tool_count(self) -> int:
        return sum(len(layer.tools) for layer in self.layers)

    @property
    def layer_count(self) -> int:
        return len(self.layers)

    @property
    def route_depth(self) -> int:
        return max(0, self.layer_count - 1)


class RouteSearcher:
    """Find bounded feasible routes without executing tools or scoring quality."""

    def search(
        self,
        topology: Topology,
        required_capabilities: AbstractSet[str],
        *,
        max_candidate_routes: int = 20,
        max_bridge_depth: int = 2,
        disabled_tools: AbstractSet[str] = frozenset(),
        disabled_edges: Iterable[ToolEdge | tuple[str, str]] = (),
    ) -> tuple[CandidateRoute, ...]:
        if not required_capabilities:
            raise RouteSearchError("Route search requires at least one capability")
        if max_candidate_routes <= 0:
            raise RouteSearchError("max_candidate_routes must be positive")
        if max_bridge_depth < 0:
            raise RouteSearchError("max_bridge_depth cannot be negative")

        required = tuple(
            sorted(
                validate_capability_name(capability)
                for capability in required_capabilities
            )
        )
        enabled_nodes, enabled_edges = self._enabled_search_space(
            topology, disabled_tools, disabled_edges
        )
        providers = {
            capability: tuple(
                name
                for name in enabled_nodes
                if capability in topology.node(name).spec.capabilities
            )
            for capability in required
        }
        if any(not names for names in providers.values()):
            return ()

        layer_order = {layer.name: layer.order for layer in topology.layers()}
        nodes_by_order = {
            layer.order: tuple(
                name
                for name in enabled_nodes
                if topology.node(name).spec.layer == layer.name
            )
            for layer in topology.layers()
        }

        routes: dict[str, CandidateRoute] = {}
        provider_choices = (providers[capability] for capability in required)
        for assignment in product(*provider_choices):
            terminals = frozenset(assignment)
            minimal_tool_sets: list[frozenset[str]] = []
            for route in self._routes_for_terminals(
                topology=topology,
                terminals=terminals,
                required=frozenset(required),
                layer_order=layer_order,
                nodes_by_order=nodes_by_order,
                enabled_edges=enabled_edges,
                max_bridge_depth=max_bridge_depth,
            ):
                route_tools = frozenset(
                    name for layer in route.layers for name in layer.tools
                )
                if any(existing < route_tools for existing in minimal_tool_sets):
                    continue
                minimal_tool_sets = [
                    existing
                    for existing in minimal_tool_sets
                    if not route_tools < existing
                ]
                minimal_tool_sets.append(route_tools)
                routes.setdefault(route.fingerprint, route)
                if len(routes) >= max_candidate_routes:
                    return tuple(routes[key] for key in sorted(routes))
        return tuple(routes[key] for key in sorted(routes))

    def _routes_for_terminals(
        self,
        *,
        topology: Topology,
        terminals: frozenset[str],
        required: frozenset[str],
        layer_order: dict[str, int],
        nodes_by_order: dict[int, tuple[str, ...]],
        enabled_edges: frozenset[tuple[str, str]],
        max_bridge_depth: int,
    ) -> Iterator[CandidateRoute]:
        terminal_orders: dict[int, tuple[str, ...]] = {}
        for name in sorted(terminals):
            order = layer_order[topology.node(name).spec.layer]
            terminal_orders.setdefault(order, ())
            terminal_orders[order] = (*terminal_orders[order], name)

        ordered_terminal_layers = sorted(terminal_orders)
        if any(
            right - left - 1 > max_bridge_depth
            for left, right in zip(
                ordered_terminal_layers,
                ordered_terminal_layers[1:],
                strict=False,
            )
        ):
            return

        first = ordered_terminal_layers[0]
        last = ordered_terminal_layers[-1]
        options: list[tuple[tuple[str, ...], ...]] = []
        for order in range(first, last + 1):
            mandatory = terminal_orders.get(order)
            if mandatory:
                options.append((mandatory,))
                continue
            bridge_nodes = nodes_by_order.get(order, ())
            options.append(tuple(self._non_empty_subsets(bridge_nodes)))
            if not options[-1]:
                return

        groups: list[tuple[str, ...]] = []

        def walk(index: int) -> Iterator[CandidateRoute]:
            if index == len(options):
                selected = {name for group in groups for name in group}
                capabilities = frozenset(
                    capability
                    for name in selected
                    for capability in topology.node(name).spec.capabilities
                )
                if not required.issubset(capabilities):
                    return
                route_layers = tuple(
                    RouteLayer(
                        layer=topology.node(group[0]).spec.layer,
                        tools=group,
                    )
                    for group in groups
                )
                edges = tuple(
                    ToolEdge(source, target)
                    for left, right in zip(groups, groups[1:], strict=False)
                    for source in left
                    for target in right
                    if (source, target) in enabled_edges
                )
                yield CandidateRoute(route_layers, edges, capabilities)
                return

            for group in options[index]:
                if groups and not self._groups_connect(
                    groups[-1], group, enabled_edges
                ):
                    continue
                groups.append(group)
                yield from walk(index + 1)
                groups.pop()

        yield from walk(0)

    @staticmethod
    def _groups_connect(
        sources: tuple[str, ...],
        targets: tuple[str, ...],
        enabled_edges: frozenset[tuple[str, str]],
    ) -> bool:
        return all(
            any((source, target) in enabled_edges for target in targets)
            for source in sources
        ) and all(
            any((source, target) in enabled_edges for source in sources)
            for target in targets
        )

    @staticmethod
    def _non_empty_subsets(nodes: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
        for size in range(1, len(nodes) + 1):
            yield from combinations(nodes, size)

    @staticmethod
    def _enabled_search_space(
        topology: Topology,
        disabled_tools: AbstractSet[str],
        disabled_edges: Iterable[ToolEdge | tuple[str, str]],
    ) -> tuple[tuple[str, ...], frozenset[tuple[str, str]]]:
        unknown_tools = sorted(set(disabled_tools) - set(topology.nodes()))
        if unknown_tools:
            raise RouteSearchError(
                f"Unknown disabled tools: {', '.join(unknown_tools)}"
            )

        disabled_pairs: set[tuple[str, str]] = set()
        for edge in disabled_edges:
            pair = (edge.source, edge.target) if isinstance(edge, ToolEdge) else edge
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not all(isinstance(name, str) for name in pair)
            ):
                raise RouteSearchError(f"Invalid disabled edge: {edge!r}")
            if not topology.has_edge(*pair):
                raise RouteSearchError(
                    f"Disabled edge does not exist: {pair[0]} -> {pair[1]}"
                )
            disabled_pairs.add(pair)

        enabled_nodes = tuple(
            name for name in topology.nodes() if name not in disabled_tools
        )
        enabled_set = set(enabled_nodes)
        enabled_edges = frozenset(
            (edge.source, edge.target)
            for edge in topology.edges()
            if edge.source in enabled_set
            and edge.target in enabled_set
            and (edge.source, edge.target) not in disabled_pairs
        )
        return enabled_nodes, enabled_edges
