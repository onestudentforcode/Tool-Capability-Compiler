"""Declaration-time diagnostics (discovery-routing batch D, P6/P7).

UNSATISFIABLE_INPUT: a consumed type whose producers all live in the same
or a later layer (or nowhere) can never be satisfied at run time — same-
layer tools run concurrently, later layers have not executed yet.
SLOT_NAME_CONFLICT: argument resolution is name-first, so a parameter named
like a different type's slot binds the wrong artifact.

Regression guard: the office and refund domains trigger ZERO of these new
warnings (their declarations follow the conventions).
"""

# NOTE: no `from __future__ import annotations` here — the slot-conflict
# diagnostic inspects real (non-string) handler annotations.

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from capability_runtime import (  # noqa: E402
    LayerRegistry,
    ToolRegistry,
    TopologyBuildError,
    TopologyBuilder,
    tool,
)
from capability_runtime.core.errors import InvalidCapabilityError  # noqa: E402

from examples.office import office  # noqa: E402
from examples.slow_refund import refund  # noqa: E402

NEW_CODES = ("UNSATISFIABLE_INPUT", "SLOT_NAME_CONFLICT")


def _codes(topology):
    return [w.code for w in topology.warnings()]


def _by_code(topology, code):
    return [w for w in topology.warnings() if w.code == code]


@dataclass(frozen=True)
class Order:
    order_id: str


@dataclass(frozen=True)
class Report:
    title: str


@dataclass(frozen=True)
class Decision:
    value: str


@dataclass(frozen=True)
class Ghost:
    ghost_id: str


@dataclass(frozen=True)
class Archive:
    name: str


def _register(layers_tools):
    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "act")):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in layers_tools:
        tools.register(node)
    return TopologyBuilder(layers, tools).build()


def _async(handler):
    return handler


# ---- P6: unsatisfiable inputs ------------------------------------------------


def test_same_layer_only_producer_flagged() -> None:
    @tool(layer="read", produces=[Order], capabilities={"order.read"})
    async def p1() -> Order:
        return Order(order_id="O-1")

    @tool(layer="analyze", produces=[Report], capabilities={"report.make"})
    async def report_gen() -> Report:
        return Report(title="T")

    @tool(layer="analyze", consumes=[Report], produces=[Order],
          capabilities={"decide.report"})
    async def same_layer_decide(report: Report) -> Order:
        return Order(order_id=report.title)

    topology = _register([p1, report_gen, same_layer_decide])
    warnings = _by_code(topology, "UNSATISFIABLE_INPUT")
    assert len(warnings) == 1
    assert warnings[0].source == "same_layer_decide"
    assert warnings[0].target == "Report"
    assert "same or a later layer" in warnings[0].message


def test_earlier_layer_producer_clean() -> None:
    @tool(layer="read", produces=[Order], capabilities={"order.read"})
    async def p1() -> Order:
        return Order(order_id="O-1")

    @tool(layer="analyze", consumes=[Order], produces=[Decision],
          capabilities={"decide.order"})
    async def good(order: Order) -> Decision:
        return Decision(value=order.order_id)

    topology = _register([p1, good])
    assert _by_code(topology, "UNSATISFIABLE_INPUT") == []


def test_missing_producer_flagged() -> None:
    @tool(layer="analyze", consumes=[Ghost], produces=[Decision],
          capabilities={"decide.ghost"})
    async def ghost_decide(ghost: Ghost) -> Decision:
        return Decision(value=ghost.ghost_id)

    topology = _register([ghost_decide])
    warnings = _by_code(topology, "UNSATISFIABLE_INPUT")
    assert len(warnings) == 1
    assert "no tool in the topology produces this type" in warnings[0].message


def test_later_layer_only_producer_flagged() -> None:
    @tool(layer="analyze", consumes=[Archive], produces=[Decision],
          capabilities={"decide.archive"})
    async def forward(archive: Archive) -> Decision:
        return Decision(value=archive.name)

    @tool(layer="act", produces=[Archive], capabilities={"act.archive"})
    async def archiver() -> Archive:
        return Archive(name="A-1")

    topology = _register([forward, archiver])
    warnings = _by_code(topology, "UNSATISFIABLE_INPUT")
    assert len(warnings) == 1
    assert warnings[0].source == "forward"
    assert warnings[0].target == "Archive"


# ---- P7: slot-name conflicts --------------------------------------------------


def test_slot_name_conflict_flagged() -> None:
    @tool(layer="read", produces=[Order], capabilities={"order.read"})
    async def p1() -> Order:
        return Order(order_id="O-1")

    @tool(layer="analyze", produces=[Decision], capabilities={"decision.make"})
    async def decision_maker() -> Decision:
        return Decision(value="D")

    # parameter named "decision" (Decision's slot) while annotated Order:
    # name-first resolution binds the Decision artifact, never the Order.
    @tool(layer="analyze", consumes=[Order], produces=[Order],
          capabilities={"decide.confused"})
    async def confused(decision: Order) -> Order:
        return decision

    topology = _register([p1, decision_maker, confused])
    warnings = _by_code(topology, "SLOT_NAME_CONFLICT")
    assert len(warnings) == 1
    assert warnings[0].source == "confused"
    assert warnings[0].target == "decision"
    assert "rename the parameter to 'order'" in warnings[0].message


def test_convention_compliant_parameters_clean() -> None:
    @tool(layer="read", produces=[Order], capabilities={"order.read"})
    async def p1() -> Order:
        return Order(order_id="O-1")

    @tool(layer="analyze", consumes=[Order], produces=[Decision],
          capabilities={"decide.order"})
    async def good(order: Order) -> Decision:
        return Decision(value=order.order_id)

    topology = _register([p1, good])
    assert _by_code(topology, "SLOT_NAME_CONFLICT") == []


# ---- regression guard: existing domains trigger zero new diagnostics ----------


def test_office_triggers_zero_new_diagnostics() -> None:
    topology, _ = office.build_topology()
    for code in NEW_CODES:
        assert _by_code(topology, code) == [], code


def test_refund_triggers_zero_new_diagnostics() -> None:
    topology, _ = refund.build_topology()
    for code in NEW_CODES:
        assert _by_code(topology, code) == [], code
