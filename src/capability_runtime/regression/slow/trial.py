from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ...core.errors import ExecutionError
from ...core.metrics import TokenUsage
from ...evaluation.base import EvaluationResult
from .route import ObservedRoute
from .trace import ExecutionTrace


class TrialExecutionStatus(Enum):
    """Outcome of executing a single Trial.

    COMPLETED only means execution finished and was routed to the end; it does
    NOT imply business success (that is decided by the Evaluator)."""

    COMPLETED = "completed"
    ROUTING_ERROR = "routing_error"
    TOOL_ERROR = "tool_error"
    LAYER_ERROR = "layer_error"
    EVALUATION_ERROR = "evaluation_error"
    FIXTURE_ERROR = "fixture_error"


@dataclass(frozen=True, slots=True)
class Trial:
    """An independent, traceable, evaluable execution unit for one scenario."""

    id: str
    scenario_id: str
    trial_index: int
    topology_version: str
    scenario_suite_version: str
    router_config_id: str

    def __post_init__(self) -> None:
        for field_name in ("id", "scenario_id", "topology_version", "scenario_suite_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ExecutionError(f"Trial {field_name} must be a non-empty string")
        if not isinstance(self.router_config_id, str) or not self.router_config_id.strip():
            raise ExecutionError("Trial router_config_id must be a non-empty string")
        if isinstance(self.trial_index, bool) or self.trial_index < 0:
            raise ExecutionError("Trial trial_index must be a non-negative integer")


@dataclass(slots=True)
class TrialResult:
    """Everything produced by one Trial: structure, trace, and outcome ($75)."""

    trial: Trial
    execution_status: TrialExecutionStatus
    route: ObservedRoute | None
    trace: ExecutionTrace
    evaluation: EvaluationResult | None
    latency_ms: float
    token_usage: TokenUsage
    cost: float | None

    def __post_init__(self) -> None:
        if isinstance(self.latency_ms, bool) or self.latency_ms < 0:
            raise ExecutionError("TrialResult latency_ms must be non-negative")
        if not isinstance(self.token_usage, TokenUsage):
            raise ExecutionError("TrialResult token_usage must be a TokenUsage")