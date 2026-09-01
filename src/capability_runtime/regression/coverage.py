from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import AbstractSet

from ..core.capability import validate_capability_name
from ..core.errors import CoverageAnalyzerError, InvalidCapabilityError
from ..registry.capability_registry import CapabilityRegistry
from ..topology.models import Topology
from .candidate_route import CandidateRoute
from .route_search import RouteSearch


class CoverageStatus(str, Enum):
    COVERED = "covered"
    UNCERTAIN = "uncertain"
    UNCOVERED = "uncovered"


class FailureReason(str, Enum):
    MISSING_CAPABILITY = "missing_capability"
    TOPOLOGY_DISCONNECTED = "topology_disconnected"
    LOW_RESOLUTION_CONFIDENCE = "low_resolution_confidence"
    AMBIGUOUS_CAPABILITY = "ambiguous_capability"
    ROUTE_SEARCH_LIMIT_REACHED = "route_search_limit_reached"
    INVALID_SCENARIO = "invalid_scenario"


@dataclass(frozen=True, slots=True)
class CoverageResult:
    status: CoverageStatus
    reason: FailureReason | None
    required_capabilities: tuple[str, ...]
    covered_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    candidate_routes: tuple[CandidateRoute, ...] = ()


class CoverageAnalyzer:
    """Gold Mode: judge whether required capabilities are covered by the topology.

    This is a static metadata analysis. It does not execute tools, does not call
    any LLM, and does not modify the topology.

    A scenario is COVERED iff at least one legal candidate route exists that
    covers all required capabilities. Feasibility is decided exactly by
    ``RouteSearch`` (maximal valid chain), so the verdict is never affected by
    a search budget.
    """

    def analyze(
        self,
        topology: Topology,
        required_capabilities: AbstractSet[str],
    ) -> CoverageResult:
        if not required_capabilities:
            raise CoverageAnalyzerError(
                "Coverage analysis requires at least one capability; "
                "query-only scenarios are not supported until the Resolver."
            )

        required = sorted(
            validate_capability_name(capability)
            for capability in required_capabilities
        )

        provider_map, layer_of = self._index(topology)

        missing = sorted(
            capability
            for capability in required
            if not provider_map.get(capability)
        )
        if missing:
            return CoverageResult(
                status=CoverageStatus.UNCOVERED,
                reason=FailureReason.MISSING_CAPABILITY,
                required_capabilities=tuple(required),
                covered_capabilities=(),
                missing_capabilities=tuple(missing),
            )

        capability_registry = self._build_capability_registry(topology)
        route_search = RouteSearch(topology, capability_registry)
        candidate_routes = route_search.search(tuple(required))
        if candidate_routes:
            return CoverageResult(
                status=CoverageStatus.COVERED,
                reason=None,
                required_capabilities=tuple(required),
                covered_capabilities=tuple(sorted(set(required))),
                missing_capabilities=(),
                candidate_routes=candidate_routes,
            )

        covered = route_search.covered_capabilities(tuple(required))
        missing = sorted(set(required) - set(covered))
        return CoverageResult(
            status=CoverageStatus.UNCOVERED,
            reason=FailureReason.TOPOLOGY_DISCONNECTED,
            required_capabilities=tuple(required),
            covered_capabilities=tuple(covered),
            missing_capabilities=tuple(missing),
        )

    @staticmethod
    def _build_capability_registry(topology: Topology) -> CapabilityRegistry:
        registry = CapabilityRegistry()
        for name in topology.nodes():
            node = topology.node(name)
            for capability in sorted(node.spec.capabilities):
                registry.register(capability, name)
        return registry

    @staticmethod
    def _index(
        topology: Topology,
    ) -> tuple[dict[str, list[str]], dict[str, int]]:
        provider_map: dict[str, list[str]] = {}
        for name in topology.nodes():
            node = topology.node(name)
            for capability in sorted(node.spec.capabilities):
                provider_map.setdefault(capability, []).append(name)

        layer_order = {
            layer.name: layer.order for layer in topology.layers()
        }
        layer_of = {
            name: layer_order[node.spec.layer]
            for name in topology.nodes()
            for node in (topology.node(name),)
        }
        return provider_map, layer_of
