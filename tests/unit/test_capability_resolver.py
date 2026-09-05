import asyncio

import pytest

from capability_runtime import (
    CapabilityResolution,
    CapabilityResolutionError,
    CoverageStatus,
    FakeCapabilityResolver,
    FastRegressionRunner,
    FailureReason,
    LayerRegistry,
    Scenario,
    ScenarioSuite,
    ToolRegistry,
    TopologyBuilder,
    tool,
)


def test_resolution_is_structured_normalized_and_validated() -> None:
    resolution = CapabilityResolution(
        required=("refund.policy.check", "order.read"),
        optional=("policy.read",),
        missing_capability_hints=("refund.execute",),
        confidence=0.9,
        reasoning="  metadata match  ",
    )
    assert resolution.required == ("order.read", "refund.policy.check")
    assert resolution.reasoning == "metadata match"

    with pytest.raises(CapabilityResolutionError):
        CapabilityResolution(required=("order.read",), optional=("order.read",))
    with pytest.raises(CapabilityResolutionError):
        CapabilityResolution(required=("OrderRead",))
    with pytest.raises(CapabilityResolutionError):
        CapabilityResolution(required=(), confidence=1.1)


def test_fake_resolver_enforces_available_capabilities() -> None:
    resolver = FakeCapabilityResolver(
        {"query": CapabilityResolution(required=("order.read",))}
    )
    result = asyncio.run(resolver.resolve("query", {"order.read"}))
    assert result.required == ("order.read",)
    assert resolver.calls == ["query"]

    with pytest.raises(CapabilityResolutionError):
        asyncio.run(resolver.resolve("query", {"policy.read"}))


@tool(layer="read", capabilities={"order.read"})
async def db():
    raise AssertionError("Fast Regression must not invoke tools")


def topology():
    layers = LayerRegistry()
    layers.register("read", 0)
    tools = ToolRegistry()
    tools.register(db)
    return TopologyBuilder(layers, tools).build()


def suite(scenario: Scenario) -> ScenarioSuite:
    return ScenarioSuite(name="discovery", version="v1", scenarios=(scenario,))


def test_discovery_mode_uses_fake_resolver_and_preserves_optional() -> None:
    query = "find my order"
    resolver = FakeCapabilityResolver(
        {
            query: CapabilityResolution(
                required=("order.read",),
                optional=("order.search",),
                confidence=0.95,
            )
        }
    )
    # Optional capabilities must also come from the advertised capability set.
    @tool(layer="read", capabilities={"order.read", "order.search"})
    async def searchable_db(): return None

    layers = LayerRegistry()
    layers.register("read", 0)
    tools = ToolRegistry()
    tools.register(searchable_db)
    searchable_topology = TopologyBuilder(layers, tools).build()

    report = asyncio.run(
        FastRegressionRunner(resolver=resolver).run(
            suite(Scenario(id="q1", query=query)), searchable_topology
        )
    )
    result = report.results[0]
    assert result.status == CoverageStatus.COVERED
    assert result.optional_capabilities == ("order.search",)
    assert result.candidate_routes


def test_discovery_low_confidence_and_missing_hints() -> None:
    low = FakeCapabilityResolver(
        {
            "unclear": CapabilityResolution(
                required=("order.read",), confidence=0.2
            )
        }
    )
    low_report = asyncio.run(
        FastRegressionRunner(resolver=low).run(
            suite(Scenario(id="low", query="unclear")), topology()
        )
    )
    assert low_report.results[0].status == CoverageStatus.UNCERTAIN
    assert low_report.results[0].reason == FailureReason.LOW_RESOLUTION_CONFIDENCE

    gap = FakeCapabilityResolver(
        {
            "send invoice": CapabilityResolution(
                required=("order.read",),
                missing_capability_hints=("invoice.send",),
                confidence=0.9,
            )
        }
    )
    gap_report = asyncio.run(
        FastRegressionRunner(resolver=gap).run(
            suite(Scenario(id="gap", query="send invoice")), topology()
        )
    )
    result = gap_report.results[0]
    assert result.status == CoverageStatus.UNCOVERED
    assert result.reason == FailureReason.MISSING_CAPABILITY
    assert result.missing_capability_hints == ("invoice.send",)
    assert gap_report.missing_capabilities[0].capability == "invoice.send"


def test_gold_mode_never_calls_resolver() -> None:
    resolver = FakeCapabilityResolver({})
    gold = Scenario(
        id="gold",
        query="read order",
        expected_capabilities=("order.read",),
    )
    report = asyncio.run(
        FastRegressionRunner(resolver=resolver).run(suite(gold), topology())
    )
    assert report.results[0].status == CoverageStatus.COVERED
    assert resolver.calls == []
