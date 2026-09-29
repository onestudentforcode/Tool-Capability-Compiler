"""In-memory sandbox store driving the refund demo tools.

Battlefield-hardening batch C: tools stop returning constants and read this
store instead, so fixture variants produce genuinely different business
outcomes, failure paths and metering. Everything is deterministic — resetting
to the same variant always yields the same behavior.

Variants:

    eligible     order may be refunded end-to-end
    ineligible   order exists but policy rejects it (eligible=False)
    high_risk    order amount exceeds the policy ceiling -> reject
    not_found    the order does not exist -> read tools fail
    erp_down     the ERP backend is unavailable -> erp tool fails
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    """The whole mutable sandbox state for one demo run."""

    variant: str = _DEFAULT
    order: Order | None = None
    erp_record: ErpRecord | None = None
    erp_available: bool = True
    _refunded: set[str] = field(default_factory=set)

    def reset(self, variant: str = _DEFAULT) -> None:
        if variant not in VARIANTS:
            raise ValueError(
                f"unknown sandbox variant {variant!r}; expected one of {VARIANTS}"
            )
        self.variant = variant
        self._refunded.clear()
        self.erp_available = variant != "erp_down"
        self.order = _VARIANT_ORDERS.get(variant)
        self.erp_record = (
            ErpRecord(self.order.order_id, warehouse="WH-A", verified=True)
            if self.order is not None
            else None
        )

    def record_refund(self, order_id: str) -> None:
        self._refunded.add(order_id)

    @property
    def refunded_order_ids(self) -> frozenset[str]:
        return frozenset(self._refunded)


# Module-level sandbox; the demo fixture manager resets it per scenario/trial.
STORE = SandboxStore()
STORE.reset(_DEFAULT)
