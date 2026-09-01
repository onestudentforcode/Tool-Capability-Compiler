import pytest

from capability_runtime import (
    LayerRegistry,
    RouteSearchError,
    RouteSearcher,
    ToolRegistry,
    Topology,
    TopologyBuilder,
    tool,
)


def build(layers: tuple[str, ...], *nodes) -> Topology:
    layer_registry = LayerRegistry()
    for order, name in enumerate(layers):
        layer_registry.register(name, order)
    tool_registry = ToolRegistry()
    for node in nodes:
        tool_registry.register(node)
    return TopologyBuilder(layer_registry, tool_registry).build()


@tool(layer="read", capabilities={"order.read"})
async def db():
    raise AssertionError("Fast Regression must not invoke tools")


@tool(layer="read", capabilities={"order.read"})
async def erp():
    raise AssertionError("Fast Regression must not invoke tools")


@tool(
    layer="analyze",
    capabilities={"refund.policy.check"},
)
async def policy_check():
    raise AssertionError("Fast Regression must not invoke tools")


def test_finds_multiple_provider_routes_without_execution() -> None:
    topology = build(("read", "analyze"), db, erp, policy_check)
    routes = RouteSearcher().search(
        topology, {"order.read", "refund.policy.check"}
    )

    assert [route.fingerprint for route in routes] == [
        "read:[db]|analyze:[policy_check]",
        "read:[erp]|analyze:[policy_check]",
    ]
    assert all(route.capabilities >= {"order.read", "refund.policy.check"} for route in routes)
    assert all(len(route.route_id) == 16 for route in routes)


def test_allows_multiple_tools_in_one_layer() -> None:
    @tool(layer="read", capabilities={"policy.read"})
    async def rag(): return None

    topology = build(("read", "analyze"), db, rag, policy_check)
    routes = RouteSearcher().search(
        topology,
        {"order.read", "policy.read", "refund.policy.check"},
    )

    assert len(routes) == 1
    assert routes[0].layers[0].tools == ("db", "rag")
    assert routes[0].tool_count == 3
    assert routes[0].layer_count == 2
    assert routes[0].route_depth == 1


def test_discovers_bridge_tools_with_depth_limit() -> None:
    @tool(layer="read", workers=["normalize"], capabilities={"order.read"})
    async def source(): return None

    @tool(layer="transform", providers=["source"], workers=["refund"])
    async def normalize(): return None

    @tool(
        layer="act",
        providers=["normalize"],
        capabilities={"refund.execute"},
    )
    async def refund(): return None

    topology = build(("read", "transform", "act"), source, normalize, refund)
    routes = RouteSearcher().search(
        topology, {"order.read", "refund.execute"}, max_bridge_depth=1
    )
    assert [layer.tools for layer in routes[0].layers] == [
        ("source",),
        ("normalize",),
        ("refund",),
    ]
    assert RouteSearcher().search(
        topology, {"order.read", "refund.execute"}, max_bridge_depth=0
    ) == ()


def test_respects_disabled_tools_and_edges() -> None:
    topology = build(("read", "analyze"), db, erp, policy_check)
    searcher = RouteSearcher()

    without_db = searcher.search(
        topology,
        {"order.read", "refund.policy.check"},
        disabled_tools={"db"},
    )
    assert [route.fingerprint for route in without_db] == [
        "read:[erp]|analyze:[policy_check]"
    ]

    without_erp_edge = searcher.search(
        topology,
        {"order.read", "refund.policy.check"},
        disabled_edges={("erp", "policy_check")},
    )
    assert [route.fingerprint for route in without_erp_edge] == [
        "read:[db]|analyze:[policy_check]"
    ]


def test_deduplicates_routes_when_one_tool_covers_multiple_capabilities() -> None:
    @tool(
        layer="read",
        capabilities={"order.read", "order.search"},
    )
    async def order_db(): return None

    topology = build(("read", "analyze"), order_db, policy_check)
    routes = RouteSearcher().search(
        topology,
        {"order.read", "order.search", "refund.policy.check"},
    )
    assert len(routes) == 1
    assert routes[0].layers[0].tools == ("order_db",)


def test_removes_obvious_redundant_bridge_supersets() -> None:
    @tool(layer="read", capabilities={"order.read"})
    async def source(): return None

    @tool(layer="transform")
    async def bridge_a(): return None

    @tool(layer="transform")
    async def bridge_b(): return None

    @tool(layer="act", capabilities={"refund.execute"})
    async def target(): return None

    topology = build(
        ("read", "transform", "act"), source, bridge_a, bridge_b, target
    )
    routes = RouteSearcher().search(
        topology, {"order.read", "refund.execute"}
    )
    assert [route.layers[1].tools for route in routes] == [
        ("bridge_a",),
        ("bridge_b",),
    ]


def test_limits_candidate_count_deterministically() -> None:
    @tool(layer="read", capabilities={"order.read"})
    async def cache(): return None

    topology = build(("read", "analyze"), db, erp, cache, policy_check)
    searcher = RouteSearcher()
    first = searcher.search(
        topology,
        {"order.read", "refund.policy.check"},
        max_candidate_routes=2,
    )
    second = searcher.search(
        topology,
        {"order.read", "refund.policy.check"},
        max_candidate_routes=2,
    )
    assert len(first) == 2
    assert [route.route_id for route in first] == [route.route_id for route in second]


def test_missing_capability_returns_no_route_and_invalid_options_raise() -> None:
    topology = build(("read",), db)
    searcher = RouteSearcher()
    assert searcher.search(topology, {"invoice.send"}) == ()

    with pytest.raises(RouteSearchError):
        searcher.search(topology, {"order.read"}, max_candidate_routes=0)
    with pytest.raises(RouteSearchError):
        searcher.search(topology, {"order.read"}, disabled_tools={"missing"})
