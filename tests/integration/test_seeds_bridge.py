"""CLI bridge end-to-end: fast --out-dir -> seeds export -> slow --basefast.

Covers the seeds.json v2 fingerprint binding (mismatch refuses the slow
run unless explicitly waived) and legacy v1 seeds backward compatibility.
All offline: the bridge tools are deterministic and source_bad raises on
purpose so the exporter has a runtime failure to fall through.
"""

from __future__ import annotations

import json

from capability_runtime import TopologyLoader
from capability_runtime.cli import main
from capability_runtime.optimization.artifacts import declared_fingerprint

_BRIDGE = "tests.unit._seed_bridge_tools"

_LAYERS = [
    {"name": "read", "order": 0},
    {"name": "analyze", "order": 1},
    {"name": "act", "order": 2},
]

_TOOLS = [
    ("source_bad", "read", ["data.read"]),
    ("source_good", "read", ["data.read"]),
    ("decide", "analyze", ["task.decide"]),
    ("archive", "act", ["act.archive"]),
]


def _topology_payload() -> dict:
    return {
        "version": "seed-bridge-v1",
        "layers": _LAYERS,
        "tools": [
            {
                "name": name,
                "layer": layer,
                "capabilities": caps,
                "implementation": f"{_BRIDGE}:{name}",
            }
            for name, layer, caps in _TOOLS
        ],
    }


def _scenarios_payload() -> dict:
    return {
        "name": "seed_bridge_cli",
        "version": "0.1",
        "scenarios": [
            {"id": "S1", "query": "decide",
             "expected_capabilities": ["data.read", "task.decide"]},
            {"id": "S2", "query": "decide only",
             "expected_capabilities": ["task.decide"]},
        ],
    }


def _write_files(tmp_path):
    topo = tmp_path / "topology.json"
    topo.write_text(json.dumps(_topology_payload()), encoding="utf-8")
    scen = tmp_path / "scenarios.json"
    scen.write_text(json.dumps(_scenarios_payload()), encoding="utf-8")
    return topo, scen


def _fast_report(tmp_path, topo, scen):
    fast_dir = tmp_path / "fast"
    code = main([
        "regression", "fast",
        "--topology", str(topo), "--scenario", str(scen),
        "--out-dir", str(fast_dir),
    ])
    assert code == 0
    return fast_dir


def test_fast_report_persists_candidate_routes(tmp_path) -> None:
    topo, scen = _write_files(tmp_path)
    fast_dir = _fast_report(tmp_path, topo, scen)
    report = json.loads((fast_dir / "report.json").read_text(encoding="utf-8"))
    by_id = {item["scenario_id"]: item for item in report["results"]}
    assert report["stage"] == "fast"
    # S1 has two subset-minimal chains: source_bad first (fingerprint
    # order), then source_good.
    assert len(by_id["S1"]["candidate_routes"]) == 2
    first = by_id["S1"]["candidate_routes"][0]
    assert first["layers"][0]["tools"] == ["source_bad"]


def test_seeds_export_and_strict_slow(tmp_path) -> None:
    topo, scen = _write_files(tmp_path)
    fast_dir = _fast_report(tmp_path, topo, scen)

    seeds = tmp_path / "seeds.json"
    code = main([
        "seeds", "export",
        "--topology", str(topo), "--scenario", str(scen),
        "--fast-report", str(fast_dir), "--out", str(seeds),
    ])
    assert code == 0
    payload = json.loads(seeds.read_text(encoding="utf-8"))
    assert payload["format_version"] == 2
    assert set(payload["seeds"]) == {"S1"}
    entries = {e["scenario_id"]: e for e in payload["entries"]}
    assert entries["S1"]["status"] == "frozen"
    assert entries["S2"]["status"] == "replay-failed"
    topology = TopologyLoader().load_file(str(topo))
    assert payload["topology_fingerprint"] == declared_fingerprint(topology)

    # the frozen seeds drive the slow run
    assert main([
        "regression", "slow",
        "--topology", str(topo), "--scenario", str(scen),
        "--basefast", str(seeds), "--trials", "1",
    ]) == 0


def test_fingerprint_mismatch_refuses_slow_run(tmp_path) -> None:
    topo, scen = _write_files(tmp_path)
    fast_dir = _fast_report(tmp_path, topo, scen)
    seeds = tmp_path / "seeds.json"
    main([
        "seeds", "export",
        "--topology", str(topo), "--scenario", str(scen),
        "--fast-report", str(fast_dir), "--out", str(seeds),
    ])

    payload = json.loads(seeds.read_text(encoding="utf-8"))
    payload["topology_fingerprint"] = "deadbeefdeadbeef"
    seeds.write_text(json.dumps(payload), encoding="utf-8")

    code = main([
        "regression", "slow",
        "--topology", str(topo), "--scenario", str(scen),
        "--basefast", str(seeds), "--trials", "1",
    ])
    assert code == 2

    code = main([
        "regression", "slow",
        "--topology", str(topo), "--scenario", str(scen),
        "--basefast", str(seeds), "--trials", "1",
        "--allow-seed-mismatch",
    ])
    assert code == 0


def test_legacy_seeds_still_load(tmp_path) -> None:
    topo, scen = _write_files(tmp_path)
    seeds = tmp_path / "legacy_seeds.json"
    seeds.write_text(json.dumps({
        "S1": {
            "layers": [
                {"layer": "read", "tools": ["source_good"]},
                {"layer": "analyze", "tools": ["decide"]},
            ],
            "capabilities": ["data.read", "task.decide"],
        },
    }), encoding="utf-8")
    code = main([
        "regression", "slow",
        "--topology", str(topo), "--scenario", str(scen),
        "--basefast", str(seeds), "--trials", "1",
    ])
    assert code == 0


def test_seeds_discover_scripted_router_end_to_end(tmp_path) -> None:
    topo, scen = _write_files(tmp_path)
    routing = tmp_path / "routing.json"
    routing.write_text(json.dumps({
        "S1": {"read": ["source_good"], "analyze": ["decide"], "act": ["archive"]},
        "S2": {},
    }), encoding="utf-8")
    seeds = tmp_path / "discovered.json"

    code = main([
        "seeds", "discover",
        "--topology", str(topo), "--scenario", str(scen),
        "--scripted-router", str(routing), "--out", str(seeds),
    ])
    assert code == 0
    payload = json.loads(seeds.read_text(encoding="utf-8"))
    assert payload["source"] == "model-discovery"
    assert set(payload["seeds"]) == {"S1"}
    entries = {e["scenario_id"]: e for e in payload["entries"]}
    assert entries["S1"]["status"] == "frozen"
    assert entries["S2"]["status"] == "discovery-failed"
    topology = TopologyLoader().load_file(str(topo))
    assert payload["topology_fingerprint"] == declared_fingerprint(topology)

    # discovered chains replay through the standard slow path
    assert main([
        "regression", "slow",
        "--topology", str(topo), "--scenario", str(scen),
        "--basefast", str(seeds), "--trials", "1",
    ]) == 0
