"""Battlefield Hardening Batch C: data-dependent sandbox tools.

The refund demo tools read the sandbox store instead of returning constants,
so fixture variants drive genuinely different outcomes, failure paths and
metering (battlefield-hardening §4).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from capability_runtime import (
    EvaluationResult,
    FinalResult,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    ExecutionState,
    ArtifactValue,
    FakeRouter,
)

_PROJECT = Path(__file__).resolve().parents[2]
_DEMO = _PROJECT / "examples" / "slow_refund"
sys.path.insert(0, str(_DEMO))

import facts  # noqa: E402
import refund  # noqa: E402
import store  # noqa: E402


# ---- store variants ----------------------------------------------------------


def test_store_variants_shape() -> None:
    store.STORE.reset("eligible")
    assert store.STORE.order == facts.Order("ORD-100", 1200.0, True, "web")
    assert store.STORE.erp_available

    store.STORE.reset("ineligible")
    assert store.STORE.order.eligible is False
    assert store.STORE.order.amount <= 5000.0  # rejected only by eligibility

    store.STORE.reset("high_risk")
    assert store.STORE.order.amount > 5000.0  # rejected by the policy ceiling

    store.STORE.reset("not_found")
    assert store.STORE.order is None

    store.STORE.reset("erp_down")
    assert store.STORE.erp_available is False
    assert store.STORE.order is not None


def test_store_rejects_unknown_variant() -> None:
    with pytest.raises(ValueError, match="unknown sandbox variant"):
        store.STORE.reset("made_up")


def test_store_reset_clears_refunded_history() -> None:
    store.STORE.reset("eligible")
    store.STORE.record_refund("ORD-100")
    assert store.STORE.refunded_order_ids == frozenset({"ORD-100"})
    store.STORE.reset("eligible")
    assert store.STORE.refunded_order_ids == frozenset()


# ---- tool behavior per variant ------------------------------------------------


def test_order_db_fails_when_order_missing() -> None:
    store.STORE.reset("not_found")
    with pytest.raises(LookupError):
        asyncio.run(refund.order_db.invoke())


def test_erp_fails_when_backend_down() -> None:
    store.STORE.reset("erp_down")
    with pytest.raises(ConnectionError):
        asyncio.run(refund.erp.invoke())


def test_policy_branches_on_order_data() -> None:
    for variant, expected in (
        ("eligible", "approve"),
        ("ineligible", "reject"),
        ("high_risk", "reject"),
    ):
        store.STORE.reset(variant)
        decision = asyncio.run(refund.policy_check.invoke(store.STORE.order))
        assert decision.decision == expected, variant


def test_refund_api_records_successful_refund() -> None:
    store.STORE.reset("eligible")
    order = store.STORE.order
    result = asyncio.run(
        refund.refund_api.invoke(order, facts.PolicyDecision("approve"))
    )
    assert result.success
    assert store.STORE.refunded_order_ids == frozenset({order.order_id})

    rejected = asyncio.run(
        refund.refund_api.invoke(order, facts.PolicyDecision("reject"))
    )
    assert not rejected.success
    assert rejected.refunded_amount == 0.0


def test_tools_carry_differentiated_metering() -> None:
    specs = {node.spec.name: node.spec for node in (
        refund.order_db, refund.erp, refund.rag, refund.web_search,
        refund.policy_check, refund.refund_api,
    )}
    costs = {name: spec.cost_per_call for name, spec in specs.items()}
    assert all(cost is not None and cost > 0 for cost in costs.values())
    # web search is the most expensive read; refund api the most expensive call
    assert costs["web_search"] > costs["order_db"]
    assert costs["refund_api"] == max(costs.values())


# ---- evaluator: continuous quality -------------------------------------------


def _final_result_with(slots: dict) -> FinalResult:
    state = ExecutionState(query="q")
    for name, value in slots.items():
        state.add_artifact(
            name, ArtifactValue(value=value, source_tool="t", layer="read")
        )
    return FinalResult(response=None, state_snapshot=state)


async def _evaluate(evaluator, slots):
    result = await evaluator.evaluate(
        Scenario(id="s", query="q"), _final_result_with(slots), trace=None
    )
    return result


def test_completeness_scores_strictly_between_zero_and_one() -> None:
    evaluator = refund.CompletenessEvaluator()
    full = asyncio.run(_evaluate(evaluator, {
        "order": store.STORE.order,
        "refund_result": facts.RefundResult("ORD-100", True, 1200.0),
        "digest": facts.Digest("d"),
    }))
    assert full.quality_score == 1.0

    partial = asyncio.run(_evaluate(evaluator, {
        "order": store.STORE.order,
        "refund_result": facts.RefundResult("ORD-100", True, 1200.0),
    }))
    assert 0.0 < partial.quality_score < 1.0


def test_composite_quality_is_continuous() -> None:
    store.STORE.reset("eligible")
    evaluator = refund.build_evaluator()
    # successful refund, but the route skipped the summarizer
    result = asyncio.run(_evaluate(evaluator, {
        "order": store.STORE.order,
        "refund_result": facts.RefundResult("ORD-100", True, 1200.0),
    }))
    assert result.success
    assert 0.0 < result.quality_score < 1.0
    assert result.quality_score == pytest.approx(0.7 * 1.0 + 0.3 * (2 / 3))


# ---- end-to-end: variants change outcomes, runs are reproducible ---------------


_ROUTE = FakeRouter(
    layer_selections={
        "read": ["order_db"],
        "analyze": ["policy_check"],
        "action": ["refund_api"],
    }
)


def _run_once(variant: str):
    store.STORE.reset(variant)
    suite = ScenarioSuite(
        name="sandbox",
        version="1.0",
        description="sandbox",
        scenarios=(Scenario(id="s1", query="refund the order"),),
    )
    outcome = asyncio.run(
        SlowRegressionRunner(
            topology=refund.build_topology()[0],
            evaluator=refund.build_evaluator(),
            trials_per_scenario=1,
            topology_version="v0.3.1",
            router_config_id="fake",
            router=_ROUTE,
        ).run(suite)
    )
    return outcome.results[0]


def test_variants_produce_different_business_outcomes() -> None:
    eligible = _run_once("eligible")
    ineligible = _run_once("ineligible")
    assert eligible.evaluation.success
    assert not ineligible.evaluation.success
    assert eligible.evaluation.quality_score > ineligible.evaluation.quality_score


def test_same_variant_reruns_are_identical() -> None:
    first = _run_once("eligible")
    second = _run_once("eligible")
    assert first.route.route_id == second.route.route_id
    assert first.evaluation.success == second.evaluation.success
    assert first.evaluation.quality_score == pytest.approx(
        second.evaluation.quality_score
    )
    assert first.tool_cost == pytest.approx(second.tool_cost)


def test_failure_variant_fails_the_read_layer() -> None:
    result = _run_once("not_found")
    assert result.execution_status.value == "layer_error"
    # the failed attempt is recorded and still billed
    failed = result.trace.layers[-1]
    assert failed.selected_tools == ("order_db",)
    assert failed.tool_executions[0].status.value == "error"
    assert result.tool_cost == pytest.approx(0.001)
