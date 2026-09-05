from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from collections.abc import Iterable
from enum import Enum

from ..scenario.models import Scenario
from ..topology.models import Topology
from .evidence import EvidenceReport


class CandidateStatus(str, Enum):
    IDENTIFIED = "identified"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROBE_REQUIRED = "probe_required"
    PROTECTED = "protected"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CandidateReason(str, Enum):
    """Why a candidate warrants investigation -- never a justification to delete."""

    HIGH_OPPORTUNITY_UNUSED = "high_opportunity_unused"
    LOW_SELECTION_RATE = "low_selection_rate"
    REDUNDANT_CONNECTIVITY = "redundant_connectivity"
    REDUNDANT_PROVIDER_PATH = "redundant_provider_path"
    NO_SUCCESSFUL_ROUTE_SUPPORT = "no_successful_route_support"
    NODE_NEVER_SELECTED = "node_never_selected"


@dataclass(frozen=True, slots=True)
class PruningConfig:
    """Defaults live here, never scattered through algorithms ($27)."""

    min_edge_opportunities: int = 100
    min_node_availability: int = 100
    edge_usage_threshold: float = 0.01
    node_selection_threshold: float = 0.01
    max_fast_coverage_drop: float = 0.0
    max_success_rate_drop: float = 0.01
    max_quality_drop: float = 0.02
    max_pruning_batch_size: int = 20

    def __post_init__(self) -> None:
        if self.min_edge_opportunities < 0 or self.min_node_availability < 0:
            raise ValueError("opportunity/availability floors must be non-negative")
        bounds = (
            self.edge_usage_threshold,
            self.node_selection_threshold,
            self.max_fast_coverage_drop,
            self.max_success_rate_drop,
            self.max_quality_drop,
        )
        if any(value < 0.0 or value > 1.0 for value in bounds):
            raise ValueError("thresholds and drops must be floats in [0, 1]")
        if self.max_pruning_batch_size < 1:
            raise ValueError("max_pruning_batch_size must be positive")


@dataclass(frozen=True, slots=True)
class Candidate:
    """A subject worth investigating -- detection only, never a modification."""

    kind: str
    subject: str
    status: CandidateStatus
    reason: CandidateReason | None
    opportunity_count: int
    usage_rate: float
    source: str | None = None
    target: str | None = None
    successful_route_count: int = 0
    scenario_count: int = 0
    protected: bool = False


class CandidateDetector:
    """Rule-based first-pass detector ($26): evidence in, candidates out. No changes."""

    def __init__(
        self,
        config: PruningConfig | None = None,
        protected_nodes: Iterable[str] = (),
    ) -> None:
        self._config = config or PruningConfig()
        self._protected_nodes = frozenset(protected_nodes)

    def detect(self, evidence: EvidenceReport) -> tuple[Candidate, ...]:
        detected: list[Candidate] = []
        for key in sorted(evidence.edge_evidence):
            edge = evidence.edge_evidence[key]
            status, reason = self._classify_edge(edge)
            if status is None:
                continue
            detected.append(
                Candidate(
                    kind="edge",
                    subject=key,
                    status=status,
                    reason=reason,
                    opportunity_count=edge.opportunity_count,
                    usage_rate=edge.usage_rate,
                    source=edge.source,
                    target=edge.target,
                    successful_route_count=edge.successful_route_count,
                    scenario_count=edge.scenario_count,
                    protected=edge.protected,
                )
            )
        for tool in sorted(evidence.node_evidence):
            if tool in self._protected_nodes:
                detected.append(
                    Candidate(
                        kind="node",
                        subject=tool,
                        status=CandidateStatus.PROTECTED,
                        reason=None,
                        opportunity_count=0,
                        usage_rate=0.0,
                        source=tool,
                        protected=True,
                    )
                )
                continue
            node = evidence.node_evidence[tool]
            if node.never_available:
                continue
            if (
                node.available_count >= self._config.min_node_availability
                and node.selected_count == 0
            ):
                detected.append(
                    Candidate(
                        kind="node",
                        subject=tool,
                        status=CandidateStatus.IDENTIFIED,
                        reason=CandidateReason.NODE_NEVER_SELECTED,
                        opportunity_count=node.available_count,
                        usage_rate=0.0,
                        source=tool,
                    )
                )
        return tuple(detected)

    def _classify_edge(self, edge) -> tuple[CandidateStatus | None, CandidateReason | None]:
        if edge.protected:
            return CandidateStatus.PROTECTED, None
        low_usage = edge.usage_rate <= self._config.edge_usage_threshold
        if edge.opportunity_count < self._config.min_edge_opportunities:
            if low_usage:
                return CandidateStatus.INSUFFICIENT_EVIDENCE, None
            return None, None
        if low_usage:
            reason = (
                CandidateReason.HIGH_OPPORTUNITY_UNUSED
                if edge.zero_observed
                else CandidateReason.LOW_SELECTION_RATE
            )
            return CandidateStatus.IDENTIFIED, reason
        return None, None


