from __future__ import annotations

import itertools

from ..registry.capability_registry import CapabilityRegistry
from ..route.models import RouteLayer
from ..topology.models import Topology
from .candidate_route import CandidateRoute


class RouteSearch:
    """Capability-guided Layer Search.

    Finds candidate routes through a layered topology that cover a set of
    required capabilities. The search is purely static metadata analysis:
    it does not execute tools, call any LLM, or modify the topology.

    Feasibility is decided exactly by a *maximal valid chain* (monotone
    fixpoint): for a contiguous span of layers, repeatedly drop any tool that
    lacks a forward/backward edge to the surviving tools in the adjacent layer
    until convergence. Any legal chain is a sub-chain of this maximal set, so
    the maximal set covers all required capabilities iff a legal route exists.
    This is exact for single-layer multi-tool routes, cross-layer parallel
    branches, and arbitrarily deep bridge layers.

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
        max_expansions: int = 100_000,
    ) -> None:
        self._topology = topology
        self._capability_registry = capability_registry
        self._max_candidate_routes = max_candidate_routes
        self._max_expansions = max_expansions
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

        required_set = set(required_capabilities)
        cap_of = {
            name: set(self._topology.node(name).spec.capabilities)
            for name in self._topology.nodes()
        }
        relevant_orders = sorted({layer_of[t] for t in relevant})
        candidates: list[CandidateRoute] = []
        expansions = [0]

        # Seed the result with the maximal valid chain of the best witness span,
        # so a feasible scenario always yields at least one candidate route.
        witness = self._feasible_witness(required_set, cap_of)
        if witness is None:
            return ()
        seed_span, seed_M, _ = witness
        seed_groups = [
            (layer_name, tuple(sorted(seed_M[layer_name]))) for layer_name in seed_span
        ]
        self._try_add_route(seed_groups, required_set, cap_of, candidates)

        for start_idx, start_order in enumerate(relevant_orders):
            for end_order in relevant_orders[start_idx:]:
                span_names = [
                    self._order_to_layer[o]
                    for o in sorted(
                        o
                        for o in self._layer_order.values()
                        if start_order <= o <= end_order
                    )
                ]
                M = self._max_chain(span_names)
                layer_candidates: list[tuple[str, list[str]]] = []
                ok = True
                for o in sorted(
                    o
                    for o in self._layer_order.values()
                    if start_order <= o <= end_order
                ):
                    layer_name = self._order_to_layer[o]
                    tools = sorted(M.get(layer_name, ()))
                    if not tools:
                        ok = False
                        break
                    layer_candidates.append((layer_name, tools))
                if not ok:
                    continue
                self._enumerate_subsets(
                    layer_candidates=layer_candidates,
                    layer_index=0,
                    current_groups=[],
                    covered=set(),
                    required_capabilities=required_set,
                    cap_of=cap_of,
                    candidates=candidates,
                    expansions=expansions,
                )
                if len(candidates) >= self._max_candidate_routes:
                    break
            if len(candidates) >= self._max_candidate_routes:
                break

        return self._deduplicate_and_limit(candidates)

    def is_feasible(self, required_capabilities: tuple[str, ...]) -> bool:
        """Exact feasibility: does at least one legal route cover all capabilities?"""
        if not required_capabilities:
            return False
        cap_of = {
            name: set(self._topology.node(name).spec.capabilities)
            for name in self._topology.nodes()
        }
        return self._feasible_witness(set(required_capabilities), cap_of) is not None

    def covered_capabilities(
        self, required_capabilities: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Best-effort covered subset: the witness span that covers the most
        required capabilities (ties broken lexicographically)."""
        if not required_capabilities:
            return ()
        required_set = set(required_capabilities)
        cap_of = {
            name: set(self._topology.node(name).spec.capabilities)
            for name in self._topology.nodes()
        }
        best: set[str] = set()
        names = [layer.name for layer in self._topology.layers()]
        n = len(names)
        for s in range(n):
            for e in range(s, n):
                span = names[s : e + 1]
                M = self._max_chain(span)
                covered = set()
                for layer_name in span:
                    for tool in M[layer_name]:
                        covered |= cap_of[tool]
                here = covered & required_set
                if len(here) > len(best) or (
                    len(here) == len(best) and sorted(here) < sorted(best)
                ):
                    best = here
                if best == required_set:
                    return tuple(sorted(best))
        return tuple(sorted(best))

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

    def _max_chain(self, span_names: list[str]) -> dict[str, set[str]]:
        """Maximal valid chain over a contiguous span of layers.

        A tool survives iff it has an edge to/from a surviving tool in each
        adjacent layer. The fixpoint is reached by monotone deletion.
        """
        tools_by_layer = {
            name: set(self._topology.nodes_in_layer(name)) for name in span_names
        }
        M: dict[str, set[str]] = {name: set(tools) for name, tools in tools_by_layer.items()}
        changed = True
        while changed:
            changed = False
            for i in range(len(span_names) - 1):
                left, right = span_names[i], span_names[i + 1]
                keep_right = {
                    t for t in M[right] if any(self._topology.has_edge(p, t) for p in M[left])
                }
                if keep_right != M[right]:
                    M[right] = keep_right
                    changed = True
                keep_left = {
                    t for t in M[left] if any(self._topology.has_edge(t, c) for c in M[right])
                }
                if keep_left != M[left]:
                    M[left] = keep_left
                    changed = True
        return M

    def _feasible_witness(
        self, required_set: set[str], cap_of: dict[str, set[str]]
    ) -> tuple[list[str], dict[str, set[str]], set[str]] | None:
        """Return the first (span, maximal chain, covered caps) that covers all required."""
        names = [layer.name for layer in self._topology.layers()]
        n = len(names)
        for s in range(n):
            for e in range(s, n):
                span = names[s : e + 1]
                M = self._max_chain(span)
                covered: set[str] = set()
                for layer_name in span:
                    for tool in M[layer_name]:
                        covered |= cap_of[tool]
                if required_set.issubset(covered):
                    return span, M, covered
        return None

    def _enumerate_subsets(
        self,
        layer_candidates: list[tuple[str, list[str]]],
        layer_index: int,
        current_groups: list[tuple[str, tuple[str, ...]]],
        covered: set[str],
        required_capabilities: set[str],
        cap_of: dict[str, set[str]],
        candidates: list[CandidateRoute],
        expansions: list[int],
    ) -> None:
        if len(candidates) >= self._max_candidate_routes:
            return
        if expansions[0] >= self._max_expansions:
            return
        if layer_index == len(layer_candidates):
            if current_groups and required_capabilities.issubset(covered):
                self._try_add_route(current_groups, required_capabilities, cap_of, candidates)
            return

        expansions[0] += 1
        layer_name, tools = layer_candidates[layer_index]
        previous_tools = current_groups[-1][1] if current_groups else None

        for size in range(1, len(tools) + 1):
            for subset in itertools.combinations(tools, size):
                if previous_tools is not None and not self._is_interconnected(
                    previous_tools, subset
                ):
                    continue
                new_covered = covered | set().union(*(cap_of[t] for t in subset))
                missing = required_capabilities - new_covered
                # Look-ahead pruning: every still-missing capability must have a
                # provider in a strictly later layer, else this subset cannot lead
                # to a complete route.
                if missing:
                    suffix: set[str] = set()
                    for _, later_tools in layer_candidates[layer_index + 1 :]:
                        for t in later_tools:
                            suffix |= cap_of[t]
                    if not missing.issubset(suffix):
                        continue
                current_groups.append((layer_name, subset))
                self._enumerate_subsets(
                    layer_candidates,
                    layer_index + 1,
                    current_groups,
                    new_covered,
                    required_capabilities,
                    cap_of,
                    candidates,
                    expansions,
                )
                current_groups.pop()
                if len(candidates) >= self._max_candidate_routes:
                    return
                if expansions[0] >= self._max_expansions:
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
        cap_of: dict[str, set[str]],
        candidates: list[CandidateRoute],
    ) -> None:
        if len(candidates) >= self._max_candidate_routes:
            return

        all_capabilities: set[str] = set()
        for _, tools in groups:
            for tool in tools:
                all_capabilities |= cap_of[tool]

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
