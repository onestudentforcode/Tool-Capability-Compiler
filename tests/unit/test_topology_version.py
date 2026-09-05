import pytest

from capability_runtime import (
    LayerRegistry,
    Topology,
    TopologyBuilder,
    TopologyPatch,
    TopologyVersion,
    TopologyVersioningError,
    ToolRegistry,
    commit_patch,
    compose_patches,
    initial_version,
    rollback,
    tool,
)


@tool(layer="L1", workers="all", capabilities={"cap.a"})
async def tool_a() -> None:
    pass


@tool(layer="L2", workers="all", capabilities={"cap.b"})
async def tool_b() -> None:
    pass


@tool(layer="L2", workers="all", capabilities={"cap.c"})
async def tool_c() -> None:
    pass


def _make_topology() -> Topology:
    layers = LayerRegistry()
    layers.register("L1", 0)
    layers.register("L2", 1)
    tools = ToolRegistry()
    tools.register(tool_a)
    tools.register(tool_b)
    tools.register(tool_c)
    return TopologyBuilder(layers, tools).build()


def test_initial_version_creates_v1_with_active_equals_declared() -> None:
    topo = _make_topology()
    v = initial_version(topo, version="v1")
    assert isinstance(v, TopologyVersion)
    assert v.version == "v1"
    assert v.declared is topo
    assert v.patch.disabled_edges == ()
    assert v.patch.disabled_nodes == ()


def test_initial_version_defaults_to_v1() -> None:
    topo = _make_topology()
    v = initial_version(topo)
    assert v.version == "v1"


def test_initial_version_empty_version_rejected() -> None:
    topo = _make_topology()
    with pytest.raises(TopologyVersioningError):
        initial_version(topo, version="  ")


def test_commit_patch_produces_new_version() -> None:
    topo = _make_topology()
    v1 = initial_version(topo, version="v1")
    patch = TopologyPatch(disabled_edges=("tool_a->tool_b",))
    v2 = commit_patch(v1, patch, version="v2")
    assert v2.version == "v2"
    assert v2.patch.disabled_edges == ("tool_a->tool_b",)
    assert v2.declared is topo
    assert v2.active is not topo
    assert len(v2.active.edges()) < len(topo.edges())


def test_commit_patch_unions_with_existing_disable_list() -> None:
    topo = _make_topology()
    v1 = initial_version(topo, version="v1")
    p1 = TopologyPatch(disabled_edges=("tool_a->tool_b",))
    v2 = commit_patch(v1, p1, version="v2")
    p2 = TopologyPatch(disabled_nodes=("tool_c",))
    v3 = commit_patch(v2, p2, version="v3")
    assert set(v3.patch.disabled_edges) == {"tool_a->tool_b"}
    assert set(v3.patch.disabled_nodes) == {"tool_c"}


def test_commit_patch_requires_nonempty_version() -> None:
    topo = _make_topology()
    v1 = initial_version(topo, version="v1")
    with pytest.raises(TopologyVersioningError):
        commit_patch(v1, TopologyPatch(), version="")


def test_rollback_returns_to_declared_topology() -> None:
    topo = _make_topology()
    v1 = initial_version(topo, version="v1")
    patch = TopologyPatch(disabled_edges=("tool_a->tool_b",))
    v2 = commit_patch(v1, patch, version="v2")
    assert len(v2.active.edges()) < len(topo.edges())
    rolled = rollback(v2)
    assert len(rolled.active.edges()) == len(topo.edges())
    assert rolled.patch.disabled_edges == ()
    assert rolled.patch.disabled_nodes == ()
    assert rolled.version == "v2-rolled-back"


def test_topology_version_immutable() -> None:
    topo = _make_topology()
    v = initial_version(topo, version="v1")
    with pytest.raises(AttributeError):
        v.version = "v2"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        v.patch = TopologyPatch(disabled_edges=("x",))  # type: ignore[misc]


def test_compose_patches_unions_edges_and_nodes() -> None:
    p1 = TopologyPatch(disabled_edges=("a:b",), disabled_nodes=("n1",))
    p2 = TopologyPatch(disabled_edges=("c:d",), disabled_nodes=("n2",))
    combined = compose_patches(p1, p2)
    assert set(combined.disabled_edges) == {"a:b", "c:d"}
    assert set(combined.disabled_nodes) == {"n1", "n2"}


def test_topology_version_none_declared_rejected() -> None:
    with pytest.raises(TopologyVersioningError):
        TopologyVersion(
            version="v1",
            declared=None,  # type: ignore[arg-type]
            active=_make_topology(),
            patch=TopologyPatch(),
        )