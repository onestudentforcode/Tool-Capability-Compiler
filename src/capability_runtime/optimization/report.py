from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..topology.patch import TopologyPatch
from ..topology.version import TopologyVersion


@dataclass(frozen=True, slots=True)
class OptimizationRound:
    """Result of one round of pruning (one batch)."""

    round_id: int
    candidates_proposed: int
    candidates_applied: int
    candidates_rejected: int
    patch: TopologyPatch
    fast_passed: bool = True
    slow_passed: bool = True
    diversity_passed: bool = True
    notes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.fast_passed and self.slow_passed and self.diversity_passed


@dataclass(frozen=True, slots=True)
class OptimizationReport:
    """Structured summary of a full topology optimization run ($127)."""

    start_version: str
    end_version: str
    declared_edges: int
    declared_nodes: int
    active_edges_before: int
    active_edges_after: int
    active_nodes_before: int
    active_nodes_after: int
    rounds: tuple[OptimizationRound, ...] = ()
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None

    @property
    def total_rounds(self) -> int:
        return len(self.rounds)

    @property
    def rounds_passed(self) -> int:
        return sum(1 for r in self.rounds if r.passed)

    @property
    def rounds_rejected(self) -> int:
        return self.total_rounds - self.rounds_passed

    @property
    def edges_removed(self) -> int:
        return self.active_edges_before - self.active_edges_after

    @property
    def nodes_removed(self) -> int:
        return self.active_nodes_before - self.active_nodes_after

    @property
    def success(self) -> bool:
        return self.edges_removed >= 0 and self.nodes_removed >= 0

    def summary_text(self) -> str:
        """Human-readable one-paragraph summary."""
        lines = [
            f"Optimization {self.start_version} -> {self.end_version}",
            f"  Edges: {self.active_edges_before} -> {self.active_edges_after} "
            f"(removed {self.edges_removed})",
            f"  Nodes: {self.active_nodes_before} -> {self.active_nodes_after} "
            f"(removed {self.nodes_removed})",
            f"  Rounds: {self.total_rounds} total, "
            f"{self.rounds_passed} passed, {self.rounds_rejected} rejected",
        ]
        if self.finished_at:
            lines.append(f"  Finished: {self.finished_at}")
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {
            "start_version": self.start_version,
            "end_version": self.end_version,
            "declared_edges": self.declared_edges,
            "declared_nodes": self.declared_nodes,
            "active_edges_before": self.active_edges_before,
            "active_edges_after": self.active_edges_after,
            "active_nodes_before": self.active_nodes_before,
            "active_nodes_after": self.active_nodes_after,
            "edges_removed": self.edges_removed,
            "nodes_removed": self.nodes_removed,
            "total_rounds": self.total_rounds,
            "rounds_passed": self.rounds_passed,
            "rounds_rejected": self.rounds_rejected,
            "success": self.success,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "rounds": [
                {
                    "round_id": r.round_id,
                    "candidates_proposed": r.candidates_proposed,
                    "candidates_applied": r.candidates_applied,
                    "candidates_rejected": r.candidates_rejected,
                    "passed": r.passed,
                    "fast_passed": r.fast_passed,
                    "slow_passed": r.slow_passed,
                    "diversity_passed": r.diversity_passed,
                    "patch": r.patch.to_json(),
                    "notes": list(r.notes),
                }
                for r in self.rounds
            ],
        }


def build_report(
    *,
    start: TopologyVersion,
    end: TopologyVersion,
    rounds: tuple[OptimizationRound, ...] = (),
) -> OptimizationReport:
    """Build an OptimizationReport from two TopologyVersions."""
    return OptimizationReport(
        start_version=start.version,
        end_version=end.version,
        declared_edges=len(start.declared.edges()),
        declared_nodes=len(start.declared.nodes()),
        active_edges_before=len(start.active.edges()),
        active_edges_after=len(end.active.edges()),
        active_nodes_before=len(start.active.nodes()),
        active_nodes_after=len(end.active.nodes()),
        rounds=rounds,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )