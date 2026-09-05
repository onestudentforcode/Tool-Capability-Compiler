from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.errors import ExecutionError


class ExecutionEnvironment(Enum):
    MOCK = "mock"
    SANDBOX = "sandbox"
    STAGING = "staging"


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Ambient settings for a Slow Regression run."""

    environment: ExecutionEnvironment
    max_concurrency: int = 1
    per_tool_timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.environment, ExecutionEnvironment):
            raise ExecutionError("environment must be an ExecutionEnvironment")
        if isinstance(self.max_concurrency, bool) or self.max_concurrency < 1:
            raise ExecutionError("max_concurrency must be a positive integer")
        if self.per_tool_timeout_seconds is not None and (
            isinstance(self.per_tool_timeout_seconds, bool)
            or self.per_tool_timeout_seconds <= 0
        ):
            raise ExecutionError("per_tool_timeout_seconds must be positive or None")