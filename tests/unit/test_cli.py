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

# ---- output polish P8: top-level exception backstop ------------------------


def _stderr_one_line(capsys) -> str:
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert len(err.strip().splitlines()) == 1
    return err.strip()


def test_cli_backstop_missing_topology_file(tmp_path, capsys) -> None:
    code = main(
        [
            "regression",
            "fast",
            "--topology",
            str(tmp_path / "absent.json"),
            "--scenario",
            str(tmp_path / "also_absent.json"),
        ]
    )
    assert code == 2
    assert _stderr_one_line(capsys).startswith("error: Cannot read topology")


def test_cli_backstop_malformed_topology_json(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"layers": oops', encoding="utf-8")
    scen = tmp_path / "scen.json"
    scen.write_text(json.dumps(SCENARIOS_ONE), encoding="utf-8")
    code = main(
        ["regression", "fast", "--topology", str(bad), "--scenario", str(scen)]
    )
    assert code == 2
    assert "Invalid topology JSON" in _stderr_one_line(capsys)


def test_cli_backstop_select_missing_topology(tmp_path, capsys) -> None:
    ranking = tmp_path / "ranking.json"
    ranking.write_text("{}", encoding="utf-8")
    code = main(
        [
            "select",
            "--topology",
            str(tmp_path / "absent.json"),
            "--ranking",
            str(ranking),
        ]
    )
    assert code == 2
    assert _stderr_one_line(capsys).startswith("error: Cannot read topology")


def test_cli_backstop_seeds_export_missing_fast_report(
    fixtures, tmp_path, capsys
) -> None:
    topo, scen, _ = fixtures
    code = main(
        [
            "seeds",
            "export",
            "--topology",
            str(topo),
            "--scenario",
            str(scen),
            "--fast-report",
            str(tmp_path / "absent_report.json"),
            "--out",
            str(tmp_path / "seeds.json"),
        ]
    )
    assert code == 2
    assert _stderr_one_line(capsys)


def test_cli_backstop_malformed_router_config_json(
    fixtures, tmp_path, capsys
) -> None:
    topo, scen, _ = fixtures
    router_cfg = tmp_path / "router.json"
    router_cfg.write_text("{not json", encoding="utf-8")
    code = main(
        [
            "regression",
            "slow",
            "--topology",
            str(topo),
            "--scenario",
            str(scen),
            "--router-config",
            str(router_cfg),
        ]
    )
    assert code == 2
    assert _stderr_one_line(capsys)


def test_cli_backstop_keeps_local_handler_prefix(tmp_path, capsys) -> None:
    # rank's own handler still fires (context-rich message), not the backstop
    code = main(["rank", "--slow-report", str(tmp_path / "absent_run")])
    assert code == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("cannot rank run:")
