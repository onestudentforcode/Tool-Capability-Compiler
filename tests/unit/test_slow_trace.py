from datetime import datetime, timedelta

from capability_runtime import (
    ExecutionTrace,
    LayerExecution,
    RoutingAction,
    RoutingContext,
    RoutingDecision,
    ToolExecution,
    ToolExecutionStatus,
    ToolSummary,
)


def _tool_execution(name: str) -> ToolExecution:
    now = datetime.now()
    return ToolExecution(
        tool_name=name,
        layer="analyze",
        started_at=now,
        ended_at=now + timedelta(milliseconds=5),
        status=ToolExecutionStatus.SUCCESS,
        input_summary=(),
        output_summary="ok",
        latency_ms=5.0,
    )


def _layer_execution(**overrides) -> LayerExecution:
    now = datetime.now()
    decision = RoutingDecision(
        action=RoutingAction.EXECUTE, selected_tools=("policy_check",)
    )
    return LayerExecution(
        layer=overrides.get("layer", "analyze"),
        available_tools=overrides.get("available_tools", ("policy_check", "classifier")),
        selected_tools=overrides.get("selected_tools", ("policy_check",)),
        routing_decision=overrides.get("routing_decision", decision),
        tool_executions=overrides.get(
            "tool_executions", (_tool_execution("policy_check"),)
        ),
        started_at=now,
        ended_at=now,
    )


def test_layer_execution_records_full_lifecycle() -> None:
    execution = _layer_execution()
    assert execution.layer == "analyze"
    assert execution.available_tools == ("policy_check", "classifier")
    assert execution.selected_tools == ("policy_check",)
    assert execution.routing_decision.action is RoutingAction.EXECUTE
    assert execution.tool_executions[0].tool_name == "policy_check"
    assert execution.tool_executions[0].status is ToolExecutionStatus.SUCCESS
    assert execution.started_at <= execution.ended_at


def test_execution_trace_aggregates_layers_in_order() -> None:
    trace = ExecutionTrace(
        trial_id="t1",
        scenario_id="s1",
        topology_version="1.0",
        started_at=datetime.now(),
    )
    trace.add_layer(_layer_execution(layer="read"))
    trace.add_layer(_layer_execution(layer="analyze"))

    assert len(trace.layers) == 2
    assert [layer.layer for layer in trace.layers] == ["read", "analyze"]
    assert trace.ended_at is not None and trace.ended_at == trace.layers[-1].ended_at


def test_routing_context_carries_previous_layers() -> None:
    read_layer = _layer_execution(layer="read", selected_tools=("db",))
    context = RoutingContext(
        query="q",
        current_layer="analyze",
        available_tools=(ToolSummary(name="policy_check", layer="analyze"),),
        topology_version="1.0",
        previous_layers=(read_layer,),
    )
    assert context.previous_layers == (read_layer,)


def test_layer_execution_records_tool_error_and_latency() -> None:
    now = datetime.now()
    failed = ToolExecution(
        tool_name="boom",
        layer="act",
        started_at=now,
        ended_at=now + timedelta(milliseconds=30),
        status=ToolExecutionStatus.ERROR,
        input_summary=(),
        output_summary=None,
        latency_ms=30.0,
    )
    layer = _layer_execution(
        layer="act",
        selected_tools=("boom",),
        tool_executions=(failed,),
    )
    assert layer.tool_executions[0].status is ToolExecutionStatus.ERROR
    assert layer.tool_executions[0].latency_ms == 30.0