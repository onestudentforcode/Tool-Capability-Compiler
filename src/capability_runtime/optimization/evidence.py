from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..core.errors import EvidenceError
from ..regression.slow.stats import ObservationReport
from ..regression.slow.trial import TrialResult


@dataclass(frozen=True, slots=True)
class NodeEvidence:
    """Node usage evidence derived from Phase 3 observation stats ($43)."""

    tool: str
    available_count: int
    selected_count: int
    success_trial_count: int
    failed_trial_count: int

    @property
    def selection_rate(self) -> float:
        if self.available_count == 0:
            return 0.0
        return self.selected_count / self.available_count

    @property
    def never_available(self) -> bool:
        return self.available_count == 0


@dataclass(frozen=True, slots=True)
class EdgeEvidence:
    """Edge evidence in two domains: opportunity/observed, plus business support.

    `protected` marks a must-route edge (declared topology requirement), so an
    observed-zero here is NOT a pruning signal on its own (Unused != Useless).
    """

    source: str
    target: str
    opportunity_count: int
    observed_count: int
    successful_trial_count: int
    failed_trial_count: int
    successful_route_count: int
    scenario_count: int
    protected: bool = False

    @property
    def usage_rate(self) -> float:
        if self.opportunity_count == 0:
            return 0.0
        return self.observed_count / self.opportunity_count

    @property
    def has_evidence(self) -> bool:
        return self.opportunity_count > 0

    @property
    def zero_observed(self) -> bool:
        return self.observed_count == 0


@dataclass(frozen=True, slots=True)
class EvidenceReport:
    """Deterministic, per-topology aggregation of all long-run evidence."""

    scenario_count: int
    trial_count: int
    node_evidence: Mapping[str, NodeEvidence]
    edge_evidence: Mapping[str, EdgeEvidence]


def _key(source: str, target: str) -> str:
    return f"{source}->{target}"


def _walked_edges(layers: Sequence) -> list[tuple[str, str]]:
    """Consecutive selected-selected pairs — the edges a Trial actually walked."""
    walked: list[tuple[str, str]] = []
    for index, layer in enumerate(layers):
        if index + 1 >= len(layers):
            break
        following = layers[index + 1]
        for source in layer.selected_tools:
            for target in following.selected_tools:
                walked.append((source, target))
    return walked


class EvidenceAggregator:
    """Consumes an ObservationReport, plus the raw TrialResults needed to fill
    the per-edge business-support fields that Phase 3 stats do not expose."""

    def build(
        self,
        *,
        report: ObservationReport,
        results: Sequence[TrialResult],
        edges: Sequence[tuple[str, str]],
        protected_edges: frozenset[tuple[str, str]] = frozenset(),
    ) -> EvidenceReport:
        if not edges:
            raise EvidenceError("cannot build edge evidence without declared edges")

        node_evidence: dict[str, NodeEvidence] = {}
        for tool in sorted(report.node_stats):
            stats = report.node_stats[tool]
            node_evidence[tool] = NodeEvidence(
                tool=tool,
                available_count=stats.opportunity_count,
                selected_count=stats.selected_count,
                success_trial_count=stats.success_trial_count,
                failed_trial_count=stats.failed_trial_count,
            )

        failed: list[str] = []
        for tool in report.node_stats:
            if _negative(report.node_stats[tool].opportunity_count):
                failed.append(tool)
        if failed:
            raise EvidenceError(
                f"negative opportunity count on tools: {', '.join(sorted(failed))}"
            )

        declared = frozenset(edges)
        edge_success: Counter[tuple[str, str]] = Counter()
        edge_scenarios: dict[tuple[str, str], set[str]] = defaultdict(set)

        for result in results:
            if result.trace is None:
                continue
            scenario = result.trial.scenario_id
            business_success = result.evaluation is not None and result.evaluation.success
            for edge in _walked_edges(result.trace.layers):
                if edge not in declared:
                    continue
                edge_scenarios[edge].add(scenario)
                if business_success:
                    edge_success[edge] += 1

        edge_evidence: dict[str, EdgeEvidence] = {}
        for source, target in sorted(declared):
            key = _key(source, target)
            stats = report.edge_stats.get(key)
            edge_evidence[key] = EdgeEvidence(
                source=source,
                target=target,
                opportunity_count=stats.opportunity_count if stats else 0,
                observed_count=stats.observed_count if stats else 0,
                successful_trial_count=stats.successful_trial_count if stats else 0,
                failed_trial_count=stats.failed_trial_count if stats else 0,
                successful_route_count=edge_success.get((source, target), 0),
                scenario_count=len(edge_scenarios[(source, target)]),
                protected=(source, target) in protected_edges,
            )

        return EvidenceReport(
            scenario_count=report.scenario_count,
            trial_count=report.trial_count,
            node_evidence=node_evidence,
            edge_evidence=edge_evidence,
        )


def _negative(value: int) -> bool:
    return value < 0