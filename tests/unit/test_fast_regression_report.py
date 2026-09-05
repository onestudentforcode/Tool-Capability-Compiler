import asyncio

import pytest

from capability_runtime import (
    CoverageStatus,
    FastRegressionError,
    FastRegressionRunner,
    FailureReason,
    LayerRegistry,
    Scenario,
    ScenarioSuite,
    ToolRegistry,
    TopologyBuilder,
    tool,
)


@tool(layer="read", workers=["policy"], capabilities={"order.read"})
async def db():
    raise AssertionError("Fast Regression must not invoke tools")


@tool(
    layer="analyze",
    providers=["db"],
    capabilities={"refund.policy.check"},
)
async def policy():
    raise AssertionError("Fast Regression must not invoke tools")


@tool(layer="act", providers=[], capabilities={"email.send"})
async def email():
    raise AssertionError("Fast Regression must not invoke tools")


def build_topology():
    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "act")):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in (db, policy, email):
        tools.register(node)
    return TopologyBuilder(layers, tools).build()


def build_suite() -> ScenarioSuite:
    return ScenarioSuite(
        name="customer-service",
        version="v1",
        scenarios=(
            Scenario(
                id="refund_covered",
                query="Can this order be refunded?",
                category="refund",
                expected_capabilities=("order.read", "refund.policy.check"),
            ),
            Scenario(
                id="invoice_missing",
                query="Send the invoice",
                category="invoice",
                expected_capabilities=("invoice.send",),
            ),
            Scenario(
                id="email_disconnected",
                query="Read the order and send an email",
                category="email",
                expected_capabilities=("order.read", "email.send"),
            ),
            Scenario(
                id="refund_discovery",
                query="Can I return this?",
                category="refund",
            ),
        ),
    )


def test_runner_aggregates_coverage_categories_and_gaps() -> None:
    report = asyncio.run(
        FastRegressionRunner().run(
            build_suite(), build_topology(), topology_version="declared-v1"
        )
    )

    assert report.suite_name == "customer-service"
    assert report.suite_version == "v1"
    assert report.topology_version == "declared-v1"
    assert (report.total, report.covered, report.uncertain, report.uncovered) == (
        4,
        1,
        1,
        2,
    )
    assert report.coverage_rate == 0.25
    assert [category.category for category in report.categories] == [
        "email",
        "invoice",
        "refund",
    ]
    refund = report.categories[2]
    assert (refund.total, refund.covered, refund.uncertain, refund.uncovered) == (
        2,
        1,
        1,
        0,
    )
    assert refund.coverage_rate == 0.5

    assert [entry.capability for entry in report.missing_capabilities] == [
        "invoice.send"
    ]
    assert report.missing_capabilities[0].scenario_ids == ("invoice_missing",)
    assert [entry.required_capabilities for entry in report.topology_gaps] == [
        ("email.send", "order.read")
    ]
    assert report.topology_gaps[0].scenario_ids == ("email_disconnected",)


def test_results_preserve_scenario_order_and_candidate_routes() -> None:
    report = asyncio.run(
        FastRegressionRunner().run(build_suite(), build_topology())
    )
    assert [result.scenario_id for result in report.results] == [
        "refund_covered",
        "invoice_missing",
        "email_disconnected",
        "refund_discovery",
    ]
    assert report.results[0].status == CoverageStatus.COVERED
    assert report.results[0].candidate_routes
    assert report.results[-1].status == CoverageStatus.UNCERTAIN
    assert report.results[-1].reason == FailureReason.INVALID_SCENARIO


def test_invalid_topology_version_is_rejected() -> None:
    with pytest.raises(FastRegressionError):
        asyncio.run(
            FastRegressionRunner().run(
                build_suite(), build_topology(), topology_version=" "
            )
        )
