from __future__ import annotations

from capability_runtime import CandidateRoute, RouteLayer


def _route(*layers: tuple[str, tuple[str, ...]]) -> CandidateRoute:
    return CandidateRoute(
        layers=tuple(RouteLayer(layer, tools) for layer, tools in layers),
        capabilities=frozenset({"order.read", "refund.policy.check"}),
    )


def test_candidate_route_creation() -> None:
    route = _route(("read", ("db",)), ("analyze", ("policy_check",)))
    assert len(route.layers) == 2
    assert route.layers[0].layer == "read"
    assert route.layers[0].tools == ("db",)
    assert route.capabilities == frozenset({"order.read", "refund.policy.check"})


def test_route_id_is_deterministic() -> None:
    a = _route(("read", ("db",)), ("analyze", ("policy_check",)))
    b = _route(("read", ("db",)), ("analyze", ("policy_check",)))
    assert a.route_id == b.route_id


def test_route_id_is_order_independent_within_layer() -> None:
    a = _route(("read", ("db", "rag")), ("analyze", ("policy_check",)))
    b = _route(("read", ("rag", "db")), ("analyze", ("policy_check",)))
    assert a.route_id == b.route_id


def test_route_id_differs_for_different_routes() -> None:
    a = _route(("read", ("db",)), ("analyze", ("policy_check",)))
    b = _route(("read", ("rag",)), ("analyze", ("policy_check",)))
    assert a.route_id != b.route_id


def test_candidate_route_is_frozen() -> None:
    route = _route(("read", ("db",)))
    try:
        route.layers = ()  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("CandidateRoute should be frozen")


def test_candidate_route_is_hashable() -> None:
    route = _route(("read", ("db",)), ("analyze", ("policy_check",)))
    {route}
    {route: 1}


def test_capabilities_is_frozenset() -> None:
    route = _route(("read", ("db",)))
    assert isinstance(route.capabilities, frozenset)
