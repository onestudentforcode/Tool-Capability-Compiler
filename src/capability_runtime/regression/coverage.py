from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import AbstractSet

from ..core.capability import validate_capability_name
from ..core.errors import CoverageAnalyzerError
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
    confidence: float = 1.0
    reason_detail: str = ""


class CoverageAnalyzer:
    """Perform static coverage analysis without executing tools."""

    def __init__(
        self,
        *,
        confidence_threshold: float = 0.8,
        max_candidate_routes: int = 20,
        max_bridge_depth: int | None = None,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise CoverageAnalyzerError(
                "confidence_threshold must be between 0.0 and 1.0"
            )
        if max_candidate_routes <= 0:
            raise CoverageAnalyzerError("max_candidate_routes must be positive")
        if max_bridge_depth is not None and max_bridge_depth < 0:
            raise CoverageAnalyzerError("max_bridge_depth cannot be negative")
        self._confidence_threshold = confidence_threshold
        self._max_candidate_routes = max_candidate_routes
        # Kept for constructor compatibility. Exact coverage must accept legal
        # routes of arbitrary declared depth.
        self._max_bridge_depth = max_bridge_depth

    def analyze(
        self,
        topology: Topology,
        required_capabilities: AbstractSet[str],
        *,
        resolution_confidence: float | None = None,
        ambiguous_capabilities: AbstractSet[str] = frozenset(),
    ) -> CoverageResult:
        if not required_capabilities:
            raise CoverageAnalyzerError(
                "Coverage analysis requires at least one capability; "
                "query-only scenarios are not supported until the Resolver."
            )
        required = tuple(sorted(
            validate_capability_name(capability)
            for capability in required_capabilities
        ))
        confidence = 1.0 if resolution_confidence is None else resolution_confidence
        if not 0.0 <= confidence <= 1.0:
            raise CoverageAnalyzerError(
                "resolution_confidence must be between 0.0 and 1.0"
            )
        ambiguous = tuple(sorted(
            validate_capability_name(capability)
            for capability in ambiguous_capabilities
        ))
        unknown_ambiguous = sorted(set(ambiguous) - set(required))
        if unknown_ambiguous:
            raise CoverageAnalyzerError(
                "Ambiguous capabilities must be required capabilities: "
                + ", ".join(unknown_ambiguous)
            )

        registry = self._build_capability_registry(topology)
        missing = tuple(
            capability
            for capability in required
            if not registry.providers(capability)
        )
        if missing:
            return CoverageResult(
                status=CoverageStatus.UNCOVERED,
                reason=FailureReason.MISSING_CAPABILITY,
                required_capabilities=required,
                covered_capabilities=(),
                missing_capabilities=missing,
                confidence=confidence,
                reason_detail="No enabled tool declares: " + ", ".join(missing),
            )

        search = RouteSearch(
            topology,
            registry,
            max_candidate_routes=self._max_candidate_routes,
        )
        candidate_routes = search.search(required)
        if not search.is_feasible(required):
            covered = search.covered_capabilities(required)
            return CoverageResult(
                status=CoverageStatus.UNCOVERED,
                reason=FailureReason.TOPOLOGY_DISCONNECTED,
                required_capabilities=required,
                covered_capabilities=covered,
                missing_capabilities=tuple(sorted(set(required) - set(covered))),
                confidence=confidence,
                reason_detail=(
                    "Capability providers exist but cannot form a valid topology route"
                ),
            )

        if ambiguous:
            return CoverageResult(
                status=CoverageStatus.UNCERTAIN,
                reason=FailureReason.AMBIGUOUS_CAPABILITY,
                required_capabilities=required,
                covered_capabilities=required,
                missing_capabilities=(),
                candidate_routes=candidate_routes,
                confidence=confidence,
                reason_detail="Ambiguous capabilities: " + ", ".join(ambiguous),
            )
        if confidence < self._confidence_threshold:
            return CoverageResult(
                status=CoverageStatus.UNCERTAIN,
                reason=FailureReason.LOW_RESOLUTION_CONFIDENCE,
                required_capabilities=required,
                covered_capabilities=required,
                missing_capabilities=(),
                candidate_routes=candidate_routes,
                confidence=confidence,
                reason_detail=(
                    f"Resolution confidence {confidence:.3f} is below threshold "
                    f"{self._confidence_threshold:.3f}"
                ),
            )
        return CoverageResult(
            status=CoverageStatus.COVERED,
            reason=None,
            required_capabilities=required,
            covered_capabilities=required,
            missing_capabilities=(),
            candidate_routes=candidate_routes,
            confidence=confidence,
        )

    @staticmethod
    def _build_capability_registry(topology: Topology) -> CapabilityRegistry:
        registry = CapabilityRegistry()
        for name in topology.nodes():
            for capability in sorted(topology.node(name).spec.capabilities):
                registry.register(capability, name)
        return registry
