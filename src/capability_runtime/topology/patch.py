from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.errors import TopologyPatchError
from .models import Topology


def _edge_key(source: str, target: str) -> str:
    return f"{source}->{target}"


@dataclass(frozen=True, slots=True)
class TopologyPatch:
    """A declarative override: which edges / nodes are virtually disabled.

    The declared Topology stays immutable; disabling (not deleting) is expressed
    by this patch, so it is explainable and reversible ($65).
    """

    disabled_edges: tuple[str, ...] = ()
    disabled_nodes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        edges = _normalize(self.disabled_edges, "disabled_edges")
        nodes = _normalize(self.disabled_nodes, "disabled_nodes")
        object.__setattr__(self, "disabled_edges", edges)
        object.__setattr__(self, "disabled_nodes", nodes)

    def is_edge_disabled(self, source: str, target: str) -> bool:
        return _edge_key(source, target) in self._edge_set

    def is_node_disabled(self, name: str) -> bool:
        return name in self._node_set

    def to_json(self) -> dict[str, list[str]]:
        return {
            "disabled_edges": list(self.disabled_edges),
            "disabled_nodes": list(self.disabled_nodes),
        }

    @classmethod
    def from_json(cls, data: Any) -> TopologyPatch:
        if not isinstance(data, dict):
            raise TopologyPatchError("patch must be a JSON object")
        for key in ("disabled_edges", "disabled_nodes"):
            value = data.get(key, ())
            if not isinstance(value, (list, tuple)) or any(
                not isinstance(item, str) for item in value
            ):
                raise TopologyPatchError(f"patch field {key!r} must be a list of strings")
        return cls(
            disabled_edges=tuple(data.get("disabled_edges", ())),
            disabled_nodes=tuple(data.get("disabled_nodes", ())),
        )

    @property
    def _edge_set(self) -> frozenset[str]:
        return frozenset(self.disabled_edges)

    @property
    def _node_set(self) -> frozenset[str]:
        return frozenset(self.disabled_nodes)

    def __contains__(self, name: str) -> bool:
        return name in self._node_set


@dataclass(frozen=True, slots=True)
class CandidateTopology:
    """A declared topology plus an override patch, never mutating the base ($64)."""

    base_version: str
    patch: TopologyPatch
    topology: Topology

    def __post_init__(self) -> None:
        if not self.base_version or not self.base_version.strip():
            raise TopologyPatchError("CandidateTopology requires a non-empty base_version")
        if self.patch is None or self.topology is None:
            raise TopologyPatchError("CandidateTopology requires a patch and a topology")


def apply_patch(topology: Topology, patch: TopologyPatch) -> Topology:
    """Derive the active view of a declared topology under a patch.

    Disabled nodes - and every edge touching them - are removed from the view;
    disabled edges are removed. The result is a normal, read-only Topology that
    downstream runners can consume, while the base stays untouched for rollback.
    """
    active_nodes = {
        name for name in topology.nodes() if not patch.is_node_disabled(name)
    }
    if not active_nodes:
        raise TopologyPatchError("patch disables every node; nothing remains active")

    edges = tuple(
        edge
        for edge in topology.edges()
        if edge.source in active_nodes
        and edge.target in active_nodes
        and not patch.is_edge_disabled(edge.source, edge.target)
    )
    nodes = {name: topology.node(name) for name in sorted(active_nodes)}
    return Topology(
        layers=topology.layers(),
        nodes=nodes,
        edges=edges,
        warnings=topology.warnings(),
    )


def build_candidate(
    topology: Topology,
    patch: TopologyPatch,
    base_version: str,
) -> CandidateTopology:
    active = apply_patch(topology, patch)
    return CandidateTopology(base_version=base_version, patch=patch, topology=active)


def _normalize(items: tuple[str, ...], field: str) -> tuple[str, ...]:
    values = tuple(items)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise TopologyPatchError(f"{field} entries must be non-empty strings")
    dedup = tuple(dict.fromkeys(values))
    return tuple(sorted(dedup))