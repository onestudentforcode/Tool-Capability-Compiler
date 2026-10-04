"""Composite-node demo: final capability as a macro node (composite-nodes §15).

Outer topology:

    read:[order_db] -> analyze:[policy_check] -> act:[refund_handler*]

``refund_handler`` is a composite node: inside it the inner verify/guard/refund
topology refines over bounded iterations until ``refund_result`` exists on the
inner blackboard. The demo prints the three closure properties:

1. the outer trace carries the per-iteration inner detail (composite_detail);
2. inner metering rolls up onto the outer bill (access counts);
3. the flattener feeds the inner evidence into the exact Phase 4 aggregator.

Usage::

    python run_demo.py [--trials 3]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "src"))
sys.path.insert(0, str(_HERE.parent / "slow_refund"))

from capability_runtime import (  # noqa: E402
    CompositeSpec,
    EvidenceAggregator,
    FakeRouter,
    LayerRegistry,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    ToolRegistry,
    TopologyBuilder,
    build_composite_node,
    build_observation_stats,
)
from capability_runtime.composite import flatten_composite_results  # noqa: E402

import store  # noqa: E402
import facts  # noqa: E402
from fixtures import SandboxFixtureManager  # noqa: E402
from refund import build_evaluator, order_db, policy_check  # noqa: E402
from inner_tools import build_inner_topology, reset_inner_store  # noqa: E402

_TOPOLOGY_VERSION = "v0.8.0"


def build_outer_topology():
    """Outer three-layer topology whose action node is the composite."""
    inner = build_inner_topology()
    handler_spec = CompositeSpec(
        name="refund_handler",
        layer="act",
        topology=inner,
        route=(("verify",), ("holdover", "issue_refund")),
        stop_when=("refund_result",),
        max_iterations=3,
        consumes=(facts.Order, facts.PolicyDecision),
        produces=(facts.RefundResult,),
        capabilities=frozenset({"refund.handle"}),
        cost_per_call=0.02,
        description="Handle a refund end-to-end, refine until issued",
    )
    refund_handler = build_composite_node(handler_spec)

    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "act")):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in (order_db, policy_check, refund_handler):
        tools.register(node)
    topology = TopologyBuilder(layers, tools).build()
    return topology, inner, handler_spec


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Composite node demo")
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args(argv)

    topology, inner, handler_spec = build_outer_topology()
    store.STORE.reset("eligible")

    suite = ScenarioSuite(
        name="composite_refund",
        version="1.0",
        description="composite node demo",
        scenarios=(
            Scenario(
                id="refund_full",
                query="handle this refund end-to-end",
                category="refund",
                metadata={"fixture": "eligible"},
            ),
        ),
    )
    from fixtures import SandboxFixtureManager as _Sandbox

    class DemoFixture(_Sandbox):
        """Sandbox fixture + inner-store reset: trial isolation for both."""

        async def setup(self, scenario, trial):
            reset_inner_store()
            return await super().setup(scenario, trial)

    router = FakeRouter(
        layer_selections={
            "read": ["order_db"],
            "analyze": ["policy_check"],
            "action": ["refund_handler"],
        }
    )
    outcome = await SlowRegressionRunner(
        topology=topology,
        evaluator=build_evaluator(),
        fixture_manager=DemoFixture(),
        trials_per_scenario=args.trials,
        topology_version=_TOPOLOGY_VERSION,
        router_config_id="fake-composite",
        router=router,
    ).run(suite)

    print("Composite node demo — outer trials:")
    total_iterations = 0
    for result in outcome.results:
        iterations = 0
        for layer in result.trace.layers:
            for execution in layer.tool_executions:
                if execution.composite_detail:
                    iterations += len(execution.composite_detail)
        total_iterations += iterations
        evaluation = result.evaluation
        print(
            f"  {result.trial.id}: {result.execution_status.value:<10}"
            f" success={evaluation.success if evaluation else None}"
            f"  composite_iterations={iterations}"
            f"  cost={result.cost}  access={result.access_counts}"
        )

    # ---- loop closure: inner evidence through the flattener -----------------
    pseudo = flatten_composite_results(
        outcome.results, {"refund_handler": handler_spec}
    )
    inner_edges = [
        (edge.source, edge.target) for edge in inner.edges()
    ]
    obs = build_observation_stats(pseudo, edges=inner_edges)
    evidence = EvidenceAggregator().build(
        report=obs, results=pseudo, edges=inner_edges
    )
    print(
        f"\nFlattened inner evidence: {len(pseudo)} pseudo trials "
        f"(outer trials x inner iterations), "
        f"{len(evidence.node_evidence)} inner nodes / "
        f"{len(evidence.edge_evidence)} inner edges"
    )
    for name in sorted(evidence.node_evidence):
        node = evidence.node_evidence[name]
        print(
            f"  {name:<14} available={node.available_count:<3} "
            f"selected={node.selected_count:<3}"
        )
    print(
        "\n(inner edges never entered the outer stats; the same Phase 4 "
        "machinery now applies to the inner topology.)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
