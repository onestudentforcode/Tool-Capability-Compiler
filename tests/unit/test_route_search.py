from __future__ import annotations

import pytest

from capability_runtime import (
    CapabilityRegistry,
    LayerRegistry,
    RouteLayer,
    RouteSearch,
    RouteSearchError,
    RouteSearcher,
    ToolRegistry,
    Topology,
    TopologyBuilder,
    tool,
)


def build(layers: tuple[str, ...], *nodes: object) -> Topology:
    layer_registry = LayerRegistry()
    for order, name in enumerate(layers):
        layer_registry.register(name, order)
    tool_registry = ToolRegistry()
    for node in nodes:
        tool_registry.register(node)
    return TopologyBuilder(layer_registry, tool_registry).build()


def search(topology: Topology, capabilities: set[str], **kwargs: int):
    registry = CapabilityRegistry()
    for name in topology.nodes():
        for capability in topology.node(name).spec.capabilities:
            registry.register(capability, name)
    return RouteSearch(topology, registry, **kwargs).search(
        tuple(sorted(capabilities))
    )


@tool(layer="read", capabilities={"order.read"})
async def db():
    raise AssertionError("Fast Regression must not invoke tools")


@tool(layer="read", capabilities={"order.read"})
async def erp():
    raise AssertionError("Fast Regression must not invoke tools")


@tool(layer="analyze", capabilities={"refund.policy.check"})
async def policy_check():
    raise AssertionError("Fast Regression must not invoke tools")


def test_finds_multiple_provider_routes_without_execution() -> None:
    topology = build(("read", "analyze"), db, erp, policy_check)
    routes = search(topology, {"order.read", "refund.policy.check"})
    assert [route.fingerprint for route in routes] == [
        "read:[db]|analyze:[policy_check]",
        "read:[erp]|analyze:[policy_check]",
    ]
    assert all(len(route.route_id) == 16 for route in routes)


def test_allows_multiple_tools_in_one_layer() -> None:
    @tool(layer="read", capabilities={"policy.read"})
    async def rag(): return None

    topology = build(("read", "analyze"), db, rag, policy_check)
    routes = search(
        topology, {"order.read", "policy.read", "refund.policy.check"}
    )
    assert routes[0].layers == (
        RouteLayer("read", ("db", "rag")),
        RouteLayer("analyze", ("policy_check",)),
    )
    assert routes[0].tool_count == 3
    assert routes[0].route_depth == 1


def test_respects_provider_and_worker_edges() -> None:
    @tool(layer="read", workers=["strict_policy"], capabilities={"order.read"})
    async def strict_db(): return None

    @tool(layer="read", workers=[], capabilities={"order.read"})
    async def loose_db(): return None

    @tool(
        layer="analyze",
        providers=["strict_db", "loose_db"],
        capabilities={"refund.policy.check"},
    )
    async def strict_policy(): return None

    routes = search(
        build(("read", "analyze"), strict_db, loose_db, strict_policy),
        {"order.read", "refund.policy.check"},
    )
    assert len(routes) == 1
    assert routes[0].layers[0].tools == ("strict_db",)


def test_discovers_arbitrarily_deep_bridge_chain() -> None:
    @tool(layer="read", workers=["b1"], capabilities={"order.read"})
    async def source(): return None

    @tool(layer="enrich", providers=["source"], workers=["b2"])
    async def b1(): return None

    @tool(layer="normalize", providers=["b1"], workers=["b3"])
    async def b2(): return None

    @tool(layer="transform", providers=["b2"], workers=["target"])
    async def b3(): return None

    @tool(layer="act", providers=["b3"], capabilities={"refund.execute"})
    async def target(): return None

    topology = build(
        ("read", "enrich", "normalize", "transform", "act"),
        source, b1, b2, b3, target,
    )
    routes = search(topology, {"order.read", "refund.execute"})
    assert [layer.layer for layer in routes[0].layers] == [
        "read", "enrich", "normalize", "transform", "act"
    ]


def test_stateless_facade_keeps_legacy_bridge_limit() -> None:
    @tool(layer="read", workers=["bridge"], capabilities={"order.read"})
    async def source(): return None

    @tool(layer="transform", providers=["source"], workers=["target"])
    async def bridge(): return None

    @tool(layer="act", providers=["bridge"], capabilities={"refund.execute"})
    async def target(): return None

    topology = build(("read", "transform", "act"), source, bridge, target)
    assert RouteSearcher().search(
        topology, {"order.read", "refund.execute"}, max_bridge_depth=1
    )
    assert RouteSearcher().search(
        topology, {"order.read", "refund.execute"}, max_bridge_depth=0
    ) == ()


def test_stateless_facade_respects_disabled_tools_and_edges() -> None:
    topology = build(("read", "analyze"), db, erp, policy_check)
    without_db = RouteSearcher().search(
        topology,
        {"order.read", "refund.policy.check"},
        disabled_tools={"db"},
    )
    assert [route.layers[0].tools for route in without_db] == [("erp",)]
    without_erp_edge = RouteSearcher().search(
        topology,
        {"order.read", "refund.policy.check"},
        disabled_edges={("erp", "policy_check")},
    )
    assert [route.layers[0].tools for route in without_erp_edge] == [("db",)]


def test_disconnected_capabilities_have_no_route() -> None:
    @tool(layer="read", workers=[], capabilities={"order.read"})
    async def isolated_read(): return None

    @tool(layer="analyze", providers=[], capabilities={"refund.policy.check"})
    async def isolated_analyze(): return None

    topology = build(("read", "analyze"), isolated_read, isolated_analyze)
    assert search(topology, {"order.read", "refund.policy.check"}) == ()


def test_single_layer_parallel_tools_need_no_edges() -> None:
    @tool(layer="read", workers=[], capabilities={"order.read"})
    async def only_db(): return None

    @tool(layer="read", workers=[], capabilities={"policy.read"})
    async def only_rag(): return None

    routes = search(
        build(("read", "analyze"), only_db, only_rag),
        {"order.read", "policy.read"},
    )
    assert routes[0].layers == (
        RouteLayer("read", ("only_db", "only_rag")),
    )


def test_dense_topology_returns_with_bounded_enumeration() -> None:
    nodes = []
    for index in range(4):
        async def reader(): return None
        reader.__name__ = f"reader_{index}"
        nodes.append(tool(
            layer="read", workers="all", capabilities={"order.read"}
        )(reader))
    for index in range(4):
        async def analyzer(): return None
        analyzer.__name__ = f"analyzer_{index}"
        nodes.append(tool(
            layer="analyze",
            providers="all",
            capabilities={"refund.policy.check"},
        )(analyzer))
    topology = build(("read", "analyze"), *nodes)
    routes = search(
        topology,
        {"order.read", "refund.policy.check"},
        max_expansions=100,
    )
    assert routes
    assert len(routes) <= 20


def test_invalid_search_options_raise_project_error() -> None:
    topology = build(("read",), db)
    with pytest.raises(RouteSearchError):
        RouteSearcher().search(
            topology, {"order.read"}, max_candidate_routes=0
        )
    with pytest.raises(RouteSearchError):
        RouteSearcher().search(
            topology, {"order.read"}, disabled_tools={"missing"}
        )
