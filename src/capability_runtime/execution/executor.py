from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..core.errors import ExecutionError, ToolExecutionError
from ..core.metrics import TokenUsage
from ..core.tool import ToolNode
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
    state under the snake-cased name of each declared produces type, so a later
    layer can consume them.
    """

    context: ExecutionContext

    async def execute(self, tool: ToolNode, state: ExecutionState) -> ToolExecution:
        args = self._resolve_arguments(tool, state)

        started_at = datetime.now()
        try:
            result = await tool.invoke(*args)
            status = ToolExecutionStatus.SUCCESS
            error: ExecutionError | None = None
        except Exception as exc:  # noqa: BLE001 - any tool failure -> ERROR, no retry
            status = ToolExecutionStatus.ERROR
            error = ToolExecutionError(f"tool '{tool.spec.name}' failed: {exc}")
            error.__cause__ = exc
            result = None
        ended_at = datetime.now()

        if status is ToolExecutionStatus.SUCCESS:
            self._propagate_outputs(tool, result, state)

        return ToolExecution(
            tool_name=tool.spec.name,
            layer=tool.spec.layer,
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            input_summary=args,
            output_summary=result,
            latency_ms=(ended_at - started_at).total_seconds() * 1000.0,
            error=error,
        )

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
                raise ExecutionError(
                    f"no input available for tool '{tool.spec.name}' argument '{name}'"
                )
            args.append(value)
        return args

    def _propagate_outputs(
        self, tool: ToolNode, result: Any, state: ExecutionState
    ) -> None:
        for produced in tool.spec.produces:
            state.add_artifact(
                _snake(produced.__name__ if isinstance(produced, type) else str(produced)),
                ArtifactValue(
                    value=result,
                    source_tool=tool.spec.name,
                    layer=tool.spec.layer,
                ),
            )