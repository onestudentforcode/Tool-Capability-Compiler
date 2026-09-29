"""Offline refund-domain tools + topology for the Slow Regression demo.

Battlefield-hardening batch C: the ten tools no longer return constants. They
read the module-level :data:`store.STORE` sandbox, so outcomes depend on the
active fixture variant (eligible / ineligible / high_risk / not_found /
erp_down), fail for real reasons, and carry differentiated cost and latency:

    Layer 1 — read       OrderDB, ERP, RAG, WebSearch
    Layer 2 — analyze    RefundPolicyCheck, RiskCheck, OrderSummarizer
    Layer 3 — action     RefundAPI, SendEmail, CreateTicket

Still fully offline: no LLM, no network, no database — plus simulated latency
inside the tool bodies so route latency actually differentiates.
"""

import asyncio

# NOTE: no `from __future__ import annotations` here — ToolExecutor resolves
# tool arguments by inspecting the real (non-string) type annotations.

from capability_runtime import (
    CompositeEvaluator,
    EvaluationResult,
    Evaluator,
    ExecutionState,
    FinalResult,
    LayerRegistry,
    ToolRegistry,
    TopologyBuilder,
    tool,
)

import store
from facts import (
    Digest,
    EmailSent,
    ErpRecord,
    Order,
    Passage,
    PolicyDecision,
    RefundResult,
    RiskReport,
    SearchResult,
    Ticket,
)

# ---- layer 1: read --------------------------------------------------------


@tool(layer="read", produces=[Order], cost_per_call=0.001,
      capabilities={"order.read", "order.search"})
async def order_db() -> Order:
    await asyncio.sleep(0.003)
    order = store.STORE.order
    if order is None:
        raise LookupError(f"order not found (variant={store.STORE.variant})")
    return order


@tool(layer="read", produces=[ErpRecord], cost_per_call=0.003,
      capabilities={"erp.read"})
async def erp() -> ErpRecord:
    await asyncio.sleep(0.012)
    if not store.STORE.erp_available:
        raise ConnectionError("ERP backend unavailable")
    record = store.STORE.erp_record
    if record is None:
        raise LookupError(f"order not found (variant={store.STORE.variant})")
    return record


@tool(layer="read", produces=[Passage], cost_per_call=0.002,
      capabilities={"refund.policy.read"})
async def rag() -> Passage:
    await asyncio.sleep(0.02)
    return Passage(text="Refund policy accepts full refund within 30 days.")


@tool(layer="read", produces=[SearchResult], cost_per_call=0.005,
      capabilities={"web.search"})
async def web_search() -> SearchResult:
    await asyncio.sleep(0.05)
    return SearchResult(query="refund policy", summary="14-day return window applies.")


# ---- layer 2: analyze -----------------------------------------------------


@tool(layer="analyze", consumes=[Order], produces=[PolicyDecision],
      cost_per_call=0.001, capabilities={"refund.policy.check"})
async def policy_check(order: Order) -> PolicyDecision:
    await asyncio.sleep(0.002)
    decision = "approve" if order.eligible and order.amount <= 5000.0 else "reject"
    return PolicyDecision(decision=decision)


@tool(layer="analyze", consumes=[Order], produces=[RiskReport],
      cost_per_call=0.001, capabilities={"refund.risk.check"})
async def risk_check(order: Order) -> RiskReport:
    await asyncio.sleep(0.002)
    return RiskReport(score="low" if order.amount <= 2000.0 else "high")


@tool(layer="analyze", consumes=[Order], produces=[Digest], cost_per_call=0.001,
      capabilities={"order.summarize"})
async def summarizer(order: Order) -> Digest:
    await asyncio.sleep(0.004)
    return Digest(text=f"Order {order.order_id} totals {order.amount:.0f} {order.channel}")


# ---- layer 3: action ------------------------------------------------------


@tool(
    layer="action",
    consumes=[Order, PolicyDecision],
    produces=[RefundResult],
    cost_per_call=0.01, capabilities={"refund.execute"},
)
async def refund_api(order: Order, decision: PolicyDecision) -> RefundResult:
    await asyncio.sleep(0.008)
    # Idempotency guard: a second refund of the same order fails. This makes
    # fixture isolation observable — without a per-trial reset, later trials
    # of the same scenario would start seeing "already refunded".
    approved = decision.decision == "approve"
    already = order.order_id in store.STORE.refunded_order_ids
    success = approved and not already
    if success:
        store.STORE.record_refund(order.order_id)
    return RefundResult(
        order_id=order.order_id,
        success=success,
        refunded_amount=order.amount if success else 0.0,
    )


@tool(layer="action", consumes=[Order], produces=[EmailSent], cost_per_call=0.002,
      capabilities={"email.send"})
async def send_email(order: Order) -> EmailSent:
    await asyncio.sleep(0.003)
    return EmailSent(to="customer@example.com", subject=f"Order {order.order_id}")


@tool(layer="action", produces=[Ticket], cost_per_call=0.001,
      capabilities={"ticket.create"})
async def create_ticket() -> Ticket:
    await asyncio.sleep(0.002)
    return Ticket(ticket_id="TCK-9", priority="P2")


def build_topology(*, topology_version: str = "v0.3.1"):
    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "action")):
        layers.register(name, order)

    tools = ToolRegistry()
    for node in (
        order_db,
        erp,
        rag,
        web_search,
        policy_check,
        risk_check,
        summarizer,
        refund_api,
        send_email,
        create_ticket,
    ):
        tools.register(node)
    topology = TopologyBuilder(layers, tools).build()
    return topology, topology_version


# ---- evaluation: business success + answer completeness -------------------


class RefundBusinessEvaluator(Evaluator):
    """A refund query succeeds only when a successful RefundResult was issued."""

    async def evaluate(
        self, scenario, result: FinalResult, trace
    ) -> EvaluationResult:
        refunded = _latest_refund(result.state_snapshot)
        success = refunded is not None and getattr(refunded, "success", False)
        return EvaluationResult(
            success=success,
            quality_score=1.0 if success else 0.0,
            reason=None if success else "no successful refund executed",
        )


class CompletenessEvaluator(Evaluator):
    """Quality-only dimension: how much of the useful answer is in the state.

    Always reports success — incompleteness lowers quality, it is not a
    business failure. The score is the fraction of {order, refund_result,
    digest} present, so routes that skip the summarizer land strictly between
    0 and 1.
    """

    _SLOTS = ("digest", "order", "refund_result")

    async def evaluate(
        self, scenario, result: FinalResult, trace
    ) -> EvaluationResult:
        state = result.state_snapshot
        names = state.names() if isinstance(state, ExecutionState) else ()
        present = sum(1 for name in self._SLOTS if name in names)
        score = present / len(self._SLOTS)
        return EvaluationResult(success=True, quality_score=score)


def build_evaluator() -> CompositeEvaluator:
    """Business outcome at 70% + answer completeness at 30% (phase3 §73)."""
    return CompositeEvaluator(
        {
            "refund_business": (RefundBusinessEvaluator(), 0.7),
            "answer_completeness": (CompletenessEvaluator(), 0.3),
        }
    )


def _latest_refund(state: ExecutionState):
    if state is None:
        return None
    latest = state.latest("refund_result")
    if latest is None:
        return None
    return latest.value
