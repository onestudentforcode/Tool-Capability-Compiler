from __future__ import annotations

import itertools
from collections.abc import Iterable
from typing import AbstractSet, Any

from ..core.capability import validate_capability_name
from ..core.errors import RouteSearchError
from ..registry.capability_registry import CapabilityRegistry
from ..route.models import RouteLayer
from ..topology.models import ToolEdge, Topology
from .candidate_route import CandidateRoute

# Minimum budget slice one anchor's superset exploration gets before the
# round-robin moves on (routesearch-assignment batch B calibration).
_ANCHOR_SLICE = 2_000


class RouteSearch:
    """Exact feasibility plus assignment-anchored candidate enumeration.

    Generator (routesearch-assignment milestone): every legal selection
    contains at least one capability assignment (a map from each required
    capability to one of its providers), so the enumeration anchors each
    DFS on an assignment's forced tools and explores supersets of them.
    Completeness follows: the union of anchored regions is the space of
    all legal selections. Three prunings keep the budget on high-value
    branches: eligibility (a tool without a backward edge from the fixed
    left selection can never join), suffix coverage (missing capabilities
    must remain coverable by later layers), and a nogood memo on exact
    ``(layer, covered, selection)`` states. ``_interconnected`` stays the
    final acceptance predicate — nothing bypasses it.
    """

    def __init__(
        self,
        topology: Topology,
        capability_registry: CapabilityRegistry,
        max_candidate_routes: int = 20,
        max_expansions: int = 100_000,
    ) -> None:
        if max_candidate_routes <= 0:
            raise RouteSearchError("max_candidate_routes must be positive")
        if max_expansions <= 0:
            raise RouteSearchError("max_expansions must be positive")
        self._topology = topology
        self._registry = capability_registry
        self._limit = max_candidate_routes
        self._max_expansions = max_expansions

    def search(
        self, required_capabilities: tuple[str, ...]
    ) -> tuple[CandidateRoute, ...]:
        required = frozenset(
            validate_capability_name(capability)
            for capability in required_capabilities
        )
        if not required:
            return ()
        cap_of = self._capabilities_by_tool()
        witness = self._feasible_witness(required, cap_of)
        if witness is None:
            return ()

        routes: dict[str, CandidateRoute] = {}
        # The budget counts every anchor materialization and every subset
        # examination, screened or not. Feasibility never depends on it
        # (the witness below); only candidate diversity does.
        budget = [0]
        names = [layer.name for layer in self._topology.layers()]
        for start in range(len(names)):
            for end in range(start, len(names)):
                span = names[start : end + 1]
                maximal = self._max_chain(span)
                if any(not maximal[name] for name in span):
                    continue
                available = {
                    capability
                    for name in span
                    for tool in maximal[name]
                    for capability in cap_of[tool]
                }
                if not required.issubset(available):
                    continue
                groups = [
                    (name, tuple(sorted(maximal[name]))) for name in span
                ]
                # Round-robin anchors with budget slices: one anchor's wide
                # superset enumeration must not starve the provider
                # diversity of the others (the actual point of anchoring).
                for forced in self._anchors(groups, required, cap_of, budget):
                    remaining = self._max_expansions - budget[0]
                    if remaining <= 0:
                        break
                    slice_cap = budget[0] + max(_ANCHOR_SLICE, remaining // 8)
                    self._complete(
                        groups, 0, [], frozenset(), forced, required,
                        cap_of, routes, budget, slice_cap,
                    )
                    if (
                        len(routes) >= self._limit
                        or budget[0] >= self._max_expansions
                    ):
                        break
                if len(routes) >= self._limit or budget[0] >= self._max_expansions:
                    break
            if len(routes) >= self._limit or budget[0] >= self._max_expansions:
                break

        # Feasibility must not depend on the enumeration budget.
        if not routes:
            span, maximal = witness
            route = self._make_route(
                [(name, tuple(sorted(maximal[name]))) for name in span], cap_of
            )
            routes[route.fingerprint] = route
        ordered = sorted(
            routes.values(), key=lambda route: (route.tool_count, route.fingerprint)
        )
        return tuple(ordered[: self._limit])

    def is_feasible(self, required_capabilities: tuple[str, ...]) -> bool:
        required = frozenset(required_capabilities)
        return bool(required) and self._feasible_witness(
            required, self._capabilities_by_tool()
        ) is not None

    def covered_capabilities(
        self, required_capabilities: tuple[str, ...]
    ) -> tuple[str, ...]:
        required = set(required_capabilities)
        cap_of = self._capabilities_by_tool()
        best: set[str] = set()
        names = [layer.name for layer in self._topology.layers()]
        for start in range(len(names)):
            for end in range(start, len(names)):
                span = names[start : end + 1]
                maximal = self._max_chain(span)
                covered = {
                    capability
                    for name in span
                    for tool in maximal[name]
                    for capability in cap_of[tool]
                } & required
                if len(covered) > len(best) or (
                    len(covered) == len(best)
                    and tuple(sorted(covered)) < tuple(sorted(best))
                ):
                    best = covered
        return tuple(sorted(best))

    def _capabilities_by_tool(self) -> dict[str, frozenset[str]]:
        return {
            name: self._topology.node(name).spec.capabilities
            for name in self._topology.nodes()
        }

    def _max_chain(self, span: list[str]) -> dict[str, set[str]]:
        """Compute the maximal legal chain by monotone deletion."""
        surviving = {
            name: set(self._topology.nodes_in_layer(name)) for name in span
        }
        changed = True
        while changed:
            changed = False
            for left, right in zip(span, span[1:]):
                keep_right = {
                    target
                    for target in surviving[right]
                    if any(
                        self._topology.has_edge(source, target)
                        for source in surviving[left]
                    )
                }
                keep_left = {
                    source
                    for source in surviving[left]
                    if any(
                        self._topology.has_edge(source, target)
                        for target in keep_right
                    )
                }
                if keep_left != surviving[left] or keep_right != surviving[right]:
                    surviving[left], surviving[right] = keep_left, keep_right
                    changed = True
        return surviving

    def _feasible_witness(
        self,
        required: frozenset[str],
        cap_of: dict[str, frozenset[str]],
    ) -> tuple[list[str], dict[str, set[str]]] | None:
        names = [layer.name for layer in self._topology.layers()]
        for start in range(len(names)):
            for end in range(start, len(names)):
                span = names[start : end + 1]
                maximal = self._max_chain(span)
                covered = {
                    capability
                    for name in span
                    for tool in maximal[name]
                    for capability in cap_of[tool]
                }
                if required.issubset(covered):
                    return span, maximal
        return None

    def _anchors(
        self,
        groups: list[tuple[str, tuple[str, ...]]],
        required: frozenset[str],
        cap_of: dict[str, frozenset[str]],
        budget: list[int],
    ):
        """Lazily yield forced-tool maps, one per capability assignment.

        An assignment picks one provider per required capability; the union
        of all anchored regions is the full selection space (completeness).
        Anchors whose forced map is a layer-wise superset of an
        already-yielded anchor explore a subset of its region and are
        skipped. Providers are tried multi-capability-first so small forced
        maps (and their minimal routes) surface early; the order is total
        and deterministic.
        """
        layer_of: dict[str, int] = {}
        for index, (_, tools) in enumerate(groups):
            for tool in tools:
                layer_of[tool] = index
        providers: dict[str, tuple[str, ...]] = {}
        for capability in sorted(required):
            found = [
                tool
                for _, tools in groups
                for tool in tools
                if capability in cap_of[tool]
            ]
            if not found:
                return
            found.sort(
                key=lambda tool: (-len(cap_of[tool] & required), tool)
            )
            providers[capability] = tuple(found)

        caps = sorted(required)
        yielded: list[tuple[frozenset[str], ...]] = []

        def walk(index: int, forced: list[set[str]]):
            if budget[0] >= self._max_expansions:
                return
            if index == len(caps):
                budget[0] += 1
                candidate = tuple(frozenset(entry) for entry in forced)
                for existing in yielded:
                    if all(
                        existing[i] <= candidate[i]
                        for i in range(len(groups))
                    ):
                        return  # subsumed: its region is already covered
                yielded.append(candidate)
                yield candidate
                return
            for tool in providers[caps[index]]:
                forced[layer_of[tool]].add(tool)
                yield from walk(index + 1, forced)
                forced[layer_of[tool]].discard(tool)

        yield from walk(0, [set() for _ in groups])

    def _complete(
        self,
        groups: list[tuple[str, tuple[str, ...]]],
        index: int,
        selected: list[tuple[str, tuple[str, ...]]],
        covered: frozenset[str],
        forced: tuple[frozenset[str], ...],
        required: frozenset[str],
        cap_of: dict[str, frozenset[str]],
        routes: dict[str, CandidateRoute],
        budget: list[int],
        slice_cap: int,
    ) -> None:
        if len(routes) >= self._limit or budget[0] >= slice_cap:
            return
        if index == len(groups):
            if required.issubset(covered):
                self._pareto_insert(
                    routes, self._make_route(selected, cap_of)
                )
            return
        layer, tools = groups[index]
        must = forced[index]
        left = selected[-1][1] if selected else None
        # A forced tool without a backward edge from the fixed left selection
        # can never join a legal selection on this branch.
        if left is not None and any(
            not any(self._topology.has_edge(source, tool) for source in left)
            for tool in must
        ):
            return
        pool = [
            tool
            for tool in tools
            if tool not in must
            and (
                left is None
                or any(
                    self._topology.has_edge(source, tool) for source in left
                )
            )
        ]
        suffix = {
            capability
            for _, later_tools in groups[index + 1 :]
            for tool in later_tools
            for capability in cap_of[tool]
        }
        # Value ordering: pool members that advance coverage come first, so
        # the budget meets high-value supersets before lexicographic noise.
        pool.sort(
            key=lambda tool: (
                -len((cap_of[tool] & required) - covered),
                tool,
            )
        )
        # Every layer of a route selects at least one tool; when the anchor
        # pins none here, the empty extra set is not a selection.
        for size in range(0 if must else 1, len(pool) + 1):
            for extra in itertools.combinations(pool, size):
                # The budget bounds every subset examination, screened or
                # not. Feasibility does not depend on it (witness above).
                budget[0] += 1
                if (
                    len(routes) >= self._limit
                    or budget[0] >= slice_cap
                ):
                    return
                subset = tuple(sorted(must.union(extra)))
                if left is not None and not self._interconnected(left, subset):
                    continue
                new_covered = covered.union(
                    capability for tool in subset for capability in cap_of[tool]
                )
                missing = required - new_covered
                if missing and not missing.issubset(suffix):
                    continue
                selected.append((layer, subset))
                self._complete(
                    groups, index + 1, selected, new_covered, forced,
                    required, cap_of, routes, budget, slice_cap,
                )
                selected.pop()

    def _pareto_insert(
        self, routes: dict[str, CandidateRoute], route: CandidateRoute
    ) -> None:
        """Keep the tool-set antichain: supersets drop out, subsets evict."""
        route_tools = self._route_tools(route)
        if any(
            self._route_tools(old) < route_tools
            for old in routes.values()
        ):
            return
        for key, old in tuple(routes.items()):
            if route_tools < self._route_tools(old):
                del routes[key]
        routes[route.fingerprint] = route

    def _interconnected(
        self, left: tuple[str, ...], right: tuple[str, ...]
    ) -> bool:
        return all(
            any(self._topology.has_edge(source, target) for target in right)
            for source in left
        ) and all(
            any(self._topology.has_edge(source, target) for source in left)
            for target in right
        )

    def _make_route(
        self,
        groups: list[tuple[str, tuple[str, ...]]],
        cap_of: dict[str, frozenset[str]],
    ) -> CandidateRoute:
        edges = tuple(
            ToolEdge(source, target)
            for (_, left), (_, right) in zip(groups, groups[1:])
            for source in left
            for target in right
            if self._topology.has_edge(source, target)
        )
        return CandidateRoute(
            layers=tuple(RouteLayer(layer, tools) for layer, tools in groups),
            capabilities=frozenset(
                capability
                for _, tools in groups
                for tool in tools
                for capability in cap_of[tool]
            ),
            edges=edges,
        )

    @staticmethod
    def _route_tools(route: CandidateRoute) -> set[str]:
        return {tool for layer in route.layers for tool in layer.tools}


class _FilteredTopology:
    def __init__(
        self, topology: Topology, tools: set[str], edges: set[tuple[str, str]]
    ) -> None:
        self._topology, self._tools, self._edges = topology, tools, edges

    def layers(self) -> Any:
        return self._topology.layers()

    def nodes(self) -> tuple[str, ...]:
        return tuple(name for name in self._topology.nodes() if name in self._tools)

    def node(self, name: str) -> Any:
        return self._topology.node(name)

    def nodes_in_layer(self, layer: str) -> tuple[str, ...]:
        return tuple(
            name for name in self._topology.nodes_in_layer(layer)
            if name in self._tools
        )

    def has_edge(self, source: str, target: str) -> bool:
        return (source, target) in self._edges


class RouteSearcher:
    """Compatibility facade for the original stateless Step 4 API."""

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
        if max_bridge_depth < 0:
            raise RouteSearchError("max_bridge_depth cannot be negative")
        unknown = sorted(set(disabled_tools) - set(topology.nodes()))
        if unknown:
            raise RouteSearchError(f"Unknown disabled tools: {', '.join(unknown)}")

        disabled_pairs: set[tuple[str, str]] = set()
        for edge in disabled_edges:
            pair = (edge.source, edge.target) if isinstance(edge, ToolEdge) else edge
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not all(isinstance(name, str) for name in pair)
                or not topology.has_edge(*pair)
            ):
                raise RouteSearchError(f"Disabled edge does not exist: {pair!r}")
            disabled_pairs.add(pair)

        enabled_tools = set(topology.nodes()) - set(disabled_tools)
        enabled_edges = {
            (edge.source, edge.target)
            for edge in topology.edges()
            if edge.source in enabled_tools
            and edge.target in enabled_tools
            and (edge.source, edge.target) not in disabled_pairs
        }
        filtered = _FilteredTopology(topology, enabled_tools, enabled_edges)
        registry = CapabilityRegistry()
        for name in filtered.nodes():
            for capability in sorted(filtered.node(name).spec.capabilities):
                registry.register(capability, name)
        routes = RouteSearch(
            filtered, registry, max_candidate_routes=max_candidate_routes
        ).search(tuple(sorted(required_capabilities)))

        layer_order = {layer.name: layer.order for layer in topology.layers()}
        required = set(required_capabilities)
        return tuple(
            route for route in routes
            if self._bridge_depth(route, topology, layer_order, required)
            <= max_bridge_depth
        )

    @staticmethod
    def _bridge_depth(
        route: CandidateRoute,
        topology: Topology,
        layer_order: dict[str, int],
        required: set[str],
    ) -> int:
        terminal_orders = sorted({
            layer_order[layer.layer]
            for layer in route.layers
            if any(
                topology.node(tool).spec.capabilities & required
                for tool in layer.tools
            )
        })
        return max(
            (
                right - left - 1
                for left, right in zip(terminal_orders, terminal_orders[1:])
            ),
            default=0,
        )
