from __future__ import annotations

import pytest

from capability_runtime import (
    CapabilityRegistry,
    CandidateRoute,
    LayerRegistry,
    RouteLayer,
    RouteSearch,
    ToolRegistry,
    TopologyBuilder,
    tool,
)


def _build(layers: tuple[str, ...], nodes: tuple[object, ...]):
    layer_registry = LayerRegistry()
    for order, name in enumerate(layers):
        layer_registry.register(name, order)
    tool_registry = ToolRegistry()
    for node in nodes:
        tool_registry.register(node)
    return layer_registry, tool_registry


def _search(
    layers: tuple[str, ...],
    nodes: tuple[object, ...],
    capabilities: set[str],
    **kwargs: int,
) -> tuple[CandidateRoute, ...]:
    layer_registry, tool_registry = _build(layers, nodes)
    topology = TopologyBuilder(layer_registry, tool_registry).build()
    registry = CapabilityRegistry()
    for name in topology.nodes():
        node = topology.node(name)
        for capability in node.spec.capabilities:
            registry.register(capability, name)
    return RouteSearch(topology, registry, **kwargs).search(tuple(sorted(capabilities)))


# Module-level tools: use "all" defaults so they work in any topology.


@tool(layer="read", capabilities={"order.read"})
async def db(): return None


@tool(layer="read", capabilities={"order.read"})
async def erp(): return None


@tool(layer="read", capabilities={"policy.read"})
async def rag(): return None


@tool(layer="analyze", capabilities={"refund.policy.check"})
async def policy_check(): return None


@tool(layer="act", capabilities={"refund.execute"})
async def refund(): return None


@tool(layer="read", workers=[], capabilities={"order.read"})
async def isolated_read(): return None


@tool(layer="analyze", providers=[], capabilities={"refund.policy.check"})
async def isolated_analyze(): return None


def test_simple_two_layer_route() -> None:
    routes = _search(
        ("read", "analyze"),
        (db, policy_check),
        {"order.read", "refund.policy.check"},
    )
    assert len(routes) == 1
    assert routes[0].layers == (
        RouteLayer("read", ("db",)),
        RouteLayer("analyze", ("policy_check",)),
    )


def test_respect_provider() -> None:
    @tool(layer="read", capabilities={"order.read"})
    async def strict_db(): return None

    @tool(layer="read", capabilities={"order.read"})
    async def loose_db(): return None

    @tool(
        layer="analyze",
        providers=["strict_db"],
        capabilities={"refund.policy.check"},
    )
    async def strict_policy(): return None

    routes = _search(
        ("read", "analyze"),
        (strict_db, loose_db, strict_policy),
        {"order.read", "refund.policy.check"},
    )
    assert len(routes) == 1
    assert routes[0].layers[0].tools == ("strict_db",)


def test_respect_worker() -> None:
    @tool(layer="read", workers=["policy_both"], capabilities={"order.read"})
    async def db_only_policy(): return None

    @tool(layer="read", workers=[], capabilities={"order.read"})
    async def db_only_other(): return None

    @tool(
        layer="analyze",
        providers=["db_only_policy", "db_only_other"],
        capabilities={"refund.policy.check"},
    )
    async def policy_both(): return None

    routes = _search(
        ("read", "analyze"),
        (db_only_policy, db_only_other, policy_both),
        {"order.read", "refund.policy.check"},
    )
    assert len(routes) == 1
    assert routes[0].layers[0].tools == ("db_only_policy",)


def test_multiple_nodes_per_layer() -> None:
    routes = _search(
        ("read", "analyze"),
        (db, rag, policy_check),
        {"order.read", "policy.read", "refund.policy.check"},
    )
    assert len(routes) >= 1
    multi_tool_route = next(
        (r for r in routes if r.layers[0].tools == ("db", "rag")), None
    )
    assert multi_tool_route is not None


