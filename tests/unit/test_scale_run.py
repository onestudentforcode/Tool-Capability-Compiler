"""Battlefield Hardening Batch E: the scale run end to end (quick mode).

Drives ``examples/slow_refund/run_scale.py`` at reduced size and asserts the
full chain: trials -> artifacts -> evidence linkage -> candidate summary
(battlefield-hardening §6).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[2]
_DEMO = _PROJECT / "examples" / "slow_refund"
sys.path.insert(0, str(_DEMO))

import run_scale  # noqa: E402


def test_scale_run_quick_produces_artifacts_and_candidates(tmp_path, capsys) -> None:
    code = run_scale.main(
        ["--scenarios", "10", "--trials", "2", "--out-dir", str(tmp_path)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Scale run: 10 scenarios x 2 trials" in out
    assert "Optimization linkage" in out

    run_dirs = list(tmp_path.iterdir())
    assert len(run_dirs) == 1
    files = {path.name for path in run_dirs[0].iterdir()}
    assert {
        "manifest.json",
        "report.json",
        "traces.jsonl",
        "node_stats.json",
        "edge_stats.json",
        "route_stats.json",
        "optimization_summary.json",
    } <= files

    summary = json.loads(
        run_dirs[0].joinpath("optimization_summary.json").read_text(encoding="utf-8")
    )
    assert summary["run"]["total_trials"] == 20
    assert summary["run"]["elapsed_seconds"] > 0
    # the demo topology has one provider per capability, so protection fires:
    # every edge touching a unique provider is a PROTECTED candidate
    assert summary["candidates_by_status"].get("protected", 0) >= 1
    assert summary["evidence"]["protected_nodes"]
    # observation only — the summary must not claim any pruning decision
    assert "accepted" not in summary["candidates_by_status"]
    assert "rejected" not in summary["candidates_by_status"]


def test_scale_suite_is_deterministic() -> None:
    first_suite, first_seeds = run_scale.build_scale_suite(25)
    second_suite, second_seeds = run_scale.build_scale_suite(25)
    assert [s.id for s in first_suite.scenarios] == [s.id for s in second_suite.scenarios]
    assert set(first_seeds) == set(second_seeds)
    for key in sorted(first_seeds):
        assert first_seeds[key].layers == second_seeds[key].layers
    # every scenario carries a fixture variant; every 25th is a sentinel
    variants = {s.metadata.get("fixture") for s in first_suite.scenarios}
    assert variants <= {
        "eligible", "ineligible", "high_risk", "not_found", "erp_down"
    }
    sentinels = [s.id for s in first_suite.scenarios if s.metadata.get("sentinel")]
    assert sentinels == ["basic_000"]