class ProtectionRegistry:
    """Declares which edges and nodes must never enter pruning.

    Rules: unique capability provider ($33), bridge node ($110), sentinel-locked
    edges (a unique provider of a sentinel-required capability), plus explicit
    must-route overrides. Protection is structural and static -- it never
    depends on any observed usage or a scenario's business outcome.
    """

    def __init__(
        self,
        topology: Topology,
        *,
        extra_protected_edges: Iterable[tuple[str, str]] = (),
        extra_protected_nodes: Iterable[str] = (),
        sentinel_scenarios: Iterable[Scenario] = (),
    ) -> None:
        self._topology = topology
        self._protected_nodes = self._compute_protected_nodes(extra_protected_nodes)
        self._protected_edges = self._compute_protected_edges(
            extra_protected_edges, sentinel_scenarios
        )

    def protected_nodes(self) -> frozenset[str]:
        return self._protected_nodes

    def protected_edges(self) -> frozenset[tuple[str, str]]:
        return self._protected_edges

    def is_node_protected(self, name: str) -> bool:
        return name in self._protected_nodes

    def is_edge_protected(self, source: str, target: str) -> bool:
        return (source, target) in self._protected_edges

    # -- internals ---------------------------------------------------------

    def _unique_capability_providers(self) -> frozenset[str]:
        providers: dict[str, set[str]] = defaultdict(set)
        for name in self._topology.nodes():
            node = self._topology.node(name)
            for capability in node.spec.capabilities:
                providers[capability].add(name)
        unique: set[str] = set()
        for names in providers.values():
            if len(names) == 1:
                unique |= names
        return frozenset(unique)

    def _bridge_nodes(self) -> frozenset[str]:
        nodes = frozenset(self._topology.nodes())
        edges = frozenset((edge.source, edge.target) for edge in self._topology.edges())
        full_reach = _reachability(nodes, edges)
        bridges: set[str] = set()
        for removed in nodes:
            remaining = nodes - {removed}
            if not remaining:
                continue
            sub_edges = {
                (source, target)
                for (source, target) in edges
                if source != removed and target != removed
            }
            sub_reach = _reachability(remaining, sub_edges)
            for source in remaining:
                for target in full_reach[source]:
                    if target in remaining and target not in sub_reach[source]:
                        bridges.add(removed)
                        break
                if removed in bridges:
                    break
        return frozenset(bridges)

    def _compute_protected_nodes(self, extra: Iterable[str]) -> frozenset[str]:
        return frozenset(extra) | self._unique_capability_providers() | self._bridge_nodes()

    def _compute_protected_edges(
        self,
        extra: Iterable[tuple[str, str]],
        sentinel_scenarios: Iterable[Scenario],
    ) -> frozenset[tuple[str, str]]:
        protected = frozenset(extra)
        unique = self._unique_capability_providers()
        sentinel_required = _sentinel_required_capabilities(sentinel_scenarios)
        if sentinel_required:
            critical = self._capability_providers(sentinel_required) & unique
            edges = frozenset(
                (edge.source, edge.target) for edge in self._topology.edges()
            )
            incident = {
                edge
                for edge in edges
                if edge[0] in critical or edge[1] in critical
            }
            protected |= incident
        return protected

    def _capability_providers(self, capabilities: frozenset[str]) -> set[str]:
        providers: set[str] = set()
        for name in self._topology.nodes():
            node = self._topology.node(name)
            if node.spec.capabilities & capabilities:
                providers.add(name)
        return providers


def _reachability(nodes, edges) -> dict[str, frozenset[str]]:
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    for source, target in edges:
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
    reach: dict[str, frozenset[str]] = {}
    for node in nodes:
        seen: set[str] = set()
        stack = list(adjacency[node])
        while stack:
            top = stack.pop()
            if top == node or top in seen:
                continue
            seen.add(top)
            stack.extend(adjacency[top])
        reach[node] = frozenset(seen)
    return reach


def _sentinel_required_capabilities(scenarios: Iterable[Scenario]) -> frozenset[str]:
    required: set[str] = set()
    for scenario in scenarios:
        if _is_sentinel(scenario):
            required |= set(scenario.expected_capabilities)
    return frozenset(required)


def _is_sentinel(scenario: Scenario) -> bool:
    return bool(scenario.metadata.get("sentinel", False))