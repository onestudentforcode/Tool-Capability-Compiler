import asyncio

import pytest

from capability_runtime import (
    CounterfactualError,
    CounterfactualRunner,
    CounterfactualVerdict,
    CoverageStatus,
    FastRegressionRunner,
    LayerRegistry,
    Scenario,
    ScenarioSuite,
    ToolRegistry,
    TopologyBuilder,
    TopologyPatch,
    tool,
)


@tool(layer="read", workers=["policy", "policy_backup"], capabilities={"order.read"})
async def db():
    raise AssertionError("Fast Regression must not invoke tools")


@tool(
    layer="analyze",
    providers=["db"],
    capabilities={"refund.policy.check"},
)
async def policy():
    raise AssertionError("Fast Regression must not invoke tools")


@tool(
    layer="analyze",
    providers=["db"],
    capabilities={"refund.policy.check"},
)
async def policy_backup():
    raise AssertionError("Fast Regression must not invoke tools")


@tool(layer="act", providers=["policy"], capabilities={"email.send"})
async def email():
    raise AssertionError("Fast Regression must not invoke tools")


def build_topology():
    layers = LayerRegistry()
    for order, name in enumerate(("read", "analyze", "act")):
        layers.register(name, order)
    tools = ToolRegistry()
    for node in (db, policy, policy_backup, email):
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
                id="refund_backup_covered",
                query="Refund via the backup analyst",
                category="refund",
                expected_capabilities=("order.read", "refund.policy.check"),
            ),
        ),
    )


def test_counterfactual_pass_when_redundant_edge_removed() -> None:
    # Remove db->policy: policy_backup still covers refund.policy.check, so the
    # analyzer can re-wire A->C->B (here the surviving provider) and keep coverage.
    result = asyncio.run(
        CounterfactualRunner().run(
            build_suite(),
            build_topology(),
            TopologyPatch(disabled_edges=("db->policy",)),
            base_version="v1",
        )
    )
    assert result.base_version == "v1"
    assert result.verdict == CounterfactualVerdict.PASS
    assert result.verdict_safe
    assert result.covered_lost_scenarios == ()
    assert result.regressed_scenarios == ()
    assert result.unchanged_count == 2
    assert all(check.unchanged for check in result.checks)


def test_counterfactual_drop_when_unique_provider_removed() -> None:
    # Disable both providers of refund.policy.check: the covered scenarios become
    # UNCOVERED -> verdict is REJECTED (COVERAGE_DROP).
    result = asyncio.run(
        CounterfactualRunner().run(
            build_suite(),
            build_topology(),
            TopologyPatch(disabled_nodes=("policy", "policy_backup")),
            base_version="v1",
        )
    )
    assert result.verdict == CounterfactualVerdict.COVERAGE_DROP
    assert not result.verdict_safe
    assert len(result.covered_lost_scenarios) == 2
    assert len(result.regressed_scenarios) == 2
    for check in result.covered_lost_scenarios:
        assert check.before_status == CoverageStatus.COVERED
        assert check.after_status == CoverageStatus.UNCOVERED
        assert check.regressed
        assert not check.unchanged


def test_counterfactual_reports_before_and_after_reports() -> None:
    result = asyncio.run(
        CounterfactualRunner().run(
            build_suite(),
            build_topology(),
            TopologyPatch(disabled_edges=("db->policy",)),
            base_version="v2",
        )
    )
    assert result.before.topology_version == "v2"
    assert result.after.topology_version == "v2#counterfactual"
    assert result.before.covered == 2
    assert result.after.covered == 2


def test_counterfactual_no_load_path_detected_on_node_removal() -> None:
    result = asyncio.run(
        CounterfactualRunner().run(
            build_suite(),
            build_topology(),
            TopologyPatch(disabled_nodes=("email",)),
            base_version="v1",
        )
    )
    # email is on a leaf layer: disabling it leaves coverage of the analysed
    # scenarios intact, so the run stays a PASS with no covered loss.
    assert result.verdict == CounterfactualVerdict.PASS
    assert result.covered_lost_scenarios == ()


def test_counterfactual_accepts_injected_runner() -> None:
    runner = CounterfactualRunner(runner=FastRegressionRunner())
    result = asyncio.run(
        runner.run(
            build_suite(),
            build_topology(),
            TopologyPatch(),
            base_version="v1",
        )
    )
    assert result.verdict == CounterfactualVerdict.PASS
    assert result.patch.disabled_edges == ()


def test_counterfactual_blank_base_version_rejected() -> None:
    with pytest.raises(CounterfactualError):
        asyncio.run(
            CounterfactualRunner().run(
                build_suite(),
                build_topology(),
                TopologyPatch(),
                base_version=" ",
            )
        )