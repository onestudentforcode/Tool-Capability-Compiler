from __future__ import annotations

import asyncio
import json

import pytest

from capability_runtime import (
    EvaluationError,
    FinalResult,
    LLMJudgeEvaluator,
    Scenario,
)
from capability_runtime.evaluation.llm_judge import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
)


def run(coro):
    return asyncio.run(coro)


def make_judge(content: str, **kwargs):
    fake_http = lambda payload: content  # mirrors _chat: returns inner JSON
    return LLMJudgeEvaluator(_http=fake_http, **kwargs)


def scenario(query: str = "can order 001 be refunded") -> Scenario:
    return Scenario(id="s1", query=query)


def result(response: str, snapshot=None) -> FinalResult:
    return FinalResult(response=response, state_snapshot=snapshot)


def verdict(success: bool, quality: float | None = None, reason: str | None = None) -> str:
    obj: dict = {"success": success, "reason": reason}
    if quality is not None:
        obj["quality_score"] = quality
    return json.dumps(obj)


def test_judge_reads_success_and_quality() -> None:
    judge = make_judge(verdict(True, 0.9, "complete answer"))
    out = run(judge.evaluate(scenario(), result("yes it is refundable"), trace=None))
    assert out.success is True
    assert out.quality_score == 0.9
    assert out.reason == "complete answer"
    assert out.criteria[0].name == "llm_judge"
    assert out.criteria[0].passed is True


def test_judge_marks_business_failure() -> None:
    judge = make_judge(verdict(False, 0.2, "missing refund policy"))
    out = run(judge.evaluate(scenario(), result("ok"), trace=None))
    assert out.success is False
    assert out.quality_score == 0.2


def test_judge_fills_quality_when_omitted_by_success() -> None:
    judge = make_judge(verdict(False))
    out = run(judge.evaluate(scenario(), result("no"), trace=None))
    assert out.quality_score is None


def test_judge_payload_embeds_goal_and_response() -> None:
    seen: dict = {}

    def fake_http(payload):
        seen["payload"] = payload
        return verdict(True)

    judge = LLMJudgeEvaluator(_http=fake_http)
    run(judge.evaluate(scenario("refund order"), result("refunded"), trace=None))
    assert seen["payload"]["model"] == DEFAULT_MODEL
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    user = seen["payload"]["messages"][1]["content"]
    assert "refund order" in user
    assert "refunded" in user
    assert judge.model == DEFAULT_MODEL
    assert judge.base_url == DEFAULT_BASE_URL


def test_judge_rejects_non_boolean_success() -> None:
    judge = make_judge(json.dumps({"success": "yes"}))
    with pytest.raises(EvaluationError):
        run(judge.evaluate(scenario(), result("ok"), trace=None))


def test_judge_rejects_quality_out_of_range() -> None:
    judge = make_judge(verdict(True, 1.5))
    with pytest.raises(EvaluationError):
        run(judge.evaluate(scenario(), result("ok"), trace=None))


def test_judge_rejects_malformed_json() -> None:
    judge = make_judge("nonsense")
    with pytest.raises(EvaluationError):
        run(judge.evaluate(scenario(), result("ok"), trace=None))


def test_judge_rejects_non_object_json() -> None:
    judge = make_judge(json.dumps([1, 2]))
    with pytest.raises(EvaluationError):
        run(judge.evaluate(scenario(), result("ok"), trace=None))