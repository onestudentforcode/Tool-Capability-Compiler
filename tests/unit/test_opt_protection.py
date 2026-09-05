from capability_runtime import (
    Candidate,
    CandidateDetector,
    CandidateReason,
    CandidateStatus,
    EdgeEvidence,
    EvidenceReport,
    Layer,
    NodeEvidence,
    PruningConfig,
    ProtectionRegistry,
    Scenario,
    ToolEdge,
    ToolNode,
    ToolSpec,
    Topology,
)
from capability_runtime.core.tool import NodeSelector


async def _noop(*args, **kwargs):
    return None


def _node(name: str, layer: str, caps) -> ToolNode:
    return ToolNode(
        spec=ToolSpec(
            name=name,
            layer=layer,
            providers=NodeSelector(all_nodes=True),
            workers=NodeSelector(all_nodes=True),
            capabilities=frozenset(caps),
        ),
        handler=_noop,
    )


def _topo(node_defs, edges) -> Topology:
    layer_names = sorted({layer for _, layer, _ in node_defs})
    layers = tuple(Layer(order=index, name=name) for index, name in enumerate(layer_names))
    nodes = {name: _node(name, layer, caps) for name, layer, caps in node_defs}
    topology_edges = tuple(ToolEdge(s, t) for s, t in edges)
    return Topology(layers=layers, nodes=nodes, edges=topology_edges)


def _sentinel(scenario_id: str, capabilities) -> Scenario:
    return Scenario(
        id=scenario_id,
        query="sentinel",
        expected_capabilities=tuple(capabilities),
        metadata={"sentinel": True},
    )


def test_unique_capability_provider_is_protected() -> None:
    topology = _topo(
        [("s1", "read", {"alpha", "shared"}),
         ("s2", "read", {"shared"}),
         ("down", "action", {"beta"})],
        [("s1", "down"), ("s2", "down")],
    )
    registry = ProtectionRegistry(topology)
    # s1 uniquely provides alpha; s2 only shares `shared` with s1 -> not unique
    assert registry.is_node_protected("s1")
    assert not registry.is_node_protected("s2")
    # neither is a bridge (down still reachable via the other)
    assert not registry.is_edge_protected("s1", "down")


def test_bridge_node_is_protected() -> None:
    # gate is the only path from feed to sink
    topology = _topo(
        [("feed", "read", {"x"}),
         ("gate", "analyze", {"x"}),
         ("sink", "action", {"x"}),
         ("sink2", "action", {"x"})],
        [("feed", "gate"), ("gate", "sink"), ("feed", "sink2")],
    )
    registry = ProtectionRegistry(topology)
    assert registry.is_node_protected("gate")
    assert not registry.is_node_protected("feed")
    assert not registry.is_node_protected("sink")


def test_bridge_with_redundant_path_is_not_protected() -> None:
    # midA redundancy means neither midA nor midB is a bridge
    topology = _topo(
        [("up", "read", {"x"}),
         ("midA", "analyze", {"x"}),
         ("midB", "analyze", {"x"}),
         ("down", "action", {"x"})],
        [("up", "midA"), ("up", "midB"), ("midA", "down"), ("midB", "down")],
    )
    registry = ProtectionRegistry(topology)
    assert not registry.is_node_protected("midA")
    assert not registry.is_node_protected("midB")


def test_sentinel_unique_provider_locks_incident_edges() -> None:
    topology = _topo(
        [("feed", "read", {"x"}),
         ("crit", "analyze", {"critical.cap.check", "x"}),
         ("out", "action", {"y"})],
        [("feed", "crit"), ("crit", "out")],
    )
    registry = ProtectionRegistry(topology, sentinel_scenarios=[_sentinel("s", ["critical.cap.check"])])

    assert registry.is_edge_protected("feed", "crit")
    assert registry.is_edge_protected("crit", "out")
    assert registry.is_node_protected("crit")


def test_non_sentinel_unique_provider_protects_node_not_edges() -> None:
    topology = _topo(
        [("feed", "read", {"x"}),
         ("sole", "analyze", {"only_cap", "x"}),
         ("out", "action", {"y"})],
        [("feed", "sole"), ("sole", "out")],
    )
    registry = ProtectionRegistry(topology)
    # sole uniquely provides only_cap, so the NODE is protected...
    assert registry.is_node_protected("sole")
    # ...but its edges are NOT auto-locked (no sentinel requires only_cap)
    assert not registry.is_edge_protected("feed", "sole")
    assert not registry.is_edge_protected("sole", "out")


def test_extra_overrides_are_respected() -> None:
    topology = _topo(
        [("a", "read", {"p"}), ("b", "action", {"q"})],
        [("a", "b")],
    )
    registry = ProtectionRegistry(
        topology,
        extra_protected_edges=[("a", "b")],
        extra_protected_nodes=["a"],
    )
    assert registry.is_edge_protected("a", "b")
    assert registry.is_node_protected("a")


def test_detector_marks_protected_node_as_protected() -> None:
    evidence = EvidenceReport(
        scenario_count=0,
        trial_count=0,
        node_evidence={
            "gate": NodeEvidence(
                tool="gate", available_count=200, selected_count=0,
                success_trial_count=0, failed_trial_count=0,
            )
        },
        edge_evidence={},
    )
    out = CandidateDetector(
        PruningConfig(min_node_availability=100), protected_nodes=["gate"]
    ).detect(evidence)
    item = next(c for c in out if c.subject == "gate")
    assert item.status is CandidateStatus.PROTECTED
    assert item.reason is None and item.protected is True