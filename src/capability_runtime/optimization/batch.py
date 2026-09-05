from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

from ..core.errors import PruningError
from ..topology.patch import TopologyPatch
from ..topology.models import Topology


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    """A group of edges pruned together after individual checks passed ($60)."""

    id: str
    edges: tuple[tuple[str, str], ...]
    patch: TopologyPatch
    base_topology_version: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise PruningError("CandidateBatch requires a non-empty id")
        edges = tuple(self.edges)
        if not edges:
            raise PruningError(f"CandidateBatch {self.id!r} cannot be empty")
        if len(set(edges)) != len(edges):
            raise PruningError(f"CandidateBatch {self.id!r} contains duplicate edges")
        object.__setattr__(self, "edges", edges)

    @property
    def size(self) -> int:
        return len(self.edges)


class BatchCandidateBuilder:
    """Group individually-safe candidates into validated batches ($61, $62).

    Progressive pruning: candidates are emitted in a deterministic order and
    grouped so no batch exceeds max_pruning_batch_size, bounding blast radius
    and keeping failures diagnosable ($63). The builder only *shapes* batches;
    accept/reject is decided by the validation gates (Steps 8-10).
    """

    def __init__(
        self,
        *,
        max_pruning_batch_size: int = 20,
        batch_id_prefix: str = "batch",
    ) -> None:
        if isinstance(max_pruning_batch_size, bool) or max_pruning_batch_size < 1:
            raise PruningError("max_pruning_batch_size must be a positive integer")
        if not batch_id_prefix.strip():
            raise PruningError("batch_id_prefix must be a non-empty string")
        self._max_size = max_pruning_batch_size
        self._prefix = batch_id_prefix.strip()

    def build(
        self,
        edges: Iterable[tuple[str, str]],
        *,
        base_topology_version: str = "",
    ) -> tuple[CandidateBatch, ...]:
        normalized = self._normalize_edges(edges)
        batches = tuple(
            CandidateBatch(
                id=f"{self._prefix}-{(offset // self._max_size) + 1:03d}",
                edges=normalized[offset : offset + self._max_size],
                patch=TopologyPatch(
                    disabled_edges=tuple(
                        _edge_key(source, target)
                        for source, target in normalized[
                            offset : offset + self._max_size
                        ]
                    )
                ),
                base_topology_version=base_topology_version,
            )
            for offset in range(0, len(normalized), self._max_size)
        )
        if not batches:
            raise PruningError("Cannot build batches from an empty edge set")
        return batches

    def validate_edges(
        self,
        edges: Iterable[tuple[str, str]],
        topology: Topology,
    ) -> None:
        """Ensure every batched edge exists; a batch may only touch real edges."""
        unknown = sorted(
            (source, target)
            for source, target in self._normalize_edges(edges)
            if not topology.has_edge(source, target)
        )
        if unknown:
            rendered = ", ".join(_edge_key(*pair) for pair in unknown)
            raise PruningError(f"Nested batch references unknown edges: {rendered}")

    @staticmethod
    def bisect(batch: CandidateBatch) -> tuple[CandidateBatch, CandidateBatch]:
        """Split a failing batch in half to isolate the harmful subset ($94)."""
        if batch.size < 2:
            raise PruningError(
                f"Cannot bisect single-edge batch {batch.id!r}; "
                "isolate the edge itself as the harmful subset"
            )
        midpoint = (batch.size + 1) // 2
        left = batch.edges[:midpoint]
        right = batch.edges[midpoint:]
        return (
            CandidateBatch(
                id=f"{batch.id}:a",
                edges=left,
                patch=TopologyPatch(disabled_edges=tuple(_edge_key(*e) for e in left)),
                base_topology_version=batch.base_topology_version,
            ),
            CandidateBatch(
                id=f"{batch.id}:b",
                edges=right,
                patch=TopologyPatch(disabled_edges=tuple(_edge_key(*e) for e in right)),
                base_topology_version=batch.base_topology_version,
            ),
        )

    @staticmethod
    def _normalize_edges(edges: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
        pairs = tuple(edges)
        if any(
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not all(isinstance(name, str) and name.strip() for name in pair)
            for pair in pairs
        ):
            raise PruningError("edges must be an iterable of (source, target) string pairs")
        dedup = sorted(set(pairs), key=lambda pair: (pair[0], pair[1]))
        return tuple(dedup)


def _edge_key(source: str, target: str) -> str:
    return f"{source}->{target}"