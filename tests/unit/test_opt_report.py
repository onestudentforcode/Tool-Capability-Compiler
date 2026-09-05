import json

import pytest

from capability_runtime import (
    LayerRegistry,
    OptimizationReport,
    OptimizationRound,
    Topology,
    TopologyBuilder,
    TopologyPatch,
    ToolRegistry,
    build_report,
    initial_version,
    tool,
)


@tool(layer="L1", workers="all", capabilities={"cap.a"})
async def tool_a() -> None:
    pass


@tool(layer="L2", workers="all", capabilities={"cap.b"})
async def tool_b() -> None:
    pass


@tool(layer="L2", workers="all", capabilities={"cap.c"})
async def tool_c() -> None:
    pass


def _make_topology() -> Topology:
    layers = LayerRegistry()
    layers.register("L1", 0)
    layers.register("L2", 1)
    tools = ToolRegistry()
    tools.register(tool_a)
    tools.register(tool_b)
    tools.register(tool_c)
    return TopologyBuilder(layers, tools).build()


def test_optimization_report_basic_properties() -> None:
    topo = _make_topology()
    start = initial_version(topo, version="v1")
    end = initial_version(topo, version="v2")
    report = build_report(start=start, end=end)
    assert isinstance(report, OptimizationReport)
    assert report.start_version == "v1"
    assert report.end_version == "v2"
    assert report.declared_edges == len(topo.edges())
    assert report.active_edges_before == report.active_edges_after
    assert report.edges_removed == 0
    assert report.nodes_removed == 0
    assert report.success
    assert report.total_rounds == 0
    assert report.rounds_passed == 0
    assert report.rounds_rejected == 0


def test_optimization_report_with_rounds() -> None:
    topo = _make_topology()
    start = initial_version(topo, version="v1")
    patch = TopologyPatch(disabled_edges=("tool_a->tool_b",))
    end_active = TopologyPatch(disabled_edges=("tool_a->tool_b",))
    from capability_runtime.topology.patch import apply_patch
    end_topo = apply_patch(topo, patch)
    from capability_runtime.topology.version import TopologyVersion
    end = TopologyVersion(
        version="v2",
        declared=topo,
        active=end_topo,
        patch=patch,
    )
    round_obj = OptimizationRound(
        round_id=1,
        candidates_proposed=1,
        candidates_applied=1,
        candidates_rejected=0,
        patch=patch,
        fast_passed=True,
        slow_passed=True,
        diversity_passed=True,
    )
    report = build_report(start=start, end=end, rounds=(round_obj,))
    assert report.total_rounds == 1
    assert report.rounds_passed == 1
    assert report.edges_removed == 1
    assert report.success


def test_optimization_round_passed_property() -> None:
    patch = TopologyPatch()
    passed = OptimizationRound(
        round_id=1, candidates_proposed=1, candidates_applied=1,
        candidates_rejected=0, patch=patch,
    )
    assert passed.passed
    failed_fast = OptimizationRound(
        round_id=2, candidates_proposed=1, candidates_applied=0,
        candidates_rejected=1, patch=patch, fast_passed=False,
    )
    assert not failed_fast.passed


def test_optimization_report_summary_text() -> None:
    topo = _make_topology()
    start = initial_version(topo, version="v1")
    end = initial_version(topo, version="v2")
    report = build_report(start=start, end=end)
    text = report.summary_text()
    assert "v1 -> v2" in text
    assert "Edges:" in text
    assert "Nodes:" in text
    assert "Rounds:" in text


def test_optimization_report_to_json() -> None:
    topo = _make_topology()
    start = initial_version(topo, version="v1")
    end = initial_version(topo, version="v2")
    report = build_report(start=start, end=end)
    data = report.to_json()
    assert data["start_version"] == "v1"
    assert data["end_version"] == "v2"
    assert data["edges_removed"] == 0
    assert data["success"] is True
    assert "started_at" in data
    assert "finished_at" in data
    # Must be serializable
    json.dumps(data)


def test_optimization_report_negative_removal_is_not_success() -> None:
    # A report with negative edges_removed means the topology grew — not success.
    report = OptimizationReport(
        start_version="v1",
        end_version="v2",
        declared_edges=5,
        declared_nodes=3,
        active_edges_before=3,
        active_edges_after=5,
        active_nodes_before=3,
        active_nodes_after=3,
    )
    assert not report.success
    assert report.edges_removed == -2


def test_cli_optimize_help() -> None:
    from capability_runtime.cli import build_parser
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["optimize", "--help"])
    assert exc.value.code == 0