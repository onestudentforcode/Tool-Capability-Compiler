from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

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

    def __init__(self, config: PruningConfig | None = None) -> None:
        self._config = config or PruningConfig()

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