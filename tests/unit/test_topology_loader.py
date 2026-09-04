from __future__ import annotations

import json

import pytest

from capability_runtime import TopologyBuildError, TopologyLoader


MINIMAL = {
    "version": "1.0",
    "layers": [
        {"name": "read", "order": 0},
        {"name": "analyze", "order": 1},
    ],
    "tools": [
        {
            "name": "db",
            "layer": "read",
            "workers": ["policy"],
            "capabilities": ["order.read"],
        },
        {
            "name": "policy",
            "layer": "analyze",
            "providers": ["db"],
            "capabilities": ["refund.policy.check"],
        },
    ],
}


def load(data):
    return TopologyLoader().load_data(json.loads(data))


def test_loads_declarative_topology_with_edges() -> None:
    topology = load(json.dumps(MINIMAL))
    assert set(topology.nodes()) == {"db", "policy"}
    assert {(e.source, e.target) for e in topology.edges()} == {("db", "policy")}
    assert topology.node("db").spec.layer == "read"
    assert topology.node("db").spec.capabilities == frozenset({"order.read"})
    assert topology.warnings() == ()


def test_load_file_round_trip(tmp_path) -> None:
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(MINIMAL), encoding="utf-8")
    topology = TopologyLoader().load_file(path)
    assert set(topology.nodes()) == {"db", "policy"}


def test_rejects_unknown_field(tmp_path) -> None:
    bad = dict(MINIMAL)
    bad["extra"] = True
    with pytest.raises(TopologyBuildError, match="unknown fields: extra"):
        TopologyLoader().load_data(bad)


def test_rejects_missing_required_tool_field() -> None:
    bad = json.loads(json.dumps(MINIMAL))
    del bad["tools"][0]["layer"]
    with pytest.raises(TopologyBuildError, match="missing required fields: layer"):
        TopologyLoader().load_data(bad)


def test_rejects_invalid_capability() -> None:
    bad = json.loads(json.dumps(MINIMAL))
    bad["tools"][0]["capabilities"] = ["Order.Read"]
    with pytest.raises(TopologyBuildError):
        TopologyLoader().load_data(bad)


def test_rejects_invalid_selector() -> None:
    bad = json.loads(json.dumps(MINIMAL))
    bad["tools"][0]["workers"] = 7
    with pytest.raises(TopologyBuildError):
        TopologyLoader().load_data(bad)


def test_rejects_bad_selector_case_duplicates() -> None:
    bad = json.loads(json.dumps(MINIMAL))
    bad["tools"][0]["workers"] = ["policy", "policy"]
    with pytest.raises(TopologyBuildError):
        TopologyLoader().load_data(bad)


def test_rejects_non_object_tool() -> None:
    bad = json.loads(json.dumps(MINIMAL))
    bad["tools"].append("not-an-object")
    with pytest.raises(TopologyBuildError):
        TopologyLoader().load_data(bad)


def test_cross_layer_worker_reference_rejected() -> None:
    bad = json.loads(json.dumps(MINIMAL))
    bad["layers"].append({"name": "act", "order": 2})
    # read tool may not target act-level tool as its worker; builder rejects non-adjacent
    bad["tools"][0]["workers"] = ["refund"]
    bad["tools"].append(
        {"name": "refund", "layer": "act", "providers": ["db"], "capabilities": []}
    )
    with pytest.raises(TopologyBuildError):
        TopologyLoader().load_data(bad)