"""In-memory sandbox store driving the refund demo tools.

Battlefield-hardening batch C made the tools data-dependent; the
resource-metering milestone (batch B) moved the data access onto metered
handles: reads/writes now flow through :class:`InMemoryStore` and are counted
onto the tool invocation automatically. The fixture variants, failure
injection and idempotency guard are unchanged — only the access path moved.

Variants:

    eligible     order may be refunded end-to-end
    ineligible   order exists but policy rejects it (eligible=False)
    high_risk    order amount exceeds the policy ceiling -> reject
    not_found    the order does not exist -> read tools fail
    erp_down     the ERP backend is unavailable -> erp tool fails
"""

from __future__ import annotations

from dataclasses import dataclass, field

from capability_runtime.resources import InMemoryStore

try:  # package import (python -m from project root) vs direct script import
    from .facts import ErpRecord, Order
except ImportError:
    from facts import ErpRecord, Order

VARIANTS = ("eligible", "ineligible", "high_risk", "not_found", "erp_down")

_DEFAULT = "eligible"

_VARIANT_ORDERS = {
    "eligible": Order("ORD-100", 1200.0, eligible=True, channel="web"),
    "ineligible": Order("ORD-200", 300.0, eligible=False, channel="app"),
    "high_risk": Order("ORD-300", 8000.0, eligible=True, channel="api"),
    "erp_down": Order("ORD-100", 1200.0, eligible=True, channel="web"),
}


@dataclass
class SandboxStore:
    """The whole mutable sandbox state for one demo run.

    ``orders`` / ``erp_records`` / ``refunds`` are metered handles: tools must
    use their async accessors so reads/writes are counted. ``reset`` seeds via
    the unmetered administrative path (fixtures run outside tool calls).
    """

    variant: str = _DEFAULT
    erp_available: bool = True
    orders: InMemoryStore = field(
        default_factory=lambda: InMemoryStore("sandbox_orders")
    )
    erp_records: InMemoryStore = field(
        default_factory=lambda: InMemoryStore("sandbox_erp")
    )
    refunds: InMemoryStore = field(
        default_factory=lambda: InMemoryStore("sandbox_refunds")
    )
    _refunded: set[str] = field(default_factory=set)

    def reset(self, variant: str = _DEFAULT) -> None:
        if variant not in VARIANTS:
            raise ValueError(
                f"unknown sandbox variant {variant!r}; expected one of {VARIANTS}"
            )
        self.variant = variant
        self._refunded.clear()
        self.erp_available = variant != "erp_down"
        for handle in (self.orders, self.erp_records, self.refunds):
            handle.clear()
        order = _VARIANT_ORDERS.get(variant)
        if order is not None:
            self.orders.seed({order.order_id: order})
            self.erp_records.seed(
                {order.order_id: ErpRecord(order.order_id, "WH-A", verified=True)}
            )

    # ---- read-only views (tests / fixtures; not the tools' access path) ----

    @property
    def target_order_id(self) -> str | None:
        return next(iter(self.orders.snapshot()), None)

    @property
    def order(self) -> Order | None:
        order_id = self.target_order_id
        return self.orders.snapshot().get(order_id) if order_id else None

    @property
    def erp_record(self) -> ErpRecord | None:
        order_id = self.target_order_id
        return self.erp_records.snapshot().get(order_id) if order_id else None

    # ---- business state (idempotency guard) ----------------------------------

    def record_refund(self, order_id: str) -> None:
        self._refunded.add(order_id)

    @property
    def refunded_order_ids(self) -> frozenset[str]:
        return frozenset(self._refunded)


# Module-level sandbox; the demo fixture manager resets it per scenario/trial.
STORE = SandboxStore()
STORE.reset(_DEFAULT)
