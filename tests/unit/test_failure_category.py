import asyncio
from dataclasses import dataclass

import pytest

from capability_runtime import (
    ArtifactValue,
    ExecutionContext,
    ExecutionEnvironment,
    ExecutionState,
    SchemaMismatchError,
    ToolExecutionError,
    ToolExecutionStatus,
    ToolExecutor,
    TrialFailureCategory,
    tool,
)


@dataclass(frozen=True)
class Order:
    id: str


@dataclass(frozen=True)
class RefundDecision:
    approved: bool


def make_executor(*, per_tool_timeout_seconds=None) -> ToolExecutor:
    return ToolExecutor(
        ExecutionContext(
            environment=ExecutionEnvironment.MOCK,
            per_tool_timeout_seconds=per_tool_timeout_seconds,
        )
    )


def test_category_enum_values_are_stable_snake_case() -> None:
    expected = {
        "SUCCESS": "success",
        "TOOL_SELECTION_ERROR": "tool_selection_error",
        "MISSING_TOOL": "missing_tool",
        "WRONG_TOOL_DEPENDENCY": "wrong_tool_dependency",
        "SCHEMA_MISMATCH": "schema_mismatch",
        "TOOL_EXECUTION_ERROR": "tool_execution_error",
        "REASONING_ERROR": "reasoning_error",
        "ANSWER_ERROR": "answer_error",
        "PROVIDER_FAILURE": "provider_failure",
        "TIMEOUT": "timeout",
        "EVALUATION_ERROR": "evaluation_error",
        "FIXTURE_ERROR": "fixture_error",
    }
    for name, value in expected.items():
        member = getattr(TrialFailureCategory, name)
        assert member.value == value


def test_schema_mismatch_no_input() -> None:
    @tool(layer="analyze", consumes=[Order])
    async def classifier(order: Order) -> None:
        return None

    state = ExecutionState(query="q")
    execution = asyncio.run(make_executor().execute(classifier, state))
    assert execution.status is ToolExecutionStatus.ERROR
    assert isinstance(execution.error, SchemaMismatchError)
    assert execution.error_category is TrialFailureCategory.SCHEMA_MISMATCH


def test_tool_execution_error_category() -> None:
    @tool(layer="act")
    async def boom() -> None:
        raise RuntimeError("inner")

    execution = asyncio.run(make_executor().execute(boom, ExecutionState(query="q")))
    assert execution.status is ToolExecutionStatus.ERROR
    assert isinstance(execution.error, ToolExecutionError)
    assert execution.error_category is TrialFailureCategory.TOOL_EXECUTION_ERROR


def test_timeout_classifies_as_timeout_category() -> None:
    @tool(layer="act")
    async def slow() -> None:
        await asyncio.sleep(5)

    executor = make_executor(per_tool_timeout_seconds=0.05)
    execution = asyncio.run(executor.execute(slow, ExecutionState(query="q")))
    assert execution.status is ToolExecutionStatus.ERROR
    assert execution.error_category is TrialFailureCategory.TIMEOUT
    assert "timeout" in str(execution.error).lower()


def test_success_has_no_failure_category() -> None:
    @tool(layer="act", produces=[RefundDecision])
    async def decide() -> RefundDecision:
        return RefundDecision(True)

    state = ExecutionState(query="q")
    execution = asyncio.run(make_executor().execute(decide, state))
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.error_category is None
    assert state.latest("refund_decision") is not None


def test_no_timeout_does_not_classify() -> None:
    @tool(layer="act")
    async def quick() -> None:
        return None

    execution = asyncio.run(make_executor().execute(quick, ExecutionState(query="q")))
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.error_category is None


def test_categories_are_enum_of_str() -> None:
    assert issubclass(TrialFailureCategory, str)
    assert TrialFailureCategory.TIMEOUT.value == "timeout"


@pytest.mark.parametrize(
    "member,value",
    [
        (TrialFailureCategory.TIMEOUT, "timeout"),
        (TrialFailureCategory.ANSWER_ERROR, "answer_error"),
        (TrialFailureCategory.PROVIDER_FAILURE, "provider_failure"),
        (TrialFailureCategory.MISSING_TOOL, "missing_tool"),
    ],
)
def test_category_members_round_trip(member, value) -> None:
    assert TrialFailureCategory(value) is member