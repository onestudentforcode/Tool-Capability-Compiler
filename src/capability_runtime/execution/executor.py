from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..core.errors import (
    ExecutionError,
    LayerExecutionError,
    SchemaMismatchError,
    TimeoutExecutionError,
    ToolExecutionError,
)
from ..core.failure import TrialFailureCategory
from ..core.metrics import TokenUsage
from ..core.tool import ToolNode
from ..resources.metering import (
    MeteringSource,
    mount_collector,
    take_detail,
    unmount_collector,
)
from .context import ExecutionContext
from .state import ArtifactValue, ExecutionState

_MISSING = object()


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class ToolExecutionStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"


@dataclass(slots=True)
class ToolExecution:
    """Record of a single real tool call."""

    tool_name: str
    layer: str
    started_at: datetime
    ended_at: datetime
    status: ToolExecutionStatus
    input_summary: Any
    output_summary: Any
    latency_ms: float
    token_usage: TokenUsage | None = None
    cost: float | None = None
    error: ExecutionError | None = None
    error_category: TrialFailureCategory | None = None
    # resource-handle metering (resource-metering §3); billing stays declared
    access_counts: dict[str, int] | None = None
    measured_cost: float | None = None
    metering_source: MeteringSource = MeteringSource.DECLARED
    # structured detail attached by the handler via the generic channel
    # (a composite runtime attaches one LayerExecution tuple per iteration)
    composite_detail: tuple | None = None


def _lookup_by_name(state: ExecutionState, name: str) -> Any:
    latest = state.latest(_snake(name))
    return latest.value if latest is not None else _MISSING


def _lookup_by_type(state: ExecutionState, expected: type) -> Any:
    for slot in state.names():
        for artifact in state.get_artifacts(slot):
            if isinstance(artifact.value, expected):
                return artifact.value
    return _MISSING


@dataclass(slots=True)
class ToolExecutor:
    """Runs a single tool against the state and propagates its outputs.

    Inputs are resolved deterministically: parameter name first (snake-cased
    slot), then the parameter's declared type. Outputs are written into the
    state under the snake-cased name of each declared produces type (or the
    handler's return annotation when the spec declares none, e.g. for
    JSON-bound tools), so a later layer can consume them.
    """

    context: ExecutionContext

    async def execute(self, tool: ToolNode, state: ExecutionState) -> ToolExecution:
        try:
            args = self._resolve_arguments(tool, state)
        except SchemaMismatchError as exc:
            started_at = datetime.now()
            ended_at = datetime.now()
            return ToolExecution(
                tool_name=tool.spec.name,
                layer=tool.spec.layer,
                started_at=started_at,
                ended_at=ended_at,
                status=ToolExecutionStatus.ERROR,
                input_summary=_MISSING,
                output_summary=None,
                latency_ms=0.0,
                error=exc,
                error_category=TrialFailureCategory.SCHEMA_MISMATCH,
            )

        started_at = datetime.now()
        collector_token = mount_collector()
        try:
            try:
                result = await self._invoke_with_timeout(tool, args)
                status = ToolExecutionStatus.SUCCESS
                error: ExecutionError | None = None
                error_category: TrialFailureCategory | None = None
            except TimeoutExecutionError as exc:
                status = ToolExecutionStatus.ERROR
                error = exc
                error_category = TrialFailureCategory.TIMEOUT
                result = None
            except Exception as exc:  # noqa: BLE001 - any tool failure -> ERROR, no retry
                status = ToolExecutionStatus.ERROR
                error = ToolExecutionError(f"tool '{tool.spec.name}' failed: {exc}")
                error.__cause__ = exc
                error_category = TrialFailureCategory.TOOL_EXECUTION_ERROR
                result = None
        finally:
            collector = unmount_collector(collector_token)
        ended_at = datetime.now()

        if status is ToolExecutionStatus.SUCCESS:
            self._propagate_outputs(tool, result, state)

        # An invocation was attempted, so the declared cost was incurred —
        # including timeouts and errors. Argument-resolution failures above
        # never reached the tool and stay uncosted (battlefield-hardening A).
        # Handle traffic (access counts / measured tokens) settles here too,
        # with the attempt — a timed-out call still made its accesses.
        return ToolExecution(
            tool_name=tool.spec.name,
            layer=tool.spec.layer,
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            input_summary=args,
            output_summary=result,
            latency_ms=(ended_at - started_at).total_seconds() * 1000.0,
            cost=tool.spec.cost_per_call,
            error=error,
            error_category=error_category,
            access_counts=collector.access_counts or None,
            token_usage=collector.tokens,
            measured_cost=collector.measured_cost,
            metering_source=collector.source,
            composite_detail=take_detail(),
        )

    async def _invoke_with_timeout(self, tool: ToolNode, args: list[Any]) -> Any:
        timeout = self.context.per_tool_timeout_seconds
        if timeout is None:
            return await tool.invoke(*args)
        try:
            return await asyncio.wait_for(tool.invoke(*args), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutExecutionError(
                f"tool '{tool.spec.name}' exceeded timeout of {timeout}s"
            ) from exc

    def _resolve_arguments(self, tool: ToolNode, state: ExecutionState) -> list[Any]:
        params = inspect.signature(tool.handler).parameters
        args: list[Any] = []
        for name, param in params.items():
            if param.kind not in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                continue
            value = _lookup_by_name(state, name)
            if value is _MISSING and param.annotation is not inspect.Parameter.empty:
                value = _lookup_by_type(state, param.annotation)
            if value is _MISSING:
                raise SchemaMismatchError(
                    f"no input available for tool '{tool.spec.name}' argument '{name}'"
                )
            args.append(value)
        return args

    def _propagate_outputs(
        self, tool: ToolNode, result: Any, state: ExecutionState
    ) -> None:
        produced_types = tool.spec.produces
        if not produced_types:
            # JSON-bound tools carry no declared contracts; fall back to the
            # handler's real return annotation so cross-layer chaining still
            # works. Types fill the execution contract only — they never
            # build edges (same rule as @tool annotation inference).
            annotation = inspect.signature(tool.handler).return_annotation
            if isinstance(annotation, type):
                produced_types = (annotation,)
        for produced in produced_types:
            state.add_artifact(
                _snake(produced.__name__ if isinstance(produced, type) else str(produced)),
                ArtifactValue(
                    value=result,
                    source_tool=tool.spec.name,
                    layer=tool.spec.layer,
                ),
            )


@dataclass(slots=True)
class LayerExecutor:
    """Runs the selected tools of one layer concurrently.

    Same-layer tools never depend on each other, so they execute independently
    against the incoming state. Orders the returned executions consistently and
    preserves partial results: a failing sibling keeps the successful ones
    ($41). Only when every selected tool fails does the layer fail ($42).
    """

    context: ExecutionContext

    async def run(
        self, tools: Sequence[ToolNode], state: ExecutionState
    ) -> tuple[ToolExecution, ...]:
        executor = ToolExecutor(self.context)
        semaphore = asyncio.Semaphore(self.context.max_concurrency)

        async def constrained(node: ToolNode) -> ToolExecution:
            async with semaphore:
                return await executor.execute(node, state)

        executions = await asyncio.gather(*(constrained(node) for node in tools))
        results = tuple(executions)
        if results and all(
            execution.status is ToolExecutionStatus.ERROR for execution in results
        ):
            raise LayerExecutionError(
                f"all {len(results)} selected tools in layer failed",
                executions=results,
            )
        return results