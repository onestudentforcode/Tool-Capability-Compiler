"""Combine several evaluators into a single weighted verdict.

Step 13 :class:`CompositeEvaluator` lets a trial mix business-state checks with
answer-quality checks (phase3 §73), e.g. ``refund == success`` at 70% plus
``answer completeness`` at 30%, one per component. It emits a weighted
``quality_score`` but never ranks routes -- ranking is Phase 5 (§2267).

Weights are validated and normalized to sum to 1. All component evaluators must
succeed for the composite to be a business success; each component's quality
score (defaulting to 1.0 on success, 0.0 on failure) is aggregated by weight.
If any component evaluator raises :class:`EvaluationError`, it propagates so the
caller can tell an evaluation failure from a business failure (phase3 §74).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.errors import EvaluationError
from ..scenario.models import Scenario
from .base import CriterionResult, EvaluationResult, Evaluator, FinalResult


class CompositeEvaluator:
    """Weighted combination of named sub-evaluators."""

    def __init__(
        self,
        components: Mapping[str, tuple[Evaluator, float]],
    ) -> None:
        if not components:
            raise EvaluationError("CompositeEvaluator needs at least one component")
        weights: list[tuple[str, Evaluator, float]] = []
        total = 0.0
        for name, (evaluator, weight) in components.items():
            if not isinstance(name, str) or not name.strip():
                raise EvaluationError("component name must be a non-empty string")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                raise EvaluationError(f"component {name!r} weight must be a number")
            if weight < 0:
                raise EvaluationError(
                    f"component {name!r} weight must be non-negative, got {weight}"
                )
            weights.append((name.strip(), evaluator, weight))
            total += weight
        if total <= 0.0:
            raise EvaluationError("component weights must sum to a positive value")
        self._components = [
            (name, evaluator, weight / total) for name, evaluator, weight in weights
        ]

    async def evaluate(
        self,
        scenario: Scenario,
        result: FinalResult,
        trace: "ExecutionTrace",
    ) -> EvaluationResult:
        criteria: list[CriterionResult] = []
        reasons: list[str] = []
        weighted = 0.0
        overall_success = True
        for name, evaluator, weight in self._components:
            sub = await evaluator.evaluate(scenario, result, trace)
            score = sub.quality_score
            if score is None:
                score = 1.0 if sub.success else 0.0
            weighted += weight * score
            if not sub.success:
                overall_success = False
            criteria.append(
                CriterionResult(name=name, passed=sub.success, detail=sub)
            )
            if sub.reason:
                reasons.append(f"{name}: {sub.reason}")
        return EvaluationResult(
            success=overall_success,
            criteria=tuple(criteria),
            quality_score=weighted,
            reason="; ".join(reasons) if reasons else None,
        )