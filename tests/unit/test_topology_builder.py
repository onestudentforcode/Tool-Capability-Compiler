from dataclasses import dataclass

import pytest

from capability_runtime import (
    InvalidTopologyReferenceError,
    LayerNotFoundError,
    LayerRegistry,
    TopologyBuildError,
    ToolRegistry,
    TopologyBuilder,
    tool,
)


def build(*nodes):
    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "act")):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in nodes:
        tools.register(node)
    return TopologyBuilder(layers, tools).build()


def test_default_is_dense_between_adjacent_layers_only() -> None:
    @tool(layer="read")
    async def db(): return None

    @tool(layer="read")
    async def rag(): return None

    @tool(layer="analyze")
    async def policy(): return None

    @tool(layer="analyze")
    async def risk(): return None

    @tool(layer="act")
    async def refund(): return None

    topology = build(db, rag, policy, risk, refund)
    assert {(edge.source, edge.target) for edge in topology.edges()} == {
        ("db", "policy"),
        ("db", "risk"),
        ("rag", "policy"),
        ("rag", "risk"),
        ("policy", "refund"),
        ("risk", "refund"),
    }
    assert not topology.has_edge("db", "refund")


def test_provider_and_worker_allow_lists_are_intersected() -> None:
    @tool(layer="read", workers=["policy"])
    async def db(): return None

    @tool(layer="read")
    async def rag(): return None

    @tool(layer="analyze", providers=["db", "rag"])
    async def policy(): return None

    @tool(layer="analyze")
    async def risk(): return None

    topology = build(db, rag, policy, risk)
    pairs = {(edge.source, edge.target) for edge in topology.edges()}
    assert ("db", "policy") in pairs
    assert ("db", "risk") not in pairs
    assert ("rag", "policy") in pairs


def test_schema_mismatch_warns_but_does_not_remove_declared_edge() -> None:
    @dataclass(frozen=True)
    class Order: pass

    @dataclass(frozen=True)
    class RefundRequest: pass

    @tool(layer="read", produces=[Order])
    async def db(): return Order()

    @tool(layer="analyze", consumes=[RefundRequest])
    async def refund_builder(value): return None

    topology = build(db, refund_builder)
    assert topology.has_edge("db", "refund_builder")
    # scoped to SCHEMA_MISMATCH: the no-producer consumption here also
    # earns an UNSATISFIABLE_INPUT (declaration diagnostics, batch D),
    # which is asserted in its own tests.
    mismatch = [w for w in topology.warnings() if w.code == "SCHEMA_MISMATCH"]
    assert len(mismatch) == 1
    assert topology.warnings()[0].code == "SCHEMA_MISMATCH"


def test_unknown_or_non_adjacent_references_fail_fast() -> None:
    @tool(layer="read", workers=["missing"])
    async def broken(): return None

    with pytest.raises(InvalidTopologyReferenceError):
        build(broken)

    @tool(layer="read", workers=["refund"])
    async def db(): return None

    @tool(layer="act")
    async def refund(): return None

    with pytest.raises(InvalidTopologyReferenceError):
        build(db, refund)


def test_unknown_tool_layer_and_non_contiguous_layers_are_rejected() -> None:
    @tool(layer="missing")
    async def orphan(): return None

    with pytest.raises(LayerNotFoundError):
        build(orphan)

    layers = LayerRegistry()
    layers.register("read", 0)
    layers.register("act", 2)
    with pytest.raises(TopologyBuildError):
        TopologyBuilder(layers, ToolRegistry()).build()
