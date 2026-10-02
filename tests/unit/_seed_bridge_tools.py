"""Seed-bridge fixture tools: a 3-layer deterministic topology, no LLM.

Layer read:    source_bad  (data.read -> Order; always raises — statically
                            identical to source_good, dynamically broken)
               source_good (data.read -> Order; works)
Layer analyze: decide     (task.decide -> Decision; consumes Order)
Layer act:     archive    (act.archive -> Archive; consumes Decision)

The redundant data.read pair is the flagship seed-bridge case: the
shortest candidate chain picks source_bad (fails at runtime) and the
bridge must fall through to source_good. Note RouteSearch only emits
subset-minimal chains, so "add one more producer" supersets never appear
as candidates — repair candidates must use different tools.

``build_topology`` assembles them for in-process use; the CLI tests bind
the same attributes via ``implementation`` strings.
"""

from dataclasses import dataclass

from capability_runtime import (
    LayerRegistry,
    ToolRegistry,
    TopologyBuilder,
    tool,
)


@dataclass(frozen=True)
class Order:
    order_id: str


@dataclass(frozen=True)
class Decision:
    value: str


@dataclass(frozen=True)
class Archive:
    name: str


@tool(layer="read", produces=[Order], cost_per_call=0.001,
      capabilities={"data.read"})
async def source_bad() -> Order:
    raise RuntimeError("source_bad always fails")


@tool(layer="read", produces=[Order], cost_per_call=0.001,
      capabilities={"data.read"})
async def source_good() -> Order:
    return Order(order_id="O-1")


@tool(layer="analyze", consumes=[Order], produces=[Decision],
      cost_per_call=0.002, capabilities={"task.decide"})
async def decide(order: Order) -> Decision:
    return Decision(value=order.order_id)


@tool(layer="act", consumes=[Decision], produces=[Archive],
      cost_per_call=0.001, capabilities={"act.archive"})
async def archive(decision: Decision) -> Archive:
    return Archive(name=decision.value)


def build_topology():
    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "act")):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in (source_bad, source_good, decide, archive):
        tools.register(node)
    return TopologyBuilder(layers, tools).build()
