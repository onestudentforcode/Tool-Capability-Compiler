"""Domain facts shared by the refund sandbox store, tools and evaluator."""

from __future__ import annotations

from dataclasses import dataclass


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
