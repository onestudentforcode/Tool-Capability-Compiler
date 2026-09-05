from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..scenario.models import Scenario


@dataclass(frozen=True, slots=True)
class FinalResult:
    """What a Trial produced once the router returned FINISH ($64)."""

    response: Any
    state_snapshot: Any


@dataclass(frozen=True, slots=True)
class CriterionResult:
    name: str
    passed: bool
    detail: Any = None


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Business outcome of a Trial, distinct from its execution_status ($67)."""

    success: bool
    criteria: tuple[CriterionResult, ...] = ()
    quality_score: float | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.quality_score is not None and not (
            0.0 <= self.quality_score <= 1.0
        ):
            raise ValueError("quality_score must be within [0, 1]")


class Evaluator(Protocol):
    """Judges business success from the final result and the trace ($65)."""

    async def evaluate(
        self,
        scenario: Scenario,
        result: FinalResult,
        trace: "ExecutionTrace",
    ) -> EvaluationResult:
        ...