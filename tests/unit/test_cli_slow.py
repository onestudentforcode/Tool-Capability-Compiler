"""CLI `regression slow` subcommand behavior (phase3 §107-108)."""

from __future__ import annotations

import json

import pytest

from capability_runtime.cli import main, load_seed_routes

TOPO = {
    "version": "1.0",
    "layers": [
        {"name": "read", "order": 0},
        {"name": "analyze", "order": 1},
    ],
    "tools": [
        {"name": "db", "layer": "read", "capabilities": ["order.read"]},
        {"name": "policy", "layer": "analyze", "capabilities": ["refund.policy.check"]},
    ],
}

SCENARIOS = {
    "version": "1.0",
    "name": "slow_svc",
    "scenarios": [{"id": "s1", "query": "check refund"}],
}

SEEDS = {
    "s1": {
        "layers": [
            {"layer": "read", "tools": ["db"]},
            {"layer": "analyze", "tools": ["policy"]},
        ],
        "capabilities": ["order.read", "refund.policy.check"],
    }
}


@pytest.fixture
def files(tmp_path):
    topo = tmp_path / "topo.json"
    topo.write_text(json.dumps(TOPO), encoding="utf-8")
    scen = tmp_path / "scen.json"
    scen.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps(SEEDS), encoding="utf-8")
    return topo, scen, seeds


def test_load_seed_routes_parses_candidate_route_objects(files) -> None:
    _, _, seeds = files
    routes = load_seed_routes(str(seeds))
    assert set(routes) == {"s1"}
    route = routes["s1"]
    assert [seg.layer for seg in route.layers] == ["read", "analyze"]
    assert route.layers[0].tools == ("db",)


def test_cli_slow_free_mode_prints_report(files, capsys) -> None:
    topo, scen, _ = files
    code = main(["regression", "slow", "--topology", str(topo), "--scenario", str(scen)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Slow Regression" in out
    assert "Suite:  slow_svc 1.0" in out
    assert "Scenarios:       1" in out
    assert "Trials:          5" in out
    assert "Completed:        5" in out
    assert "Business Success: 5" in out  # empty expected facts -> every trial succeeds
    # §108 boundary: never a pruning recommendation
    assert "Recommend" not in out.lower()


def test_cli_slow_basefast_seeds_and_expected_facts(files, capsys) -> None:
    topo, scen, seeds = files
    code = main(
        [
            "regression",
            "slow",
            "--topology",
            str(topo),
            "--scenario",
            str(scen),
            "--trials",
            "2",
            "--basefast",
            str(seeds),
            "--expected-fact",
            "order.read=1",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Router config: basefast" in out
    assert "Business Failed:  2" in out


def test_cli_slow_writes_artifacts_when_out_dir_given(files, capsys, tmp_path) -> None:
    topo, scen, seeds = files
    out_dir = tmp_path / "run"
    code = main(
        [
            "regression",
            "slow",
            "--topology",
            str(topo),
            "--scenario",
            str(scen),
            "--trials",
            "2",
            "--basefast",
            str(seeds),
            "--out-dir",
            str(out_dir),
        ]
    )
    capsys.readouterr()  # reports also stream to stdout
    assert code == 0
    for name in (
        "manifest.json",
        "report.json",
        "traces.jsonl",
        "node_stats.json",
        "edge_stats.json",
        "route_stats.json",
    ):
        assert out_dir.joinpath(name).exists(), name
    manifest = json.loads(out_dir.joinpath("manifest.json").read_text(encoding="utf-8"))
    assert manifest["trial_count"] == 2
    assert manifest["router_config_id"] == "basefast"


def test_cli_slow_requires_topology(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["regression", "slow", "--scenario", "x.json"])