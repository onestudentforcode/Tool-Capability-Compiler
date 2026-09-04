from dataclasses import dataclass

import pytest

from capability_runtime import (
    ArtifactValue,
    ExecutionState,
    LayerRegistry,
    TopologyBuilder,
    TopologyFilter,
    ToolRegistry,
    ToolNotFoundError,
    tool,
)


@dataclass(frozen=True)
class Order:
    id: str


@dataclass(frozen=True)
class RefundDecision:
    pass


@tool(layer="read", workers=["policy_check"], produces=[Order])
async def db():
    return Order("001")


@tool(layer="read", workers=["policy_check"], produces=[Order])
async def erp():
    return Order("001")


@tool(layer="read")
async def unrelated_read():
    return None


@tool(
    layer="analyze",
    providers=["db", "erp"],
    workers=["refund_act"],
    consumes=[Order],
    produces=[RefundDecision],
)
async def policy_check(order):
    return RefundDecision()


@tool(layer="analyze", providers=["unrelated_read"])
async def classifier():
    return None


@tool(layer="act", providers=["policy_check"], consumes=[RefundDecision])
async def refund_act(decision):
    return None


@tool(layer="act", providers=[])
async def send_email():
    return None


@pytest.fixture(scope="module")
def topology():
    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "act")):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in (
        db,
        erp,
        unrelated_read,
        policy_check,
        classifier,
        refund_act,
        send_email,
    ):
        tools.register(node)
    return TopologyFilter(TopologyBuilder(layers, tools).build())


def state_with_read_source(source_tool: str) -> ExecutionState:
    state = ExecutionState(query="can order 001 be refunded")
    state.add_artifact(
        "order", ArtifactValue(value=Order("001"), source_tool=source_tool, layer="read")
    )
    return state


def test_entry_layer_exposes_all_source_tools(topology) -> None:
    # empty previous selection -> all tools in the first layer are available
    assert topology.available_tools("read") == ("db", "erp", "unrelated_read")


def test_next_layer_uses_or_reachability(topology) -> None:
    # both db and erp open the policy_check edge; db alone is enough (OR)
    assert topology.available_tools("analyze", ("db",)) == ("policy_check",)
    assert topology.available_tools("analyze", ("erp",)) == ("policy_check",)
    # unrelated_read cannot reach policy_check, so classifier is the reachable one
    assert topology.available_tools("analyze", ("unrelated_read",)) == ("classifier",)


def test_respects_worker_allow_list(topology) -> None:
    # db has workers=[policy_check], so polices which edge worker admits target
    available = topology.available_tools("analyze", ("db",))
    assert "classifier" not in available  # db does not target classifier as a worker


def test_respects_provider_allow_list(topology) -> None:
    # refund_act only admits policy_check as provider; send_email admits nobody
    state = ExecutionState(query="q")
    state.add_artifact(
        "decision",
        ArtifactValue(
            value=RefundDecision(), source_tool="policy_check", layer="analyze"
        ),
    )
    available = topology.available_tools("act", ("policy_check",), state=state)
    assert available == ("refund_act",)
    assert "send_email" not in available  # providers=[] excludes everything


def test_respects_schema_availability(topology) -> None:
    # policy_check consumes Order: unsatisfied when state lacks an Order
    empty_state = ExecutionState(query="q")
    assert topology.available_tools("analyze", ("db",), state=empty_state) == ()
    # satisfied when state holds an Order
    assert topology.available_tools(
        "analyze", ("db",), state=state_with_read_source("db")
    ) == ("policy_check",)


def test_previous_selection_can_open_multiple_targets(topology) -> None:
    # both db and erp can feed policy_check; select them -> policy_check reachable
    assert topology.available_tools("analyze", ("db", "erp")) == ("policy_check",)


def test_output_is_sorted_and_deterministic(topology) -> None:
    assert topology.available_tools("read") == topology.available_tools("read")


def test_unknown_previous_source_raises(topology) -> None:
    with pytest.raises(ToolNotFoundError):
        topology.available_tools("analyze", ("ghost",))