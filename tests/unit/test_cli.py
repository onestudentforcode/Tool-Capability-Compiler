from __future__ import annotations

import json

import pytest

from capability_runtime.cli import main


TOPO = {
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

SCENARIOS_OK = {
    "version": "1.0",
    "name": "svc",
    "scenarios": [
        {
            "id": "ok",
            "query": "check refund",
            "expected_capabilities": ["order.read"],
        },
        {
            "id": "missing",
            "query": "send invoice",
            "expected_capabilities": ["invoice.send"],
        },
    ],
}

SCENARIOS_ONE = {
    "version": "1.0",
    "name": "svc",
    "scenarios": [
        {
            "id": "good",
            "query": "check refund",
            "expected_capabilities": ["order.read"],
        }
    ],
}


@pytest.fixture
def fixtures(tmp_path):
    topo_path = tmp_path / "topo.json"
    topo_path.write_text(json.dumps(TOPO), encoding="utf-8")
    scen_path = tmp_path / "scen.json"
    scen_path.write_text(json.dumps(SCENARIOS_OK), encoding="utf-8")
    scen_one = tmp_path / "scen_one.json"
    scen_one.write_text(json.dumps(SCENARIOS_ONE), encoding="utf-8")
    return topo_path, scen_path, scen_one


def test_cli_gold_mode_prints_summary(fixtures, capsys) -> None:
    topo, scen, _ = fixtures
    code = main(
        [
            "regression",
            "fast",
            "--topology",
            str(topo),
            "--scenario",
            str(scen),
            "--topology-version",
            "v1",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Fast Regression" in out
    assert "Topology: v1" in out
    assert "Total:      2" in out
    assert "Covered:    1" in out
    assert "Uncovered:  1" in out
    assert "Coverage: 50.00%" in out
    assert "invoice.send" in out


def test_cli_save_baseline_and_diff(fixtures, capsys, tmp_path) -> None:
    topo, scen, scen_one = fixtures
    baseline_path = tmp_path / "baseline.json"
    assert (
        main(
            [
                "regression",
                "fast",
                "--topology",
                str(topo),
                "--scenario",
                str(scen),
                "--save-baseline",
                str(baseline_path),
            ]
        )
        == 0
    )
    assert baseline_path.exists()

    # current report (2 scenarios) vs baseline (2 scenarios): identical
    capsys.readouterr()
    out = ""
    assert (
        main(
            [
                "regression",
                "fast",
                "--topology",
                str(topo),
                "--scenario",
                str(scen),
                "--baseline",
                str(baseline_path),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "Newly uncovered: 0" in out


def test_cli_fail_on_regression(fixtures, capsys) -> None:
    topo, scen, _ = fixtures
    code = main(
        [
            "regression",
            "fast",
            "--topology",
            str(topo),
            "--scenario",
            str(scen),
            "--fail-on-regression",
        ]
    )
    assert code == 1


def test_cli_returns_zero_when_fully_covered(fixtures, capsys) -> None:
    topo, _, scen_one = fixtures
    code = main(
        [
            "regression",
            "fast",
            "--topology",
            str(topo),
            "--scenario",
            str(scen_one),
            "--fail-on-regression",
        ]
    )
    assert code == 0


def test_cli_requires_topology_argument(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["regression", "fast", "--scenario", "x.json"])