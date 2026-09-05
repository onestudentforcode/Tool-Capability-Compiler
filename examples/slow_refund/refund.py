"""Offline refund-domain tools + topology for the Slow Regression demo (phase3 §109-111).

Three layers, ten tools, all deterministic in-memory fake implementations so the
demo runs with no LLM, no network and no database:

    Layer 1 — read       OrderDB, ERP, RAG, WebSearch
    Layer 2 — analyze    RefundPolicyCheck, RiskCheck, OrderSummarizer
    Layer 3 — action     RefundAPI, SendEmail, CreateTicket
"""

from dataclasses import dataclass

from capability_runtime import (
    EvaluationResult,
    Evaluator,
    ExecutionState,
    FinalResult,
    LayerRegistry,
    ToolRegistry,
    TopologyBuilder,
    tool,
)


# ---- domain facts ---------------------------------------------------------


@dataclass(frozen=True)
class Order:
    order_id: str
    amount: float
    eligible: bool
    channel: str


@dataclass(frozen=True)
class ErpRecord:
    order_id: str
    warehouse: str
    verified: bool


@dataclass(frozen=True)
class Passage:
    text: str


@dataclass(frozen=True)
class SearchResult:
    query: str
    summary: str


@dataclass(frozen=True)
class PolicyDecision:
    decision: str


@dataclass(frozen=True)
class RiskReport:
    score: str


@dataclass(frozen=True)
class Digest:
    text: str


@dataclass(frozen=True)
class RefundResult:
    order_id: str
    success: bool
    refunded_amount: float


@dataclass(frozen=True)
class EmailSent:
    to: str
    subject: str


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    priority: str


# ---- layer 1: read --------------------------------------------------------


@tool(layer="read", produces=[Order])
async def order_db() -> Order:
    return Order(order_id="ORD-123", amount=1200.0, eligible=True, channel="web")


@tool(layer="read", produces=[ErpRecord])
async def erp() -> ErpRecord:
    return ErpRecord(order_id="ORD-123", warehouse="WH-A", verified=True)


@tool(layer="read", produces=[Passage])
async def rag() -> Passage:
    return Passage(text="Refund policy accepts full refund within 30 days.")


@tool(layer="read", produces=[SearchResult])
async def web_search() -> SearchResult:
    return SearchResult(query="refund policy", summary="14-day return window applies.")


# ---- layer 2: analyze -----------------------------------------------------


@tool(layer="analyze", consumes=[Order], produces=[PolicyDecision])
async def policy_check(order: Order) -> PolicyDecision:
    decision = "approve" if order.eligible and order.amount <= 5000.0 else "reject"
    return PolicyDecision(decision=decision)


@tool(layer="analyze", consumes=[Order], produces=[RiskReport])
async def risk_check(order: Order) -> RiskReport:
    return RiskReport(score="low" if order.amount <= 2000.0 else "high")


@tool(layer="analyze", consumes=[Order], produces=[Digest])
async def summarizer(order: Order) -> Digest:
    return Digest(text=f"Order {order.order_id} totals {order.amount:.0f} {order.channel}")


# ---- layer 3: action ------------------------------------------------------


@tool(layer="action", consumes=[Order, PolicyDecision], produces=[RefundResult])
async def refund_api(order: Order, decision: PolicyDecision) -> RefundResult:
    success = decision.decision == "approve"
    return RefundResult(
        order_id=order.order_id,
        success=success,
        refunded_amount=order.amount if success else 0.0,
    )


@tool(layer="action", consumes=[Order], produces=[EmailSent])
async def send_email(order: Order) -> EmailSent:
    return EmailSent(to="customer@example.com", subject=f"Order {order.order_id}")


@tool(layer="action", produces=[Ticket])
async def create_ticket() -> Ticket:
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


class RefundEvaluator(Evaluator):
    """Business success for a refund query: a successful RefundResult was issued."""

    _MISSING = object()

    async def evaluate(
        self, scenario, result: FinalResult, trace
    ) -> EvaluationResult:
        success = False
        refunded = _latest_refund(result.state_snapshot)
        if refunded is not None:
            success = getattr(refunded, "success", False)
        return EvaluationResult(
            success=success,
            quality_score=1.0 if success else 0.0,
            reason=None if success else "no successful refund executed",
        )


def _latest_refund(state: ExecutionState):
    if state is None:
        return None
    latest = state.latest("refund_result")
    if latest is None:
        return None
    return latest.value