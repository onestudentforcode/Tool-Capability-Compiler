"""Office baseline snapshot + budget-decoupling assertions (batch A).

Anchors three guarantees on the real office domain (60 scenarios):

1. Baseline self-consistency — the production RouteSearch reproduces the
   frozen fixture (determinism across time/environment). Batch B is
   expected to change ``route_count``/``fingerprints`` (that is the point);
   the test is then narrowed per acceptance doc §3.3 to the budget-free
   columns (feasible/covered) plus legality/count-baseline totals.
2. Witness-fallback contract — feasible scenarios always yield at least
   one route, infeasible ones never do (invariant 2's user-visible face).
3. Budget decoupling — on a synthetic truncating topology, verdicts are
   identical under different enumeration budgets (the office sweep above
   already proves verdicts match the budget-free witness on every
   scenario; a second full office sweep would double runtime for nothing).

One deliberate single sweep over office: the enumeration cost is the
point of this milestone, keep the gate honest but not redundant.
"""

from __future__ import annotations

import json
from pathlib import Path

from capability_runtime import (
    CapabilityRegistry,
    RouteSearch,
    ToolRegistry,
    LayerRegistry,
    Topology,
    TopologyBuilder,
    tool,
)
from capability_runtime.scenario.loader import ScenarioLoader
from capability_runtime.topology.loader import TopologyLoader

FIXTURE = Path(__file__).parent / "fixtures" / "routesearch_office_baseline.json"
ROOT = Path(__file__).parents[2]


def _office_search() -> RouteSearch:
    topology = TopologyLoader().load_file(ROOT / "examples/topology/office.json")
    registry = CapabilityRegistry()
    for name in topology.nodes():
        for capability in sorted(topology.node(name).spec.capabilities):
            registry.register(capability, name)
    return RouteSearch(
        topology, registry, max_candidate_routes=20, max_expansions=100_000
    )


def _fixture_rows() -> list[dict]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["_meta"]["budgets"] == {
        "max_candidate_routes": 20,
        "max_expansions": 100_000,
    }, "fixture budgets must match production defaults"
    return payload["scenarios"]


def test_office_baseline_self_consistent_and_witness_contract() -> None:
    search = _office_search()
    rows = _fixture_rows()
    assert len(rows) == 60
    total_routes = 0
    for row in rows:
        required = tuple(row["required"])
        routes = search.search(required)
        # budget-free columns: must never change across batches
        assert search.is_feasible(required) is row["feasible"], row["id"]
        assert list(search.covered_capabilities(required)) == row["covered"], row["id"]
        # witness-fallback contract: feasible <=> at least one route
        assert bool(routes) is row["feasible"], row["id"]
        # default-budget columns: baseline of the subset era
        assert [r.fingerprint for r in routes] == row["fingerprints"], row["id"]
        total_routes += len(routes)
    assert total_routes == sum(r["route_count"] for r in rows)


def _synthetic_truncating_topology() -> Topology:
    # 6x6 full-bipartite layers: subset enumeration hits the 100k budget,
    # so verdicts and the budget decouple empirically here.
    layer_registry = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "compose")):
        layer_registry.register(name, order)
    tool_registry = ToolRegistry()
    names = {
        "read": [f"reader_{i}" for i in range(6)],
        "analyze": [f"agg_{i}" for i in range(6)],
        "compose": [f"writer_{i}" for i in range(6)],
    }
    for index in range(6):
        async def reader(): return None
        reader.__name__ = names["read"][index]
        caps = {"order.read"} if index else {"order.read", "policy.read"}
        tool_registry.register(tool(
            layer="read", workers=names["analyze"], capabilities=caps
        )(reader))
    for index in range(6):
        async def agg(): return None
        agg.__name__ = names["analyze"][index]
        caps = {"agg.compute"} if index else {"agg.compute", "order.read"}
        tool_registry.register(tool(
            layer="analyze",
            providers=names["read"], workers=names["compose"],
            capabilities=caps,
        )(agg))
    for index in range(6):
        async def writer(): return None
        writer.__name__ = names["compose"][index]
        caps = {"report.write"} if index else {"report.write", "agg.compute"}
        tool_registry.register(tool(
            layer="compose", providers=names["analyze"], capabilities=caps
        )(writer))
    return TopologyBuilder(layer_registry, tool_registry).build()


def test_budget_variation_keeps_verdicts_stable() -> None:
    topology = _synthetic_truncating_topology()

    def make(expansions: int) -> RouteSearch:
        registry = CapabilityRegistry()
        for name in topology.nodes():
            for capability in sorted(topology.node(name).spec.capabilities):
                registry.register(capability, name)
        return RouteSearch(
            topology, registry,
            max_candidate_routes=20, max_expansions=expansions,
        )

    low, high = make(2_000), make(400_000)
    for required in (
        ("order.read", "agg.compute", "report.write"),
        ("policy.read", "report.write"),
        ("order.read", "report.write"),
        ("no.such.capability",),
    ):
        assert low.is_feasible(required) is high.is_feasible(required)
        assert (
            low.covered_capabilities(required)
            == high.covered_capabilities(required)
        )
