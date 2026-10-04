"""Inner tools of the composite refund handler (composite-nodes demo).

Inner topology (2 layers):

    check: verify                -> increments a confirmation counter (metered)
    act:   holdover              -> always succeeds (keeps the layer alive)
           issue_refund          -> releases the refund only after the
                                    counter reached 2 (double confirmation)

The refine loop: iteration 1's issue_refund sees confirm_count == 1 and
raises; the layer survives on holdover (partial failure continues), and
``refund_result`` is absent, so the loop continues. Iteration 2 increments
the counter to 2 and completes the refund, satisfying
``stop_when=("refund_result",)``. Deterministic two-iteration refine with no
hidden control flow — the gate lives on a metered store across iterations.
"""

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent / "src"))
sys.path.insert(0, str(_HERE.parent / "slow_refund"))

# NOTE: no `from __future__ import annotations` here — ToolExecutor resolves
# tool arguments by inspecting real (non-string) type annotations (same
# caveat as refund.py).

from capability_runtime import LayerRegistry, ToolRegistry, TopologyBuilder, tool
from capability_runtime.resources import InMemoryStore

from facts import Order, PolicyDecision, RefundResult
import store


@dataclass(frozen=True)
class VerifyNote:
    order_id: str


@dataclass(frozen=True)
class HoldNote:
    order_id: str


# The cross-iteration gate: a metered store the demo fixture resets per trial.
CONFIRM = InMemoryStore("composite_confirm")


def reset_inner_store() -> None:
    CONFIRM.clear()


@tool(layer="check", consumes=[Order], produces=[VerifyNote],
      capabilities={"refund.verify"}, cost_per_call=0.001)
async def verify(order: Order) -> VerifyNote:
    await asyncio.sleep(0.002)
    count = (await CONFIRM.get("confirm_count")) or 0
    await CONFIRM.put("confirm_count", count + 1)
    return VerifyNote(order_id=order.order_id)


@tool(layer="act", consumes=[Order], produces=[HoldNote],
      capabilities={"refund.hold"}, cost_per_call=0.001)
async def holdover(order: Order) -> HoldNote:
    await asyncio.sleep(0.002)
    return HoldNote(order_id=order.order_id)


@tool(layer="act", consumes=[Order, PolicyDecision],
      produces=[RefundResult], capabilities={"refund.execute.inner"},
      cost_per_call=0.008)
async def issue_refund(order: Order, decision: PolicyDecision) -> RefundResult:
    await asyncio.sleep(0.004)
    count = (await CONFIRM.get("confirm_count")) or 0
    if count < 2:
        raise RuntimeError(
            f"awaiting double confirmation (count={count})"
        )
    success = decision.decision == "approve"
    if success:
        await store.STORE.refunds.put(
            order.order_id, {"amount": order.amount, "via": "composite"}
        )
    return RefundResult(
        order_id=order.order_id,
        success=success,
        refunded_amount=order.amount if success else 0.0,
    )


def build_inner_topology():
    layers = LayerRegistry()
    layers.register("check", 0)
    layers.register("act", 1)
    tools = ToolRegistry()
    for node in (verify, holdover, issue_refund):
        tools.register(node)
    return TopologyBuilder(layers, tools).build()
