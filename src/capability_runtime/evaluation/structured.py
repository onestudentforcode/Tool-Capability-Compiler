from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.errors import EvaluationError
from ..execution.state import ExecutionState
from ..scenario.models import Scenario
from .base import CriterionResult, EvaluationResult, FinalResult


def _as_mapping(snapshot: Any) -> Mapping[str, Any]:
    if isinstance(snapshot, Mapping):
        return snapshot
    if isinstance(snapshot, ExecutionState):
        return {name: (snapshot.latest(name).value if snapshot.latest(name) else None) for name in snapshot.names()}
    raise EvaluationError(
        f"state_snapshot must be a mapping or an ExecutionState, got {type(snapshot).__name__}"
    )


class StructuredEvaluator:
    """Deterministic PASS/FAIL from an expected fact set ($69).

    The most reliable evaluator: compares expected business facts against the
    produced state snapshot, with no LLM in the loop.
    """

    def __init__(self, expected: Mapping[str, Any]) -> None:
        self._expected = dict(expected)

    async def evaluate(
        self,
        scenario: Scenario,
        result: FinalResult,
        trace: "ExecutionTrace",
    ) -> EvaluationResult:
        actual = _as_mapping(result.state_snapshot)
        criteria = tuple(
            CriterionResult(
                name=name,
                passed=actual.get(name) == expected,
                detail=actual.get(name),
            )
            for name, expected in sorted(self._expected.items())
        )
        success = all(criterion.passed for criterion in criteria)
        return EvaluationResult(
            success=success,
            criteria=criteria,
            quality_score=1.0 if success else 0.0,
            reason=None if success else "not all expected facts matched",
        )