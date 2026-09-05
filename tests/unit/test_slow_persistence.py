"""Tests for SlowRegressionWriter JSONL persistence (phase3 §104-106)."""

from __future__ import annotations

import json

from capability_runtime import (
    SlowRegressionWriter,
    build_observation_stats,
    build_slow_regression_report,
)

from _slow_helpers import build_topology, make_suite, sync_run

ARTIFACT_NAMES = (
    "manifest.json",
    "report.json",
    "traces.jsonl",
    "node_stats.json",
    "edge_stats.json",
    "route_stats.json",
)


def _run_artifacts(tmp_path, trials=3):
    topology = build_topology()
    suite = make_suite()
    outcome = sync_run(topology, suite, trials=trials)
    obs = build_observation_stats(
        outcome.results,
        edges=[(edge.source, edge.target) for edge in topology.edges()],
    )
    report = build_slow_regression_report(
        outcome,
        obs,
        suite=suite,
        topology=topology,
        topology_version="1.0",
        router_config_id="basefast",
    )
    writer = SlowRegressionWriter(tmp_path)
    written = writer.write(
        run_id="run-1",
        suite_name=suite.name,
        suite_version=suite.version,
        topology_version=report.topology_version,
        router_config_id="basefast",
        evaluator="structured:[]",
        outcome=outcome,
        report=report,
        obs=obs,
    )
    return outcome, report, obs, writer, written


def test_writer_emits_all_six_artifacts(tmp_path) -> None:
    _, report, obs, writer, written = _run_artifacts(tmp_path, trials=4)
    assert set(written) == set(ARTIFACT_NAMES)
    assert all(path.exists() for path in written.values())
    for name in ARTIFACT_NAMES:
        assert tmp_path.joinpath(name).stat().st_size > 0


def test_manifest_records_counts_and_identity(tmp_path) -> None:
    outcome, report, obs, _, written = _run_artifacts(tmp_path, trials=4)
    manifest = json.loads(tmp_path.joinpath("manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-1"
    assert manifest["suite_name"] == "demo"
    assert manifest["topology_version"] == "1.0"
    assert manifest["router_config_id"] == "basefast"
    assert manifest["trial_count"] == 4
    assert manifest["scenario_count"] == 1
    assert manifest["trial_count"] == report.trial_count


def test_traces_jsonl_has_one_line_per_trial(tmp_path) -> None:
    outcome, _, _, _, _ = _run_artifacts(tmp_path, trials=5)
    lines = [
        line
        for line in tmp_path.joinpath("traces.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(lines) == 5 == len(outcome.results)
    first = json.loads(lines[0])
    # serialized Trial keeps its identity and the trace preserves layer record
    assert first["trial"]["id"].endswith("#000")
    assert "execution_status" in first
    assert "latency_ms" in first


def test_stats_files_are_json_arrays(tmp_path) -> None:
    _run_artifacts(tmp_path, trials=6)
    node_stats = json.loads(tmp_path.joinpath("node_stats.json").read_text(encoding="utf-8"))
    edge_stats = json.loads(tmp_path.joinpath("edge_stats.json").read_text(encoding="utf-8"))
    assert isinstance(node_stats, list) and node_stats
    assert isinstance(edge_stats, list) and edge_stats
    assert {item["tool"] for item in node_stats} >= {"db", "policy_check", "summarizer"}


def test_report_json_round_trips_through_serializer(tmp_path) -> None:
    _, report, _, _, _ = _run_artifacts(tmp_path)
    report_json = json.loads(tmp_path.joinpath("report.json").read_text(encoding="utf-8"))
    assert report_json["trial_count"] == report.trial_count
    assert report_json["business_success"] == report.business_success