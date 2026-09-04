import asyncio
import pytest

from capability_runtime import (
    ArtifactValue,
    EvaluationError,
    ExecutionState,
    FinalResult,
    ObservedRoute,
    StructuredEvaluator,
    TokenUsage,
    Trial,
    TrialExecutionStatus,
    TrialResult,
)
from capability_runtime.evaluation import CriterionResult, EvaluationResult


def test_structured_evaluator_passes_all_matching_facts() -> None:
    evaluator = StructuredEvaluator(expected={"refund_allowed": True})
    result = asyncio.run(
        evaluator.evaluate(
            scenario=None,
            result=FinalResult(response="ok", state_snapshot={"refund_allowed": True}),
            trace=None,
        )
    )
    assert result.success is True
    assert result.criteria == (
        CriterionResult(name="refund_allowed", passed=True, detail=True),
    )
    assert result.quality_score == 1.0
    assert result.reason is None


def test_structured_evaluator_fails_when_a_fact_mismatches() -> None:
    evaluator = StructuredEvaluator(expected={"refund_allowed": True})
    result = asyncio.run(
        evaluator.evaluate(
            scenario=None,
            result=FinalResult(
                response="ok", state_snapshot={"refund_allowed": False}
            ),
            trace=None,
        )
    )
    assert result.success is False
    assert result.criteria[0].passed is False
    assert result.quality_score == 0.0
    assert result.reason is not None


def test_structured_evaluator_accepts_execution_state_snapshot() -> None:
    evaluator = StructuredEvaluator(expected={"amount": 100})
    state = ExecutionState(query="q")
    state.add_artifact(
        "amount", ArtifactValue(value=100, source_tool="db", layer="read")
    )
    result = asyncio.run(
        evaluator.evaluate(
            scenario=None,
            result=FinalResult(response="ok", state_snapshot=state),
            trace=None,
        )
    )
    assert result.success is True


def test_structured_evaluator_rejects_unknown_snapshot() -> None:
    evaluator = StructuredEvaluator(expected={"a": 1})
    with pytest.raises(EvaluationError):
        asyncio.run(
            evaluator.evaluate(
                scenario=None,
                result=FinalResult(response="ok", state_snapshot=42),
                trace=None,
            )
        )


def test_quality_score_out_of_range_rejected() -> None:
    with pytest.raises(ValueError):
        EvaluationResult(success=True, quality_score=1.5)


def test_trial_result_bundles_structures_and_outcome() -> None:
    trial = Trial(
        id="t1",
        scenario_id="s1",
        trial_index=0,
        topology_version="1.0",
        scenario_suite_version="2.0",
        router_config_id="cfg-fake",
    )
    route = ObservedRoute.from_layers((("read", ("db",)), ("act", ("refund",))))
    evaluation = EvaluationResult(success=True)
    result = TrialResult(
        trial=trial,
        execution_status=TrialExecutionStatus.COMPLETED,
        route=route,
        trace=None,
        evaluation=evaluation,
        latency_ms=12.5,
        token_usage=TokenUsage(),
        cost=None,
    )
    assert result.trial is trial
    assert result.execution_status is TrialExecutionStatus.COMPLETED
    assert result.route is route
    assert result.evaluation is evaluation
    assert result.latency_ms == 12.5


def test_trial_result_rejects_negative_latency() -> None:
    trial = Trial(
        id="t1", scenario_id="s1", trial_index=0, topology_version="1.0",
        scenario_suite_version="2.0", router_config_id="cfg-fake",
    )
    with pytest.raises(EvaluationError if False else Exception):
        TrialResult(
            trial=trial,
            execution_status=TrialExecutionStatus.COMPLETED,
            route=None,
            trace=None,
            evaluation=None,
            latency_ms=-1.0,
            token_usage=TokenUsage(),
            cost=None,
        )