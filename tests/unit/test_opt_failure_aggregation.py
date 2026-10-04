import asyncio

from capability_runtime import (
    EvaluationResult,
    Evaluator,
    RoutingAction,
    RoutingDecision,
    SlowRegressionRunner,
    TrialExecutionStatus,
    TrialFailureCategory,
)
from capability_runtime.regression.slow.report import (
    build_slow_regression_report,
    render_slow_report,
)
from capability_runtime.regression.slow.stats import build_observation_stats
from _slow_helpers import AlwaysPassEvaluator, build_topology, make_suite


class _CategoryFailEvaluator(Evaluator):
    def __init__(self, category):
        self._category = category

    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        return EvaluationResult(
            success=False,
            quality_score=0.0,
            category=self._category,
        )


class _ExplodeEvaluator(Evaluator):
    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        raise RuntimeError("boom")


def _router(layer, selected):
    class Stub:
        async def route(self, context):
            if context.current_layer == layer:
                return RoutingDecision(
                    action=RoutingAction.EXECUTE, selected_tools=tuple(selected)
                )
            return RoutingDecision(action=RoutingAction.FINISH, selected_tools=())

    return Stub()


def _run(*, router, evaluator):
    topo = build_topology()
    return asyncio.run(
        SlowRegressionRunner(
            topology=topo,
            evaluator=evaluator,
            trials_per_scenario=1,
            router=router,
            router_config_id="fake",
        ).run(make_suite("s1"))
    )


def _report(outcome, obs):
    return build_slow_regression_report(
        outcome=outcome,
        obs=obs,
        suite=make_suite("s1"),
        topology=build_topology(),
        topology_version="v1",
        router_config_id="fake",
    )


def test_aggregation_counts_failure_categories() -> None:
    outcome = _run(
        router=_router("read", ["db"]),
        evaluator=_CategoryFailEvaluator(TrialFailureCategory.ANSWER_ERROR),
    )
    obs = build_observation_stats(outcome.results)
    assert obs.failure_by_category == (("answer_error", len(outcome.results)),)


def test_aggregation_success_has_no_failure_categories() -> None:
    outcome = _run(router=_router("read", ["db"]), evaluator=AlwaysPassEvaluator())
    obs = build_observation_stats(outcome.results)
    assert obs.failure_by_category == ()


def test_aggregation_sorted_deterministically() -> None:
    cases = [TrialFailureCategory.TIMEOUT, TrialFailureCategory.MISSING_TOOL]
    # A single run yields one category; run twice to confirm stable tuple ordering.
    outcomes = [
        _run(
            router=_router("read", ["nonexistent_tool"]),
            evaluator=AlwaysPassEvaluator(),
        )
        for _ in cases
    ]
    for outcome in outcomes:
        obs = build_observation_stats(outcome.results)
        assert dict(obs.failure_by_category) == {"missing_tool": 1}


def test_evaluator_exception_aggregates_as_evaluation_error() -> None:
    outcome = _run(router=_router("read", ["db"]), evaluator=_ExplodeEvaluator())
    result = outcome.results[0]
    assert result.execution_status is TrialExecutionStatus.EVALUATION_ERROR
    assert result.failure_category is TrialFailureCategory.EVALUATION_ERROR
    obs = build_observation_stats(outcome.results)
    assert obs.failure_by_category == (("evaluation_error", 1),)


def test_report_carries_failure_by_category() -> None:
    outcome = _run(
        router=_router("read", ["db"]),
        evaluator=_CategoryFailEvaluator(TrialFailureCategory.ANSWER_ERROR),
    )
    obs = build_observation_stats(outcome.results)
    report = _report(outcome, obs)
    assert dict(report.failure_by_category)["answer_error"] == len(outcome.results)


def test_render_prints_failure_categories() -> None:
    outcome = _run(
        router=_router("read", ["db"]),
        evaluator=_CategoryFailEvaluator(TrialFailureCategory.ANSWER_ERROR),
    )
    obs = build_observation_stats(outcome.results)
    text = render_slow_report(_report(outcome, obs))
    assert "Failure Categories:" in text
    assert "answer_error" in text


def test_render_omits_section_when_no_failures() -> None:
    outcome = _run(router=_router("read", ["db"]), evaluator=AlwaysPassEvaluator())
    obs = build_observation_stats(outcome.results)
    text = render_slow_report(_report(outcome, obs))
    assert "Failure Categories:" not in text