import asyncio
from dataclasses import dataclass

import pytest

from capability_runtime import (
    ArtifactValue,
    ExecutionContext,
    ExecutionEnvironment,
    ExecutionError,
    ExecutionState,
    ToolExecutionError,
    ToolExecutionStatus,
    ToolExecutor,
    tool,
)


@dataclass(frozen=True)
class Order:
    id: str


@dataclass(frozen=True)
class RefundDecision:
    approved: bool


def make_executor() -> ToolExecutor:
    return ToolExecutor(
        context=ExecutionContext(environment=ExecutionEnvironment.MOCK)
    )


def test_no_arg_tool_writes_produces_into_state() -> None:
    @tool(layer="read", produces=[Order])
    async def db() -> Order:
        return Order("001")

    state = ExecutionState(query="remediate it")
    execution = asyncio.run(make_executor().execute(db, state))

    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.tool_name == "db"
    assert execution.layer == "read"
    assert execution.latency_ms >= 0.0
    assert execution.error is None
    artifacts = state.get_artifacts("order")
    assert len(artifacts) == 1
    assert artifacts[0].value == Order("001")
    assert artifacts[0].source_tool == "db"
    assert artifacts[0].layer == "read"


def test_consumes_input_resolved_by_param_name() -> None:
    @tool(layer="read", produces=[Order])
    async def db() -> Order:
        return Order("001")

    @tool(layer="analyze", consumes=[Order], produces=[RefundDecision])
    async def policy_check(order: Order) -> RefundDecision:
        assert order == Order("001")
        return RefundDecision(True)

    state = ExecutionState(query="q")
    state.add_artifact(
        "order", ArtifactValue(value=Order("001"), source_tool="db", layer="read")
    )
    execution = asyncio.run(make_executor().execute(policy_check, state))
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.input_summary == [Order("001")]
    decision = state.get_artifacts("refund_decision")
    assert len(decision) == 1
    assert decision[0].value == RefundDecision(True)
    assert decision[0].source_tool == "policy_check"


def test_failing_tool_records_error_and_writes_no_produces() -> None:
    @tool(layer="act", produces=[Order])
    async def boom() -> Order:
        raise RuntimeError("boom down")

    state = ExecutionState(query="q")
    execution = asyncio.run(make_executor().execute(boom, state))

    assert execution.status is ToolExecutionStatus.ERROR
    assert isinstance(execution.error, ToolExecutionError)
    assert "boom down" in str(execution.error)
    assert state.get_artifacts("order") == ()


def test_missing_required_input_raises_and_writes_nothing() -> None:
    @tool(layer="analyze", consumes=[Order])
    async def classifier(order: Order) -> None:
        return None

    state = ExecutionState(query="q")  # no Order present
    with pytest.raises(ExecutionError):
        asyncio.run(make_executor().execute(classifier, state))
    assert state.names() == ()


def test_input_resolution_falls_back_to_type_when_name_mismatches() -> None:
    @tool(layer="analyze", consumes=[Order], produces=[RefundDecision])
    async def inspect(order: Order) -> RefundDecision:
        return RefundDecision(True)

    # order sits under a different slot name -> type fallback must still resolve
    state = ExecutionState(query="q")
    state.add_artifact(
        "source_order",
        ArtifactValue(value=Order("007"), source_tool="source", layer="read"),
    )
    execution = asyncio.run(make_executor().execute(inspect, state))
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.input_summary == [Order("007")]


def test_snake_case_produces_slot_naming() -> None:
    @tool(layer="analyze", produces=[RefundDecision])
    async def judge() -> RefundDecision:
        return RefundDecision(True)

    state = ExecutionState(query="q")
    asyncio.run(make_executor().execute(judge, state))
    assert len(state.get_artifacts("refund_decision")) == 1


def test_execution_records_metadata() -> None:
    @tool(layer="read", produces=[Order])
    async def db() -> Order:
        return Order("1")

    state = ExecutionState(query="q")
    execution = asyncio.run(make_executor().execute(db, state))
    assert execution.ended_at >= execution.started_at
    assert execution.output_summary == Order("1")
    assert execution.token_usage is None and execution.cost is None