def test_bridge_node_discovery() -> None:
    @tool(layer="read", capabilities={"order.read"})
    async def src(): return None

    @tool(layer="analyze", capabilities=set())
    async def normalize(): return None

    @tool(layer="act", capabilities={"refund.execute"})
    async def sink(): return None

    routes = _search(
        ("read", "analyze", "act"),
        (src, normalize, sink),
        {"order.read", "refund.execute"},
    )
    assert len(routes) == 1
    assert [layer.layer for layer in routes[0].layers] == ["read", "analyze", "act"]
    assert routes[0].layers[1].tools == ("normalize",)


def test_route_deduplication() -> None:
    routes = _search(
        ("read", "analyze"),
        (db, erp, policy_check),
        {"order.read", "refund.policy.check"},
    )
    route_ids = [r.route_id for r in routes]
    assert len(route_ids) == len(set(route_ids))


def test_max_route_count() -> None:
    routes = _search(
        ("read", "analyze"),
        (db, erp, policy_check),
        {"order.read", "refund.policy.check"},
        max_candidate_routes=1,
    )
    assert len(routes) == 1


def test_multi_provider_routes() -> None:
    routes = _search(
        ("read", "analyze"),
        (db, erp, policy_check),
        {"order.read", "refund.policy.check"},
    )
    first_layer_tools = {r.layers[0].tools for r in routes}
    assert ("db",) in first_layer_tools
    assert ("erp",) in first_layer_tools


def test_no_route_when_disconnected() -> None:
    routes = _search(
        ("read", "analyze"),
        (isolated_read, isolated_analyze),
        {"order.read", "refund.policy.check"},
    )
    assert routes == ()


def test_single_layer_route() -> None:
    @tool(layer="read", capabilities={"order.read", "policy.read"})
    async def combined(): return None

    routes = _search(
        ("read", "analyze"),
        (combined,),
        {"order.read", "policy.read"},
    )
    assert len(routes) == 1
    assert routes[0].layers == (RouteLayer("read", ("combined",)),)


def test_three_layer_route() -> None:
    routes = _search(
        ("read", "analyze", "act"),
        (db, policy_check, refund),
        {"order.read", "refund.policy.check", "refund.execute"},
    )
    assert len(routes) == 1
    assert [layer.layer for layer in routes[0].layers] == ["read", "analyze", "act"]


def test_optional_provider_at_extreme_layer() -> None:
    """Regression: an isolated relevant tool at the top layer must not force
    the search to require passing through it."""
    routes = _search(
        ("read", "analyze", "act"),
        (isolated_read, erp, policy_check, refund),
        {"order.read", "refund.policy.check", "refund.execute"},
    )
    assert len(routes) >= 1
    assert all("isolated_read" not in r.layers[0].tools for r in routes)


def test_bridge_depth_limit() -> None:
    @tool(layer="read", capabilities={"order.read"})
    async def s(): return None

    @tool(layer="analyze", capabilities=set())
    async def b1(): return None

    @tool(layer="act", capabilities={"refund.execute"})
    async def t(): return None

    routes = _search(
        ("read", "analyze", "act"),
        (s, b1, t),
        {"order.read", "refund.execute"},
    )
    assert len(routes) == 1


def test_deterministic_ordering() -> None:
    a = _search(
        ("read", "analyze"),
        (db, erp, policy_check),
        {"order.read", "refund.policy.check"},
    )
    b = _search(
        ("read", "analyze"),
        (db, erp, policy_check),
        {"order.read", "refund.policy.check"},
    )
    assert [r.route_id for r in a] == [r.route_id for r in b]


def test_route_id_fingerprint() -> None:
    routes = _search(
        ("read", "analyze"),
        (db, policy_check),
        {"order.read", "refund.policy.check"},
    )
    assert len(routes) == 1
    assert len(routes[0].route_id) == 16
    assert all(c in "0123456789abcdef" for c in routes[0].route_id)
