from datetime import datetime

import pytest

from capability_runtime import (
    ExecutionError,
    ExecutionTrace,
    LayerExecution,
    ObservedRoute,
    RoutingDecision,
    RoutingAction,
    extract_observed_route,
)


def _layer(layer: str, selected: tuple[str, ...]) -> LayerExecution:
    now = datetime.now()
    return LayerExecution(
        layer=layer,
        available_tools=selected,
        selected_tools=selected,
        routing_decision=RoutingDecision(
            action=RoutingAction.EXECUTE, selected_tools=selected
        ),
        tool_executions=(),
        started_at=now,
        ended_at=now,
    )


def test_from_layers_sorts_tools_ascending() -> None:
    route = ObservedRoute.from_layers((("read", ("rag", "db")),))
    assert route.segments[0].tools == ("db", "rag")


def test_sibling_order_does_not_change_route_id() -> None:
    a = ObservedRoute.from_layers((("read", ("rag", "db")), ("analyze", ("pc",))))
    b = ObservedRoute.from_layers((("read", ("db", "rag")), ("analyze", ("pc",))))
    assert a.route_id == b.route_id


def test_same_structure_yields_stable_route_id() -> None:
    a = ObservedRoute.from_layers((("read", ["db"]), ("action", ["refund"])))
    b = ObservedRoute.from_layers((("read", ["db"]), ("action", ["refund"])))
    assert a.route_id == b.route_id
    assert a.route_id


def test_different_structure_yields_different_route_id() -> None:
    a = ObservedRoute.from_layers((("read", ["db"]),))
    b = ObservedRoute.from_layers((("read", ["rag"]),))
    assert a.route_id != b.route_id


def test_canonical_text_format() -> None:
    route = ObservedRoute.from_layers((("read", ("db", "rag")), ("analyse", ("pc",))))
    assert route.canonical == "read:[db,rag]\nanalyse:[pc]"


def test_empty_segment_ignored_but_all_empty_raises() -> None:
    # a layer that produced no tools (FINISH) is dropped
    route = ObservedRoute.from_layers((("read", ("db",)), ("analyze", ())))
    assert [seg.layer for seg in route.segments] == ["read"]

    with pytest.raises(ExecutionError):
        ObservedRoute.from_layers((("read", ()), ("analyze", ())))


def test_extract_observed_route_from_trace() -> None:
    trace = ExecutionTrace(
        trial_id="t1", scenario_id="s1", topology_version="1.0"
    )
    trace.add_layer(_layer("read", ("rag", "db")))
    trace.add_layer(_layer("analyze", ("policy_check",)))

    route = extract_observed_route(trace)
    assert [(seg.layer, seg.tools) for seg in route.segments] == [
        ("read", ("db", "rag")),
        ("analyze", ("policy_check",)),
    ]
    assert route.route_id == ObservedRoute.from_layers(
        (("read", ("rag", "db")), ("analyze", ("policy_check",)))
    ).route_id