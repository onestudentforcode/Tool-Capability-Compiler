from __future__ import annotations

import pytest

from capability_runtime import (
    CoverageAnalyzer,
    CoverageAnalyzerError,
    CoverageStatus,
    FailureReason,
    InvalidCapabilityError,
    LayerRegistry,
    ToolRegistry,
    Topology,
    TopologyBuilder,
    tool,
)


def _build(layers: tuple[str, ...], nodes: tuple[object, ...]) -> Topology:
    layer_registry = LayerRegistry()
    for order, name in enumerate(layers):
        layer_registry.register(name, order)
    tool_registry = ToolRegistry()
    for node in nodes:
        tool_registry.register(node)
    return TopologyBuilder(layer_registry, tool_registry).build()


@tool(layer="read", workers="all", capabilities={"order.read"})
async def db():
    return None


@tool(layer="read", workers="all", capabilities={"order.read"})
async def erp():
    return None


@tool(layer="read", workers="all", capabilities={"policy.read"})
async def rag():
    return None


@tool(
    layer="analyze",
    providers="all",
    workers="all",
    capabilities={"refund.policy.check"},
)
async def policy_check():
    return None


@tool(layer="act", providers="all", capabilities={"refund.execute"})
async def refund():
    return None


@tool(layer="read", workers=[], capabilities={"order.read"})
async def isolated_read():
    return None


@tool(layer="analyze", providers=[], capabilities={"refund.policy.check"})
async def isolated_analyze():
    return None


def test_covered_simple_connected_route() -> None:
    topology = _build(
        ("read", "analyze", "act"),
        (db, policy_check, refund),
    )
    result = CoverageAnalyzer().analyze(
        topology, {"order.read", "refund.policy.check"}
    )
    assert result.status == CoverageStatus.COVERED
    assert result.reason is None
    assert result.required_capabilities == ("order.read", "refund.policy.check")
    assert result.covered_capabilities == ("order.read", "refund.policy.check")
    assert result.missing_capabilities == ()


def test_missing_capability() -> None:
    topology = _build(
        ("read", "analyze", "act"),
        (db, policy_check, refund),
    )
    result = CoverageAnalyzer().analyze(
        topology, {"order.read", "order.shipping_address.update"}
    )
    assert result.status == CoverageStatus.UNCOVERED
    assert result.reason == FailureReason.MISSING_CAPABILITY
    assert result.missing_capabilities == ("order.shipping_address.update",)
    assert result.covered_capabilities == ()


def test_missing_reports_all_missing() -> None:
    topology = _build(("read",), (db,))
    result = CoverageAnalyzer().analyze(
        topology, {"invoice.send", "order.shipping_address.update"}
    )
    assert result.status == CoverageStatus.UNCOVERED
    assert result.reason == FailureReason.MISSING_CAPABILITY
    assert result.missing_capabilities == (
        "invoice.send",
        "order.shipping_address.update",
    )


def test_topology_disconnected() -> None:
    topology = _build(
        ("read", "analyze"),
        (isolated_read, isolated_analyze),
    )
    result = CoverageAnalyzer().analyze(
        topology, {"order.read", "refund.policy.check"}
    )
    assert result.status == CoverageStatus.UNCOVERED
    assert result.reason == FailureReason.TOPOLOGY_DISCONNECTED
    assert result.covered_capabilities == ("order.read",)
    assert result.missing_capabilities == ("refund.policy.check",)


def test_multi_provider_covered() -> None:
    topology = _build(
        ("read", "analyze"),
        (db, erp, policy_check),
    )
    result = CoverageAnalyzer().analyze(topology, {"order.read"})
    assert result.status == CoverageStatus.COVERED
    assert result.covered_capabilities == ("order.read",)


