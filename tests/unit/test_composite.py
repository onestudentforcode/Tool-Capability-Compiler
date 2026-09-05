from __future__ import annotations

import asyncio
import json

import pytest

from capability_runtime import (
    CompositeEvaluator,
    EvaluationError,
    EvaluationResult,
    FinalResult,
    LLMJudgeEvaluator,
    Scenario,
    StructuredEvaluator,
)


def run(coro):
    return asyncio.run(coro)


def scenario() -> Scenario:
    return Scenario(id="s1", query="refund order 001")


def result(snapshot, response="ok") -> FinalResult:
    return FinalResult(response=response, state_snapshot=snapshot)


def make_judge(quality: float, success: bool, reason: str = "judge said") -> LLMJudgeEvaluator:
    content = json.dumps({"success": success, "quality_score": quality, "reason": reason})
    return LLMJudgeEvaluator(_http=lambda payload: content)


def test_composite_weights_quality_scores() -> None:
    business = StructuredEvaluator(expected={"refund_allowed": True})
    quality = make_judge(0.5, True)
    evaluator = CompositeEvaluator(
        {"business": (business, 0.7), "completeness": (quality, 0.3)}
    )
    out = run(
        evaluator.evaluate(
            scenario(),
            result({"refund_allowed": True}, response="you can refund"),
            trace=None,
        )
    )
    assert out.success is True
    assert out.quality_score == pytest.approx(0.7 * 1.0 + 0.3 * 0.5)
    names = {criterion.name for criterion in out.criteria}
    assert names == {"business", "completeness"}
    assert out.criteria[0].passed is True


def test_composite_fails_when_any_component_fails() -> None:
    business = StructuredEvaluator(expected={"refund_allowed": True})
    quality = make_judge(0.8, True)
    evaluator = CompositeEvaluator({"business": (business, 0.9), "quality": (quality, 0.1)})
    out = run(
        evaluator.evaluate(scenario(), result({"refund_allowed": False}), trace=None)
    )
    assert out.success is False
    assert out.quality_score == pytest.approx(0.9 * 0.0 + 0.1 * 0.8)


def test_composite_normalizes_weights() -> None:
    business = StructuredEvaluator(expected={"a": 1})
    judge = make_judge(0.4, True)
    # weights 2 and 3 sum to 5 -> normalized 0.4 / 0.6
    evaluator = CompositeEvaluator({"b": (business, 2), "j": (judge, 3)})
    out = run(evaluator.evaluate(scenario(), result({"a": 1}), trace=None))
    assert out.quality_score == pytest.approx(0.4 * 1.0 + 0.6 * 0.4)


def test_composite_rejects_empty_components() -> None:
    with pytest.raises(EvaluationError):
        CompositeEvaluator({})


def test_composite_rejects_negative_weight() -> None:
    with pytest.raises(EvaluationError):
        CompositeEvaluator({"a": (lambda *_: None, -1.0)})


def test_composite_rejects_zero_weight_sum() -> None:
    with pytest.raises(EvaluationError):
        CompositeEvaluator({"a": (StructuredEvaluator(expected={"x": 1}), 0.0)})


def test_composite_propagates_evaluation_error() -> None:
    # fake judge returns malformed JSON -> LLMJudgeEvaluator raises EvaluationError,
    # which must surface (evaluation failed) rather than being read as business failure.
    bad_judge = LLMJudgeEvaluator(_http=lambda payload: "not json")
    evaluator = CompositeEvaluator({"quality": (bad_judge, 1.0)})
    with pytest.raises(EvaluationError):
        run(evaluator.evaluate(scenario(), result({"a": 1}), trace=None))