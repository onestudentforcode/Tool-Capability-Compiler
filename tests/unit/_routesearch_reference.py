"""Frozen reference copy of RouteSearch for the equivalence oracle.

Verbatim snapshot of the subset-enumeration RouteSearch as of 2026-10-04
(routesearch-assignment batch A). Batch B replaces the generator in
src/capability_runtime/regression/route_search.py with assignment-driven
enumeration; this copy stays frozen forever as the oracle's "old
implementation". Under exhaustive budgets both generators must produce
the identical route set (acceptance doc §3.3).
"""

from __future__ import annotations

import itertools

from capability_runtime.core.capability import validate_capability_name
from capability_runtime.core.errors import RouteSearchError
from capability_runtime.registry.capability_registry import (
    CapabilityRegistry,
)
from capability_runtime.route.models import RouteLayer
from capability_runtime.topology.models import ToolEdge, Topology
from capability_runtime.regression.candidate_route import CandidateRoute


class ReferenceRouteSearch:
    """Exact feasibility plus bounded candidate enumeration (subset era)."""

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
        expansions = [0]
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
                self._enumerate(
                    groups, 0, [], frozenset(), required, cap_of, routes,
                    expansions
                )
                if len(routes) >= self._limit or expansions[0] >= self._max_expansions:
                    break
            if len(routes) >= self._limit or expansions[0] >= self._max_expansions:
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

    def _enumerate(
        self,
        groups: list[tuple[str, tuple[str, ...]]],
        index: int,
        selected: list[tuple[str, tuple[str, ...]]],
        covered: frozenset,
        required: frozenset,
        cap_of: dict[str, frozenset[str]],
        routes: dict[str, CandidateRoute],
        expansions: list[int],
    ) -> None:
        if len(routes) >= self._limit or expansions[0] >= self._max_expansions:
            return
        if index == len(groups):
            if required.issubset(covered):
                route = self._make_route(selected, cap_of)
                route_tools = self._route_tools(route)
                if any(self._route_tools(old) < route_tools for old in routes.values()):
                    return
                for key, old in tuple(routes.items()):
                    if route_tools < self._route_tools(old):
                        del routes[key]
                routes[route.fingerprint] = route
            return
        expansions[0] += 1
        layer, tools = groups[index]
        for size in range(1, len(tools) + 1):
            for subset in itertools.combinations(tools, size):
                # The budget must bound every subset examination, screened or
                # not: on wide maximal chains the failing-subset scan alone is
                # exponential while recursion-only counting never sees it.
                # Feasibility does not depend on this budget (witness below).
                expansions[0] += 1
                if len(routes) >= self._limit or expansions[0] >= self._max_expansions:
                    return
                if selected and not self._interconnected(selected[-1][1], subset):
                    continue
                new_covered = covered.union(
                    capability for tool in subset for capability in cap_of[tool]
                )
                missing = required - new_covered
                suffix = {
                    capability
                    for _, later_tools in groups[index + 1 :]
                    for tool in later_tools
                    for capability in cap_of[tool]
                }
                if missing and not missing.issubset(suffix):
                    continue
                selected.append((layer, subset))
                self._enumerate(
                    groups, index + 1, selected, new_covered, required,
                    cap_of, routes, expansions
                )
                selected.pop()

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
