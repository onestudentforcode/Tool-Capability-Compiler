"""Battlefield Hardening Batch B: JSON topology <-> executable binding.

An optional ``"implementation": "module:attr"`` entry point on each JSON tool
binds a real async handler; unbound tools stay on the null placeholder and must
be refused by slow regression instead of silently running as no-ops
(battlefield-hardening §3).
"""

from __future__ import annotations

import asyncio

import pytest

from capability_runtime import (
    EvaluationResult,
    Evaluator,
    Scenario,
    ScenarioSuite,
    SlowRegressionRunner,
    ToolExecutionStatus,
    TopologyLoader,
    unbound_tool_names,
)
from capability_runtime.core.errors import TopologyBuildError

_BINDING_TOOLS = "tests.unit._binding_tools"


def _suite_doc(*tools) -> dict:
    return {
        "version": "1.0",
        "layers": [
            {"name": "read", "order": 0},
            {"name": "analyze", "order": 1},
        ],
        "tools": list(tools),
    }


def _tool(name: str, layer: str, implementation: str | None) -> dict:
    entry = {"name": name, "layer": layer}
    if implementation is not None:
        entry["implementation"] = implementation
    return entry


# ---- entry-point resolution --------------------------------------------------


def test_binds_declared_implementation() -> None:
    topology = TopologyLoader().load_data(
        _suite_doc(_tool("db", "read", f"{_BINDING_TOOLS}:fetch"))
    )
    from tests.unit import _binding_tools

    assert topology.node("db").handler is _binding_tools.fetch
    assert unbound_tool_names(topology) == ()


def test_default_stays_on_null_placeholder() -> None:
    topology = TopologyLoader().load_data(
        _suite_doc(_tool("db", "read", None), _tool("x", "analyze", None))
    )
    assert unbound_tool_names(topology) == ("db", "x")


def test_rejects_malformed_reference() -> None:
    for bad in ("no-colon", "a:b:c", "", " :attr", "mod:"):
        with pytest.raises(TopologyBuildError, match="implementation"):
            TopologyLoader().load_data(
                _suite_doc(_tool("db", "read", bad))
            )


def test_rejects_non_string_reference() -> None:
    with pytest.raises(TopologyBuildError, match="implementation"):
        TopologyLoader().load_data(
            _suite_doc(_tool("db", "read", 42))
        )


def test_rejects_unknown_module() -> None:
    with pytest.raises(TopologyBuildError, match="cannot import module"):
        TopologyLoader().load_data(
            _suite_doc(_tool("db", "read", "no_such_module_zz:fetch"))
        )


def test_rejects_missing_attribute() -> None:
    with pytest.raises(TopologyBuildError, match="no attribute"):
        TopologyLoader().load_data(
            _suite_doc(_tool("db", "read", f"{_BINDING_TOOLS}:missing"))
        )


def test_rejects_synchronous_handler() -> None:
    with pytest.raises(TopologyBuildError, match="async"):
        TopologyLoader().load_data(
            _suite_doc(_tool("db", "read", "math:sqrt"))
        )


def test_error_names_the_tool_and_location() -> None:
    with pytest.raises(TopologyBuildError, match="'db'"):
        TopologyLoader().load_data(
            _suite_doc(_tool("db", "read", f"{_BINDING_TOOLS}:missing"))
        )


# ---- real execution through slow regression -----------------------------------


class _Pass(Evaluator):
    async def evaluate(self, scenario, result, trace) -> EvaluationResult:
        return EvaluationResult(success=True)


def test_bound_topology_executes_real_handlers_end_to_end() -> None:
    topology = TopologyLoader().load_data(
        _suite_doc(
            _tool("db", "read", f"{_BINDING_TOOLS}:fetch"),
            _tool("scan", "read", f"{_BINDING_TOOLS}:analyze"),
        )
    )
    suite = ScenarioSuite(
        name="binding",
        version="1.0",
        description="binding",
        scenarios=(Scenario(id="s1", query="q"),),
    )
    outcome = asyncio.run(
        SlowRegressionRunner(
            topology=topology,
            evaluator=_Pass(),
            trials_per_scenario=1,
            topology_version="1.0",
        ).run(suite)
    )
    result = outcome.results[0]
    outputs = {
        execution.tool_name: execution.output_summary
        for layer in result.trace.layers
        for execution in layer.tool_executions
    }
    assert result.execution_status.value == "completed"
    # real return values, not the null placeholder's None
    assert outputs["db"].text == "fetched:ORD-1"
    assert outputs["scan"].text == "analyzed:eligible"
    assert all(
        execution.status is ToolExecutionStatus.SUCCESS
        for layer in result.trace.layers
        for execution in layer.tool_executions
    )


def test_unbound_placeholder_produces_none_outputs() -> None:
    # Documents why the CLI refuses unbound tools: the placeholder "succeeds"
    # while writing nothing, which would silently poison statistics.
    topology = TopologyLoader().load_data(_suite_doc(_tool("db", "read", None)))
    suite = ScenarioSuite(
        name="binding",
        version="1.0",
        description="binding",
        scenarios=(Scenario(id="s1", query="q"),),
    )
    outcome = asyncio.run(
        SlowRegressionRunner(
            topology=topology,
            evaluator=_Pass(),
            trials_per_scenario=1,
            topology_version="1.0",
        ).run(suite)
    )
    execution = outcome.results[0].trace.layers[0].tool_executions[0]
    assert execution.status is ToolExecutionStatus.SUCCESS
    assert execution.output_summary is None