def test_bridge_tool_enables_coverage() -> None:
    @tool(layer="read", workers=["normalize"], capabilities={"order.read"})
    async def source():
        return None

    @tool(
        layer="analyze",
        providers=["source"],
        workers=["target"],
    )
    async def normalize():
        return None

    @tool(layer="act", providers=["normalize"], capabilities={"refund.execute"})
    async def target():
        return None

    topology = _build(("read", "analyze", "act"), (source, normalize, target))
    result = CoverageAnalyzer().analyze(
        topology, {"order.read", "refund.execute"}
    )
    assert result.status == CoverageStatus.COVERED
    assert result.covered_capabilities == ("order.read", "refund.execute")


def test_single_layer_span() -> None:
    topology = _build(("read", "analyze"), (db, erp, policy_check))
    result = CoverageAnalyzer().analyze(topology, {"order.read"})
    assert result.status == CoverageStatus.COVERED
    assert result.covered_capabilities == ("order.read",)


def test_two_components_partial_cover_is_disconnected() -> None:
    topology = _build(
        ("read", "analyze"),
        (isolated_read, isolated_analyze),
    )
    result = CoverageAnalyzer().analyze(
        topology, {"order.read", "refund.policy.check"}
    )
    assert result.status == CoverageStatus.UNCOVERED
    assert result.reason == FailureReason.TOPOLOGY_DISCONNECTED


def test_span_excludes_irrelevant_outer_layer() -> None:
    @tool(layer="context", workers=["analyze_bridge"])
    async def context_tool():
        return None

    @tool(
        layer="analyze",
        providers=["context_tool"],
        workers=["act_bridge"],
    )
    async def analyze_bridge():
        return None

    @tool(layer="act", providers=["analyze_bridge"])
    async def act_bridge():
        return None

    topology = _build(
        ("context", "analyze", "act"),
        (context_tool, analyze_bridge, act_bridge),
    )
    result = CoverageAnalyzer().analyze(
        topology, {"order.read", "refund.policy.check"}
    )
    assert result.status == CoverageStatus.UNCOVERED
    assert result.reason == FailureReason.MISSING_CAPABILITY


def test_empty_required_raises() -> None:
    topology = _build(("read",), (db,))
    with pytest.raises(CoverageAnalyzerError):
        CoverageAnalyzer().analyze(topology, set())


def test_invalid_capability_name_raises() -> None:
    topology = _build(("read",), (db,))
    with pytest.raises(InvalidCapabilityError):
        CoverageAnalyzer().analyze(topology, {"RefundCheck"})


def test_result_tuples_sorted() -> None:
    topology = _build(
        ("read", "analyze", "act"),
        (db, policy_check, refund),
    )
    result = CoverageAnalyzer().analyze(
        topology, {"refund.policy.check", "order.read"}
    )
    assert result.required_capabilities == ("order.read", "refund.policy.check")
    assert result.covered_capabilities == ("order.read", "refund.policy.check")


def test_covered_three_layer_refund_topology() -> None:
    topology = _build(
        ("read", "analyze", "act"),
        (db, rag, policy_check, refund),
    )
    result = CoverageAnalyzer().analyze(
        topology,
        {"order.read", "policy.read", "refund.policy.check", "refund.execute"},
    )
    assert result.status == CoverageStatus.COVERED
    assert result.reason is None
    assert result.missing_capabilities == ()


def test_disconnected_three_layer_refund_topology() -> None:
    @tool(layer="read", workers=["policy_check"], capabilities={"order.read"})
    async def good_db():
        return None

    @tool(layer="read", workers=[], capabilities={"policy.read"})
    async def disconnected_rag():
        return None

    topology = _build(
        ("read", "analyze", "act"),
        (good_db, disconnected_rag, policy_check, refund),
    )
    result = CoverageAnalyzer().analyze(
        topology, {"order.read", "policy.read"}
    )
    assert result.status == CoverageStatus.UNCOVERED
    assert result.reason == FailureReason.TOPOLOGY_DISCONNECTED
    assert result.covered_capabilities == ("order.read",)
    assert result.missing_capabilities == ("policy.read",)
