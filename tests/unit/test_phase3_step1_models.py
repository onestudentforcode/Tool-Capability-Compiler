from datetime import datetime

import pytest

from capability_runtime import (
    ArtifactValue,
    ExecutionContext,
    ExecutionEnvironment,
    ExecutionError,
    ExecutionState,
    TokenUsage,
    Trial,
    TrialExecutionStatus,
)


def test_artifact_value_records_source_and_layer() -> None:
    artifact = ArtifactValue(value={"order": "001"}, source_tool="db", layer="read")
    assert artifact.source_tool == "db"
    assert artifact.layer == "read"
    assert isinstance(artifact.timestamp, datetime)


@pytest.mark.parametrize(
    ("source_tool", "layer"),
    [("", "read"), ("  ", "read"), ("db", ""), ("db", "  ")],
)
def test_artifact_value_rejects_blank_source_or_layer(source_tool, layer) -> None:
    with pytest.raises(ExecutionError):
        ArtifactValue(value=1, source_tool=source_tool, layer=layer)


def test_execution_state_keeps_multi_source_artifacts_not_overwrite() -> None:
    state = ExecutionState(query="can order 001 be refunded")
    state.add_artifact("order", ArtifactValue(value=1, source_tool="db", layer="read"))
    state.add_artifact(
        "order", ArtifactValue(value=2, source_tool="erp", layer="read")
    )

    artifacts = state.get_artifacts("order")
    assert len(artifacts) == 2
    assert {a.source_tool for a in artifacts} == {"db", "erp"}
    assert state.latest("order").source_tool == "erp"


def test_execution_state_isolates_distinct_names_and_sorts() -> None:
    state = ExecutionState(query="q")
    state.add_artifact("z", ArtifactValue(value=1, source_tool="a", layer="l"))
    state.add_artifact("a", ArtifactValue(value=2, source_tool="b", layer="l"))
    assert state.names() == ("a", "z")
    assert state.get_artifacts("missing") == ()


def test_execution_state_rejects_bad_inputs() -> None:
    with pytest.raises(ExecutionError):
        ExecutionState(query="   ")
    with pytest.raises(ExecutionError):
        ExecutionState(query="q", scenario_inputs=[1, 2])
    state = ExecutionState(query="q")
    with pytest.raises(ExecutionError):
        state.add_artifact("", ArtifactValue(value=1, source_tool="a", layer="l"))
    with pytest.raises(ExecutionError):
        state.add_artifact("x", "not-an-artifact")


def test_execution_state_copies_scenario_inputs() -> None:
    inputs = {"order_id": "001"}
    state = ExecutionState(query="q", scenario_inputs=inputs)
    inputs["order_id"] = "mutated"
    assert state.scenario_inputs["order_id"] == "001"


def test_execution_context_validates_environment_and_counts() -> None:
    ctx = ExecutionContext(environment=ExecutionEnvironment.SANDBOX, max_concurrency=4)
    assert ctx.environment is ExecutionEnvironment.SANDBOX
    assert ctx.max_concurrency == 4
    assert ctx.per_tool_timeout_seconds is None

    with pytest.raises(ExecutionError):
        ExecutionContext(environment="sandbox")  # not an enum
    with pytest.raises(ExecutionError):
        ExecutionContext(environment=ExecutionEnvironment.MOCK, max_concurrency=0)
    with pytest.raises(ExecutionError):
        ExecutionContext(
            environment=ExecutionEnvironment.MOCK, per_tool_timeout_seconds=-1
        )


def test_token_usage_total() -> None:
    assert TokenUsage(input_tokens=10, output_tokens=5).total == 15
    assert TokenUsage().total == 0


def test_trial_accepts_valid_fields() -> None:
    trial = Trial(
        id="t1",
        scenario_id="s1",
        trial_index=0,
        topology_version="1.0",
        scenario_suite_version="2.0",
        router_config_id="default",
    )
    assert trial.router_config_id == "default"


@pytest.mark.parametrize("field", ["id", "scenario_id", "topology_version", "scenario_suite_version"])
def test_trial_rejects_empty_identity_fields(field) -> None:
    kwargs = {
        "id": "t1",
        "scenario_id": "s1",
        "trial_index": 0,
        "topology_version": "1.0",
        "scenario_suite_version": "2.0",
        "router_config_id": "default",
    }
    kwargs[field] = "  "
    with pytest.raises(ExecutionError):
        Trial(**kwargs)


def test_trial_rejects_invalid_index_or_router_id() -> None:
    args = dict(
        id="t1",
        scenario_id="s1",
        trial_index=0,
        topology_version="1.0",
        scenario_suite_version="2.0",
        router_config_id="default",
    )
    args["trial_index"] = -1
    with pytest.raises(ExecutionError):
        Trial(**args)
    args["trial_index"] = 0
    args["router_config_id"] = " "
    with pytest.raises(ExecutionError):
        Trial(**args)


def test_trial_execution_status_has_expected_members() -> None:
    members = {status.value for status in TrialExecutionStatus}
    assert members == {
        "completed",
        "routing_error",
        "tool_error",
        "layer_error",
        "evaluation_error",
        "fixture_error",
    }