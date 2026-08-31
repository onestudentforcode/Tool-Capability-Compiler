from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import AbstractSet

from ..core.capability import validate_capability_name
from ..core.errors import CoverageAnalyzerError, InvalidCapabilityError
from ..topology.models import Topology


class CoverageStatus(str, Enum):
    COVERED = "covered"
    UNCERTAIN = "uncertain"
    UNCOVERED = "uncovered"


class FailureReason(str, Enum):
    MISSING_CAPABILITY = "missing_capability"
    TOPOLOGY_DISCONNECTED = "topology_disconnected"
    LOW_RESOLUTION_CONFIDENCE = "low_resolution_confidence"
    AMBIGUOUS_CAPABILITY = "ambiguous_capability"
    NO_VALID_ROUTE = "no_valid_route"
    ROUTE_SEARCH_LIMIT_REACHED = "route_search_limit_reached"
    INVALID_SCENARIO = "invalid_scenario"


@dataclass(frozen=True, slots=True)
class CoverageResult:
    status: CoverageStatus
    reason: FailureReason | None
    required_capabilities: tuple[str, ...]
    covered_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]


class CoverageAnalyzer:
    """Gold Mode: judge whether required capabilities are covered by the topology.

    This is a static metadata analysis. It does not execute tools, does not call
    any LLM, and does not modify the topology.
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

        relevant = sorted(
            {
                tool
                for capability in required
                for tool in provider_map[capability]
            }
        )
        min_order = min(layer_of[tool] for tool in relevant)
        max_order = max(layer_of[tool] for tool in relevant)

        covered = self._covered_in_span(
            topology, provider_map, required, layer_of, min_order, max_order
        )
        if covered == required:
            return CoverageResult(
                status=CoverageStatus.COVERED,
                reason=None,
                required_capabilities=tuple(required),
                covered_capabilities=tuple(covered),
                missing_capabilities=(),
            )

        missing = sorted(set(required) - set(covered))
        return CoverageResult(
            status=CoverageStatus.UNCOVERED,
            reason=FailureReason.TOPOLOGY_DISCONNECTED,
            required_capabilities=tuple(required),
            covered_capabilities=tuple(covered),
            missing_capabilities=tuple(missing),
        )

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

    @staticmethod
    def _covered_in_span(
        topology: Topology,
        provider_map: dict[str, list[str]],
        required: list[str],
        layer_of: dict[str, int],
        min_order: int,
        max_order: int,
    ) -> list[str]:
        layer_order = {
            layer.name: layer.order for layer in topology.layers()
        }
        in_span = {
            name
            for name in topology.nodes()
            if min_order <= layer_of[name] <= max_order
        }
        remaining = set(in_span)
        required_set = set(required)
        best: list[str] = []
        while remaining:
            seed = min(remaining)
            component: set[str] = set()
            queue: deque[str] = deque([seed])
            while queue:
                current = queue.popleft()
                if current in component:
                    continue
                component.add(current)
                for neighbor in (
                    *topology.predecessors(current),
                    *topology.successors(current),
                ):
                    if (
                        neighbor in in_span
                        and neighbor not in component
                    ):
                        queue.append(neighbor)
            remaining -= component

            covered_here = sorted(
                capability
                for capability in required
                if any(tool in component for tool in provider_map[capability])
            )
            if (
                len(covered_here) > len(best)
                or (len(covered_here) == len(best) and covered_here < best)
            ):
                best = covered_here
            if set(best) == required_set:
                break

        return best
