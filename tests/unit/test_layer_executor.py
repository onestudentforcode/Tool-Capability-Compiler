import asyncio
from dataclasses import dataclass

import pytest

from capability_runtime import (
    ArtifactValue,
    ExecutionContext,
    ExecutionEnvironment,
    ExecutionState,
    LayerExecutionError,
    LayerExecutor,
    ToolExecutionStatus,
    tool,
)


@dataclass(frozen=True)
class Doc:
    text: str


@dataclass(frozen=True)
class Note:
    text: str


def make_layer_executor(max_concurrency: int = 2) -> LayerExecutor:
    return LayerExecutor(
        context=ExecutionContext(
            environment=ExecutionEnvironment.MOCK, max_concurrency=max_concurrency
        )
    )


def test_concurrent_tools_all_succeed_and_propagate() -> None:
    @tool(layer="read", produces=[Doc], name="db")
    async def fetch_db() -> Doc:
        return Doc("db-row")

    @tool(layer="read", produces=[Note], name="rag")
    async def fetch_rag() -> Note:
        return Note("rag-note")

    state = ExecutionState(query="q")
    executions = asyncio.run(
        make_layer_executor().run((fetch_rag, fetch_db), state)
    )

    # gather preserves input order
    assert [e.tool_name for e in executions] == ["rag", "db"]
    assert all(e.status is ToolExecutionStatus.SUCCESS for e in executions)
    assert state.get_artifacts("doc")[0].value == Doc("db-row")
    assert state.get_artifacts("note")[0].value == Note("rag-note")


def test_partial_failure_keeps_successes() -> None:
    @tool(layer="read", produces=[Doc], name="good")
    async def successful() -> Doc:
        return Doc("ok")

    @tool(layer="read", produces=[Doc], name="bad")
    async def failing() -> Doc:
        raise RuntimeError("nope")

    state = ExecutionState(query="q")
    executions = asyncio.run(
        make_layer_executor().run((successful, failing), state)
    )

    assert [e.tool_name for e in executions] == ["good", "bad"]
    assert executions[0].status is ToolExecutionStatus.SUCCESS
    assert executions[1].status is ToolExecutionStatus.ERROR
    # successful sibling still wrote its output
    assert len(state.get_artifacts("doc")) == 1


def test_entire_layer_failure_raises() -> None:
    @tool(layer="read", produces=[Doc], name="a")
    async def fail_a() -> Doc:
        raise RuntimeError("a")

    @tool(layer="read", produces=[Doc], name="b")
    async def fail_b() -> Doc:
        raise RuntimeError("b")

    state = ExecutionState(query="q")
    with pytest.raises(LayerExecutionError):
        asyncio.run(make_layer_executor().run((fail_a, fail_b), state))
    assert state.names() == ()


def test_same_slot_from_concurrent_siblings_is_preserved() -> None:
    @tool(layer="read", produces=[Doc], name="db")
    async def from_db() -> Doc:
        return Doc("db")

    @tool(layer="read", produces=[Doc], name="erp")
    async def from_erp() -> Doc:
        return Doc("erp")

    state = ExecutionState(query="q")
    executions = asyncio.run(
        make_layer_executor().run((from_db, from_erp), state)
    )
    assert len(executions) == 2
    docs = state.get_artifacts("doc")
    assert len(docs) == 2  # multi-source: both kept, not overwritten
    assert {a.source_tool for a in docs} == {"db", "erp"}


def test_max_concurrency_serial_bound() -> None:
    @tool(layer="read", produces=[Doc], name="x")
    async def one() -> Doc:
        return Doc("x")

    @tool(layer="read", produces=[Note], name="y")
    async def two() -> Note:
        return Note("y")

    # with max_concurrency=1 the two must still both succeed, just serialized
    state = ExecutionState(query="q")
    executions = asyncio.run(
        make_layer_executor(max_concurrency=1).run((one, two), state)
    )
    assert {e.status for e in executions} == {ToolExecutionStatus.SUCCESS}
    assert len(executions) == 2