import pytest

from capability_runtime import (
    BatchCandidateBuilder,
    Layer,
    PruningError,
    ToolNode,
    ToolSpec,
    Topology,
    TopologyPatch,
)
from capability_runtime.core.tool import NodeSelector
from capability_runtime.topology.models import ToolEdge


def _edge(source: str, target: str) -> ToolEdge:
    return ToolEdge(source, target)


def _noop(*args, **kwargs):
    return None


def _node(name: str, layer: str) -> ToolNode:
    return ToolNode(
        spec=ToolSpec(
            name=name,
            layer=layer,
            providers=NodeSelector(all_nodes=True),
            workers=NodeSelector(all_nodes=True),
        ),
        handler=_noop,
    )


def _topology() -> Topology:
    return Topology(
        layers=(Layer(order=0, name="a"), Layer(order=1, name="b")),
        nodes={"x": _node("x", "a"), "y": _node("y", "b"), "z": _node("z", "b")},
        edges=(_edge("x", "y"), _edge("x", "z")),
    )


def _edges(*pairs):
    return list(pairs)


def test_build_chunks_edges_within_max_batch_size() -> None:
    edges = _edges(
        ("a", "b"),
        ("c", "d"),
        ("e", "f"),
        ("g", "h"),
        ("i", "j"),
        ("k", "l"),
    )
    batches = BatchCandidateBuilder(max_pruning_batch_size=4).build(edges)
    assert [batch.size for batch in batches] == [4, 2]
    assert all(batch.size <= 4 for batch in batches)
    assert batches[0].id == "batch-001"
    assert batches[1].id == "batch-002"
    assert batches[0].edges == (
        ("a", "b"),
        ("c", "d"),
        ("e", "f"),
        ("g", "h"),
    )
    assert batches[1].edges == (("i", "j"), ("k", "l"))


def test_batch_patch_disables_exactly_its_edges() -> None:
    edges = _edges(("a", "b"), ("a", "c"))
    batch = BatchCandidateBuilder().build(edges)[0]
    assert isinstance(batch.patch, TopologyPatch)
    assert batch.patch.disabled_edges == ("a->b", "a->c")


def test_build_sorts_and_dedupes_edges() -> None:
    edges = _edges(("d", "e"), ("a", "b"), ("a", "b"), ("a", "c"))
    batch = BatchCandidateBuilder().build(edges)[0]
    assert batch.edges == (("a", "b"), ("a", "c"), ("d", "e"))


def test_build_empty_rejected() -> None:
    with pytest.raises(PruningError):
        BatchCandidateBuilder().build([])


def test_build_invalid_edge_shape_rejected() -> None:
    with pytest.raises(PruningError):
        BatchCandidateBuilder().build([("a", "b"), ("only-one",)])


def test_default_max_batch_size_is_enforced() -> None:
    # Default is 20; 21 edges must split into two batches.
    edges = _edges(*[(f"a{i}", f"b{i}") for i in range(21)])
    batches = BatchCandidateBuilder().build(edges)
    assert batches[0].size == 20
    assert batches[1].size == 1


def test_invalid_max_size_rejected() -> None:
    with pytest.raises(PruningError):
        BatchCandidateBuilder(max_pruning_batch_size=0)


def test_bisect_splits_a_failing_batch() -> None:
    batch = BatchCandidateBuilder().build(_edges(("a", "b"), ("c", "d"), ("e", "f")))[0]
    left, right = BatchCandidateBuilder.bisect(batch)
    assert left.size == 2
    assert right.size == 1
    assert left.patch.disabled_edges == ("a->b", "c->d")
    assert right.patch.disabled_edges == ("e->f",)
    assert left.base_topology_version == batch.base_topology_version


def test_bisect_single_edge_rejected() -> None:
    batch = BatchCandidateBuilder().build(_edges(("a", "b")))[0]
    with pytest.raises(PruningError):
        BatchCandidateBuilder.bisect(batch)


def test_validate_edges_rejects_unknown_edges() -> None:
    builder = BatchCandidateBuilder()
    with pytest.raises(PruningError):
        builder.validate_edges([("ghost", "phantom")], _topology())


def test_validate_edges_accepts_real_edges() -> None:
    builder = BatchCandidateBuilder()
    builder.validate_edges([("x", "y"), ("x", "z")], _topology())