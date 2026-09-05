from __future__ import annotations

from dataclasses import dataclass

from ..core.errors import TopologyVersioningError
from ..topology.models import Topology
from .patch import TopologyPatch, apply_patch


@dataclass(frozen=True, slots=True)
class TopologyVersion:
    """An immutable snapshot of a topology at a specific version tag ($64).

    The ``declared`` topology always stays untouched; ``active`` is the
    currently-open search space after successive rounds of pruning.
    """

    version: str
    declared: Topology
    active: Topology
    patch: TopologyPatch

    def __post_init__(self) -> None:
        if not self.version or not self.version.strip():
            raise TopologyVersioningError(
                "TopologyVersion requires a non-empty version string"
            )
        if self.declared is None or self.active is None:
            raise TopologyVersioningError(
                "TopologyVersion requires both declared and active topologies"
            )
        if self.patch is None:
            raise TopologyVersioningError(
                "TopologyVersion requires a patch (may be empty)"
            )


def initial_version(declared: Topology, *, version: str = "v1") -> TopologyVersion:
    """Create version v1 where active ≈ declared ($64)."""
    if not version or not version.strip():
        raise TopologyVersioningError(
            "initial_version requires a non-empty version string"
        )
    empty_patch = TopologyPatch()
    active = apply_patch(declared, empty_patch)
    return TopologyVersion(
        version=version.strip(),
        declared=declared,
        active=active,
        patch=empty_patch,
    )


def commit_patch(base: TopologyVersion, patch: TopologyPatch, *, version: str) -> TopologyVersion:
    """Produce a new version by applying an additional patch on top of base.

    The new patch is *composed* with the base patch (both disabled sets
    unioned) so the full disable-list stays traceable across rounds ($63).
    """
    if not version or not version.strip():
        raise TopologyVersioningError("commit_patch requires a non-empty version")
    combined = TopologyPatch(
        disabled_edges=tuple(
            sorted(set(base.patch.disabled_edges) | set(patch.disabled_edges))
        ),
        disabled_nodes=tuple(
            sorted(set(base.patch.disabled_nodes) | set(patch.disabled_nodes))
        ),
    )
    active = apply_patch(base.declared, combined)
    return TopologyVersion(
        version=version.strip(),
        declared=base.declared,
        active=active,
        patch=combined,
    )


def rollback(target: TopologyVersion) -> TopologyVersion:
    """Return to the declared topology (empty patch) for a clean rollback.

    Phase 4 supports full rollback: if a round is rejected, the active
    topology reverts to the declared topology, and the patch is cleared.
    This is a conservative, all-or-nothing rollback — granular per-batch
    rollback is handled by the BatchCandidateBuilder's bisect flow.
    """
    empty = TopologyPatch()
    return TopologyVersion(
        version=f"{target.version}-rolled-back",
        declared=target.declared,
        active=target.declared,
        patch=empty,
    )


def compose_patches(first: TopologyPatch, second: TopologyPatch) -> TopologyPatch:
    """Union two patches so the second stacks on top of the first."""
    return TopologyPatch(
        disabled_edges=tuple(
            sorted(set(first.disabled_edges) | set(second.disabled_edges))
        ),
        disabled_nodes=tuple(
            sorted(set(first.disabled_nodes) | set(second.disabled_nodes))
        ),
    )