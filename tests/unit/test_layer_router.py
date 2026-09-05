import asyncio

import pytest

from capability_runtime import (
    FakeRouter,
    InvalidRoutingDecisionError,
    InvalidToolSelectionError,
    RoutingAction,
    RouterConfig,
    RoutingContext,
    RoutingDecision,
    ToolSummary,
    tool,
    validate_decision,
)
from capability_runtime.registry import ToolRegistry


def summary(name: str, layer: str = "analyze") -> ToolSummary:
    return ToolSummary(name=name, layer=layer)


def context(
    available: tuple[ToolSummary, ...],
    layer: str = "analyze",
    query: str = "can order 001 be refunded",
) -> RoutingContext:
    return RoutingContext(
        query=query,
        current_layer=layer,
        available_tools=available,
        topology_version="1.0",
    )


def test_router_config_validates_fields() -> None:
    cfg = RouterConfig(model="qwen3", temperature=1.0, max_tools_per_layer=3)
    assert cfg.exploration_mode == "free"

    with pytest.raises(Exception):
        RouterConfig(model="  ", temperature=1.0, max_tools_per_layer=1)
    with pytest.raises(Exception):
        RouterConfig(model="m", temperature=3.0, max_tools_per_layer=1)
    with pytest.raises(Exception):
        RouterConfig(model="m", temperature=0.5, max_tools_per_layer=0)
    with pytest.raises(Exception):
        RouterConfig(model="m", temperature=0.5, max_tools_per_layer=1, exploration_mode="nope")


def test_tool_summary_from_tool_node() -> None:
    @tool(layer="read", description="reads the order")
    async def db():
        return None

    registry = ToolRegistry()
    registry.register(db)
    node = registry.get("db")
    ts = ToolSummary.from_tool_node(node)
    assert (ts.name, ts.layer, ts.description) == ("db", "read", "reads the order")


def test_fake_router_finishes_when_no_plan() -> None:
    router = FakeRouter()
    decision = asyncio.run(router.route(context((),)))
    assert decision.action is RoutingAction.FINISH
    assert decision.selected_tools == ()


def test_fake_router_default_bounds_selection() -> None:
    router = FakeRouter(default_max_tools=2)
    decision = asyncio.run(router.route(context((summary("b"), summary("a"), summary("c")))))
    assert decision.action is RoutingAction.EXECUTE
    assert decision.selected_tools == ("a", "b")  # sorted, bounded


def test_fake_router_respects_plan_and_rejects_unknown() -> None:
    router = FakeRouter(layer_selections={"analyze": ["c", "a"]})
    decision = asyncio.run(router.route(context((summary("a"), summary("b"), summary("c")))))
    assert decision.selected_tools == ("c", "a")

    router_bad = FakeRouter(layer_selections={"analyze": ["c", "ghost"]})
    with pytest.raises(InvalidToolSelectionError):
        asyncio.run(router_bad.route(context((summary("a"), summary("b"), summary("c")))))


def test_fake_router_is_deterministic() -> None:
    router = FakeRouter(layer_selections={"analyze": ["db"]})
    ctx = context((summary("db"), summary("rag")))
    assert asyncio.run(router.route(ctx)).selected_tools == ("db",)
    assert asyncio.run(router.route(ctx)).selected_tools == ("db",)


def test_fake_router_finish_on_empty_plan() -> None:
    router = FakeRouter(layer_selections={"analyze": []})
    decision = asyncio.run(router.route(context((summary("a"),))))
    assert decision.action is RoutingAction.FINISH


def test_validate_decision_accepts_available_tools() -> None:
    decision = RoutingDecision(
        action=RoutingAction.EXECUTE, selected_tools=("b", "a")
    )
    validate_decision(decision, available_tool_names=("a", "b", "c"), max_tools_per_layer=2)


def test_validate_decision_rejects_unknown_tool() -> None:
    decision = RoutingDecision(action=RoutingAction.EXECUTE, selected_tools=("ghost",))
    with pytest.raises(InvalidToolSelectionError):
        validate_decision(decision, ("a",), max_tools_per_layer=2)


def test_validate_decision_rejects_over_limit() -> None:
    decision = RoutingDecision(action=RoutingAction.EXECUTE, selected_tools=("a", "b", "c"))
    with pytest.raises(InvalidRoutingDecisionError):
        validate_decision(decision, ("a", "b", "c"), max_tools_per_layer=2)


def test_validate_decision_finish_without_tools() -> None:
    validate_decision(RoutingDecision(action=RoutingAction.FINISH), ("a",), 1)


def test_validate_decision_finish_with_tools_rejected() -> None:
    decision = RoutingDecision(action=RoutingAction.FINISH, selected_tools=("a",))
    with pytest.raises(InvalidRoutingDecisionError):
        validate_decision(decision, ("a",), 1)


def test_validate_decision_empty_execute_rejected() -> None:
    decision = RoutingDecision(action=RoutingAction.EXECUTE, selected_tools=())
    with pytest.raises(InvalidRoutingDecisionError):
        validate_decision(decision, ("a",), 1)


def test_routing_decision_rejects_duplicates() -> None:
    with pytest.raises(InvalidRoutingDecisionError):
        RoutingDecision(action=RoutingAction.EXECUTE, selected_tools=("a", "a"))