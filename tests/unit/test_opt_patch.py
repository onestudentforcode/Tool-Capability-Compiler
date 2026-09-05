import pytest

from capability_runtime import (
    CandidateTopology,
    Layer,
    ToolEdge,
    ToolNode,
    ToolSpec,
    Topology,
    TopologyPatch,
    TopologyPatchError,
    apply_patch,
    build_candidate,
)
from capability_runtime.core.tool import NodeSelector


async def _noop(*args, **kwargs):
    return None


def _node(name: str, layer: str) -> ToolNode:
    return ToolNode(
        spec=ToolSpec(
            name=name, layer=layer,
            providers=NodeSelector(all_nodes=True),
            workers=NodeSelector(all_nodes=True),
        ),
        handler=_noop,
    )


def _base() -> Topology:
    layers = (Layer(order=0, name="read"), Layer(order=1, name="action"))
    nodes = {
        "a": _node("a", "read"),
        "b": _node("b", "action"),
        "c": _node("c", "action"),
    }
    edges = (ToolEdge("a", "b"), ToolEdge("a", "c"))
    return Topology(layers=layers, nodes=nodes, edges=edges)


def test_patch_disables_edge() -> None:
    patch = TopologyPatch(disabled_edges=("a->b",))
    assert patch.is_edge_disabled("a", "b")
    assert not patch.is_edge_disabled("a", "c")
    topo = apply_patch(_base(), patch)
    assert topo.nodes() == ("a", "b", "c")
    assert topo.edges() == (ToolEdge("a", "c"),)


def test_patch_disables_node_and_its_edges() -> None:
    patch = TopologyPatch(disabled_nodes=("b",))
    topo = apply_patch(_base(), patch)
    assert topo.nodes() == ("a", "c")
    assert topo.edges() == (ToolEdge("a", "c"),)


def test_patch_isolates_edge_and_node() -> None:
    patch = TopologyPatch(disabled_edges=("a->c",), disabled_nodes=("b",))
    topo = apply_patch(_base(), patch)
    # a and b are both gone as nodes; no edges remain between a and c
    assert topo.nodes() == ("a", "c")
    assert topo.edges() == ()


def test_declared_topology_never_mutated() -> None:
    base = _base()
    apply_patch(base, TopologyPatch(disabled_edges=("a->c",), disabled_nodes=("b",)))
    assert base.nodes() == ("a", "b", "c")
    assert len(base.edges()) == 2


def test_patch_disable_every_node_rejected() -> None:
    base = _base()
    patch = TopologyPatch(disabled_nodes=("a", "b", "c"))
    with pytest.raises(TopologyPatchError):
        apply_patch(base, patch)


def test_patch_serialization_round_trip() -> None:
    patch = TopologyPatch(disabled_edges=("a->b",), disabled_nodes=("c",))
    restored = TopologyPatch.from_json(patch.to_json())
    assert restored == patch


def test_patch_from_invalid_json_rejected() -> None:
    with pytest.raises(TopologyPatchError):
        TopologyPatch.from_json({"disabled_edges": [1, 2]})
    with pytest.raises(TopologyPatchError):
        TopologyPatch.from_json("not-a-dict")


def test_patch_sort_and_dedupe() -> None:
    patch = TopologyPatch(disabled_edges=("b->a", "a->b", "a->b"), disabled_nodes=("z", "a", "z"))
    assert patch.disabled_edges == ("a->b", "b->a")
    assert patch.disabled_nodes == ("a", "z")


def test_candidate_topology_build() -> None:
    base = _base()
    candidate = build_candidate(base, TopologyPatch(disabled_edges=("a->b",)), base_version="v1")
    assert isinstance(candidate, CandidateTopology)
    assert candidate.base_version == "v1"
    assert candidate.patch.disabled_edges == ("a->b",)
    assert candidate.topology.edges() == (ToolEdge("a", "c"),)


def test_candidate_topology_requires_base_version() -> None:
    with pytest.raises(TopologyPatchError):
        build_candidate(_base(), TopologyPatch(), base_version="")