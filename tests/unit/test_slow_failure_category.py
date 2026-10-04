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
from _slow_helpers import (  # noqa: E402  (tests dir on sys.path via pytest rootdir)
    AlwaysPassEvaluator,
    build_topology,
    make_suite,
)


class BusinessFailEvaluator(Evaluator):
    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        return EvaluationResult(
            success=False,
            quality_score=0.0,
            reason="answer bad",
            category=TrialFailureCategory.ANSWER_ERROR,
        )


class ExplodingEvaluator(Evaluator):
    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        raise RuntimeError("evaluator boom")


def _router_for(layer, selected) -> object:
    class StubRouter:
        async def route(self, context):
            if context.current_layer == layer:
                return RoutingDecision(
                    action=RoutingAction.EXECUTE,
                    selected_tools=tuple(selected),
                )
            return RoutingDecision(action=RoutingAction.FINISH, selected_tools=())

    return StubRouter()


def _run(*, router, evaluator=None):
    topo = build_topology()
    suite = make_suite("s1")
    return asyncio.run(
        SlowRegressionRunner(
            topology=topo,
            evaluator=evaluator or AlwaysPassEvaluator(),
            trials_per_scenario=1,
            router=router,
            router_config_id="fake",
        ).run(suite)
    )


def test_missing_tool_when_picked_tool_does_not_exist() -> None:
    outcome = _run(router=_router_for("read", ["nonexistent_tool"]))
    result = outcome.results[0]
    assert result.execution_status is TrialExecutionStatus.ROUTING_ERROR
    assert result.failure_category is TrialFailureCategory.MISSING_TOOL


def test_tool_selection_error_when_tool_unreachable_in_layer() -> None:
    # policy_check exists in the topology but is not reachable in the read layer.
    outcome = _run(router=_router_for("read", ["policy_check"]))
    result = outcome.results[0]
    assert result.execution_status is TrialExecutionStatus.ROUTING_ERROR
    assert result.failure_category is TrialFailureCategory.TOOL_SELECTION_ERROR


def test_business_failure_carries_evaluator_category() -> None:
    outcome = _run(router=_router_for("read", ["db"]), evaluator=BusinessFailEvaluator())
    result = outcome.results[0]
    assert result.execution_status is TrialExecutionStatus.COMPLETED
    assert result.failure_category is TrialFailureCategory.ANSWER_ERROR


def test_business_success_has_no_failure_category() -> None:
    outcome = _run(router=_router_for("read", ["db"]))
    result = outcome.results[0]
    assert result.execution_status is TrialExecutionStatus.COMPLETED
    assert result.failure_category is None


def test_evaluator_exception_is_evaluation_error() -> None:
    outcome = _run(router=_router_for("read", ["db"]), evaluator=ExplodingEvaluator())
    result = outcome.results[0]
    assert result.execution_status is TrialExecutionStatus.EVALUATION_ERROR
    assert result.failure_category is TrialFailureCategory.EVALUATION_ERROR