"""Equivalence oracle for the RouteSearch generator (batch A safety net).

Under exhaustive budgets (expansions + route cap both raised so both
generators terminate by exhaustion, never by budget), the production
RouteSearch and the frozen subset-era ReferenceRouteSearch must produce
the identical ordered route set. Batch B swaps the production generator
to assignment-driven enumeration; this file then becomes the hard
correctness gate (acceptance doc §3.3 / §5).
"""

from __future__ import annotations

import pytest

from capability_runtime import (
    CapabilityRegistry,
    RouteSearch,
    ToolRegistry,
    LayerRegistry,
    Topology,
    TopologyBuilder,
    tool,
)

from _routesearch_reference import ReferenceRouteSearch

# Exhaustive-oracle budgets: termination by exhaustion, never by budget.
ORACLE_EXPANSIONS = 5_000_000
ORACLE_ROUTE_CAP = 200


def build(layers: tuple[str, ...], *nodes: object) -> Topology:
    layer_registry = LayerRegistry()
    for order, name in enumerate(layers):
        layer_registry.register(name, order)
    tool_registry = ToolRegistry()
    for node in nodes:
        tool_registry.register(node)
    return TopologyBuilder(layer_registry, tool_registry).build()


def registry_for(topology: Topology) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for name in topology.nodes():
        for capability in topology.node(name).spec.capabilities:
            registry.register(capability, name)
    return registry


def _oracles(topology: Topology):
    # RouteSearch keeps the registry for interface compatibility; capability
    # facts are read from the topology itself, so one shared registry is fine.
    registry = registry_for(topology)
    current = RouteSearch(
        topology, registry,
        max_candidate_routes=ORACLE_ROUTE_CAP,
        max_expansions=ORACLE_EXPANSIONS,
    )
    reference = ReferenceRouteSearch(
        topology, registry,
        max_candidate_routes=ORACLE_ROUTE_CAP,
        max_expansions=ORACLE_EXPANSIONS,
    )
    return current, reference


def assert_equivalent(topology: Topology, required: set[str]) -> None:
    current, reference = _oracles(topology)
    got = current.search(tuple(sorted(required)))
    want = reference.search(tuple(sorted(required)))
    assert [r.fingerprint for r in got] == [r.fingerprint for r in want]


# ---- corpus: every shape that exercises the generator differently --------


def _narrow_two_layer() -> Topology:
    @tool(layer="read", workers=["policy"], capabilities={"order.read"})
    async def db(): return None

    @tool(layer="read", workers=["policy"], capabilities={"order.read"})
    async def erp(): return None

    @tool(layer="analyze", providers=["db", "erp"], capabilities={"refund.policy.check"})
    async def policy(): return None

    return build(("read", "analyze"), db, erp, policy)


def _wide_redundant_three_layer() -> Topology:
    # 3x4x3 tools, overlapping multi-capability providers: the interesting
    # middle ground — wide enough for real subset combinations, still
    # exhaustible under the oracle budget.
    nodes = []
    reader_names = [f"reader_{i}" for i in range(3)]
    agg_names = [f"agg_{i}" for i in range(4)]
    writer_names = [f"writer_{i}" for i in range(3)]
    for index in range(3):
        async def reader(): return None
        reader.__name__ = reader_names[index]
        caps = {"order.read"} | ({"policy.read"} if index == 0 else set())
        nodes.append(tool(
            layer="read", workers=agg_names, capabilities=caps
        )(reader))
    for index in range(4):
        async def agg(): return None
        agg.__name__ = agg_names[index]
        caps = {"agg.compute"} | ({"order.read"} if index == 2 else set())
        nodes.append(tool(
            layer="analyze", providers=reader_names, workers=writer_names,
            capabilities=caps,
        )(agg))
    for index in range(3):
        async def writer(): return None
        writer.__name__ = writer_names[index]
        caps = {"report.write"} | ({"agg.compute"} if index == 1 else set())
        nodes.append(tool(
            layer="compose", providers=agg_names, capabilities=caps
        )(writer))
    return build(("read", "analyze", "compose"), *nodes)


def _deep_bridge_chain() -> Topology:
    @tool(layer="read", workers=["b1"], capabilities={"order.read"})
    async def source(): return None

    @tool(layer="enrich", providers=["source"], workers=["b2", "b2b"])
    async def b1(): return None

    @tool(layer="normalize", providers=["b1"], workers=["b3"])
    async def b2(): return None

    @tool(layer="normalize", providers=["b1"], workers=["b3"])
    async def b2b(): return None

    @tool(layer="transform", providers=["b2", "b2b"], workers=["target"])
    async def b3(): return None

    @tool(layer="act", providers=["b3"], capabilities={"refund.execute"})
    async def target(): return None

    return build(("read", "enrich", "normalize", "transform", "act"),
                 source, b1, b2, b2b, b3, target)


def _parallel_single_layer() -> Topology:
    @tool(layer="read", workers=[], capabilities={"order.read"})
    async def only_db(): return None

    @tool(layer="read", workers=[], capabilities={"policy.read"})
    async def only_rag(): return None

    @tool(layer="read", workers=[], capabilities={"order.read", "policy.read"})
    async def both(): return None

    return build(("read",), only_db, only_rag, both)


def _disconnected() -> Topology:
    @tool(layer="read", workers=[], capabilities={"order.read"})
    async def isolated_read(): return None

    @tool(layer="analyze", providers=[], capabilities={"refund.policy.check"})
    async def isolated_analyze(): return None

    return build(("read", "analyze"), isolated_read, isolated_analyze)


CORPUS = {
    "narrow": (_narrow_two_layer, [{"order.read", "refund.policy.check"}]),
    "wide": (_wide_redundant_three_layer,
             [{"order.read", "agg.compute", "report.write"},
              {"policy.read", "report.write"},
              {"order.read", "agg.compute"}]),
    "bridge": (_deep_bridge_chain, [{"order.read", "refund.execute"}]),
    "parallel": (_parallel_single_layer,
                 [{"order.read", "policy.read"}, {"order.read"}]),
    "disconnected": (_disconnected, [{"order.read", "refund.policy.check"}]),
}


@pytest.mark.parametrize("corpus_name", sorted(CORPUS))
def test_oracle_equivalence_under_exhaustive_budget(corpus_name: str) -> None:
    factory, required_sets = CORPUS[corpus_name]
    topology = factory()
    for required in required_sets:
        assert_equivalent(topology, required)


def test_oracle_repeatability_current_implementation() -> None:
    # The oracle's other half: the production implementation itself must be
    # deterministic under oracle budgets (guards the comparison's meaning).
    topology = _wide_redundant_three_layer()
    search = RouteSearch(
        topology, registry_for(topology),
        max_candidate_routes=ORACLE_ROUTE_CAP,
        max_expansions=ORACLE_EXPANSIONS,
    )
    first = search.search(("order.read", "agg.compute", "report.write"))
    second = search.search(("order.read", "agg.compute", "report.write"))
    assert [r.fingerprint for r in first] == [r.fingerprint for r in second]
