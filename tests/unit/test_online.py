"""Phase 6 online routing: catalog, selection, balancing, fallback, telemetry.

All offline. Runtime cases drive the real sandbox refund topology, including
injected read-layer failures (not_found) and partial failure (erp_down).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from capability_runtime import (
    EvidenceAggregator,
    OnlineConfig,
    OnlineRequest,
    OnlineRuntime,
    OnlineStatus,
    OnlineTelemetry,
    RouteCatalogError,
    RouteSelectionError,
    RoundRobinBalancer,
    build_catalog,
    build_fallback_chain,
    build_observation_stats,
    candidate_groups,
    online_results_to_trials,
    parse_canonical,
    record_of,
    resolve_tier_order,
)
from capability_runtime.online.catalog import RouteEntry, RouteSegment

_PROJECT = Path(__file__).resolve().parents[2]
_DEMO = _PROJECT / "examples" / "slow_refund"
sys.path.insert(0, str(_DEMO))

import refund  # noqa: E402
import store  # noqa: E402


# ---- canonical parsing ---------------------------------------------------------


def test_parse_canonical_rebuilds_segments() -> None:
    segments = parse_canonical("read:[order_db,rag]\nanalyze:[policy_check]")
    assert segments == (
        RouteSegment("read", ("order_db", "rag")),
        RouteSegment("analyze", ("policy_check",)),
    )


def test_parse_canonical_rejects_malformed_forms() -> None:
    for bad in ("read", "read:[]", "read:[a,]", "no-tools-here", ""):
        with pytest.raises(RouteCatalogError):
            parse_canonical(bad)


# ---- catalog --------------------------------------------------------------------


def _ranking_payload(
    *, topology_version: str = "v0.4.0", routes=None
) -> dict:
    routes = routes or [
        {
            "route_id": "A",
            "canonical": "read:[erp]",
            "tiers": ["fast"],
            "categories": ["refund"],
        },
        {
            "route_id": "B",
            "canonical": (
                "read:[order_db]\nanalyze:[policy_check]\naction:[refund_api]"
            ),
            "tiers": ["quality"],
            "categories": ["refund"],
        },
    ]
    profiles = [
        {
            "route_id": item["route_id"],
            "canonical": item["canonical"],
            "categories": item["categories"],
            "trial_count": 25,
            "business_success_rate": 0.95,
            "success_confidence_interval": [0.8, 0.99],
            "latency_median": 100.0,
            "cost_mean": 0.01,
            "quality_mean": 0.9,
        }
        for item in routes
    ]
    return {
        "topology_version": topology_version,
        "router_config_id": "basefast-demo",
        "profiles": profiles,
        "eligibility": [
            {"route_id": item["route_id"], "status": "ranked"} for item in routes
        ],
        "pareto": {"frontier": [item["route_id"] for item in routes]},
        "tier_assignments": [
            {"route_id": item["route_id"], "tiers": item["tiers"]}
            for item in routes
        ],
    }


@pytest.fixture
def sandbox_catalog():
    store.STORE.reset("eligible")
    topology, _ = refund.build_topology(topology_version="v0.4.0")
    return build_catalog(
        topology, _ranking_payload(), topology_version="v0.4.0"
    )


def test_build_catalog_gates_versions(sandbox_catalog) -> None:
    topology = sandbox_catalog.topology
    with pytest.raises(RouteCatalogError, match="version mismatch"):
        build_catalog(
            topology, _ranking_payload(topology_version="v9"), topology_version="v0.4.0"
        )


def test_build_catalog_rejects_unknown_tool(sandbox_catalog) -> None:
    payload = _ranking_payload(
        routes=[{"route_id": "X", "canonical": "read:[ghost]", "tiers": [],
                 "categories": []}]
    )
    with pytest.raises(RouteCatalogError, match="unknown tool 'ghost'"):
        build_catalog(
            sandbox_catalog.topology, payload, topology_version="v0.4.0"
        )


def test_build_catalog_rejects_wrong_layer(sandbox_catalog) -> None:
    payload = _ranking_payload(
        routes=[{"route_id": "X", "canonical": "action:[order_db]", "tiers": [],
                 "categories": []}]
    )
    with pytest.raises(RouteCatalogError, match="not in layer"):
        build_catalog(
            sandbox_catalog.topology, payload, topology_version="v0.4.0"
        )


def test_catalog_filters_by_category_and_tier(sandbox_catalog) -> None:
    fast = sandbox_catalog.candidates(category="refund", tier="fast")
    assert [entry.route_id for entry in fast] == ["A"]
    quality = sandbox_catalog.candidates(category="refund", tier="quality")
    assert [entry.route_id for entry in quality] == ["B"]
    assert sandbox_catalog.candidates(category="invoice") == ()


# ---- selection / balancer / fallback ----------------------------------------------


def test_config_and_request_validation() -> None:
    with pytest.raises(RouteSelectionError):
        OnlineConfig(tier_priority=("fast", "fast"))
    with pytest.raises(RouteSelectionError):
        OnlineConfig(max_fallbacks=-1)
    with pytest.raises(RouteSelectionError):
        OnlineRequest(query=" ", tier="turbo")


def test_tier_order_request_leads(sandbox_catalog) -> None:
    request = OnlineRequest(query="q", tier="quality")
    config = OnlineConfig()
    assert resolve_tier_order(request, config) == (
        "quality", "fast", "balanced",
    )


def test_candidate_groups_include_unassigned_last(sandbox_catalog) -> None:
    request = OnlineRequest(query="q", category="refund")
    groups = candidate_groups(sandbox_catalog, request, OnlineConfig())
    tiers = [tier for tier, _entries in groups]
    assert tiers == ["fast", "quality"]


def test_candidate_groups_fail_closed_on_unknown_category(sandbox_catalog) -> None:
    request = OnlineRequest(query="q", category="invoice")
    with pytest.raises(RouteSelectionError, match="invoice"):
        candidate_groups(sandbox_catalog, request, OnlineConfig())


def test_global_fallback_can_be_enabled(sandbox_catalog) -> None:
    request = OnlineRequest(query="q", category="invoice")
    groups = candidate_groups(
        sandbox_catalog, request, OnlineConfig(allow_global_fallback=True)
    )
    assert groups  # unfiltered groups appended


def test_balancer_rotates_deterministically(sandbox_catalog) -> None:
    entries = sandbox_catalog.entries
    balancer = RoundRobinBalancer()
    picks = [balancer.pick(entries).route_id for _ in range(4)]
    assert picks[0] == picks[2] and picks[1] == picks[3] and picks[0] != picks[1]
    with pytest.raises(RouteSelectionError):
        RoundRobinBalancer().pick(())


def test_fallback_chain_dedupes_and_truncates(sandbox_catalog) -> None:
    entries = {entry.route_id: entry for entry in sandbox_catalog.entries}
    groups = candidate_groups(
        sandbox_catalog,
        OnlineRequest(query="q", category="refund"),
        OnlineConfig(),
    )
    chain = build_fallback_chain(groups, entries["A"], max_fallbacks=1)
    assert [entry.route_id for entry in chain] == ["B"]
    assert build_fallback_chain(groups, entries["A"], max_fallbacks=0) == ()


# ---- runtime: route-following, fallback, metering ----------------------------------


def _serve(catalog, request, *, max_fallbacks: int = 1):
    runtime = OnlineRuntime(
        catalog=catalog, config=OnlineConfig(max_fallbacks=max_fallbacks)
    )
    store.STORE.reset("eligible")
    return asyncio.run(runtime.serve(request))


def test_serve_follows_route_and_bills_execution(sandbox_catalog) -> None:
    result = _serve(
        sandbox_catalog,
        OnlineRequest(query="refund it", category="refund", tier="quality"),
    )
    assert result.status is OnlineStatus.SERVED
    assert result.selected_route_id == "B"
    assert result.fallback_depth == 0
    assert result.cost == pytest.approx(0.012)  # order_db + policy + refund_api
    assert result.state is not None
    assert result.trace is not None and len(result.trace.layers) == 3
    # route-following: the decision names the route, never explores
    reasons = [
        layer.routing_decision.reason for layer in result.trace.layers
    ]
    assert all(reason.startswith("online:route-following:") for reason in reasons)


def test_serve_no_candidate_is_explainable(sandbox_catalog) -> None:
    result = _serve(
        sandbox_catalog, OnlineRequest(query="q", category="invoice")
    )
    assert result.status is OnlineStatus.NO_CANDIDATE
    assert result.selected_route_id is None
    assert "invoice" in result.selection_reason


def test_serve_falls_back_on_injected_read_failure(sandbox_catalog) -> None:
    runtime = OnlineRuntime(
        catalog=sandbox_catalog,
        config=OnlineConfig(max_fallbacks=1),
    )
    store.STORE.reset("erp_down")
    result = asyncio.run(
        runtime.serve(OnlineRequest(query="refund", category="refund", tier="fast"))
    )
    store.STORE.reset("eligible")
    assert result.status is OnlineStatus.SERVED
    # initial fast route (erp-only read) failed; degraded to the quality route
    assert result.fallback_depth == 1
    step = result.fallback_chain[0]
    assert (step.from_route_id, step.to_route_id) == ("A", "B")
    assert "layer" in step.cause
    assert result.selected_route_id == "B"
    # the failed attempt was billed too (erp cost included)
    assert result.cost == pytest.approx(0.003 + 0.012)


def test_serve_exhausts_chain_replayably(sandbox_catalog) -> None:
    runtime = OnlineRuntime(
        catalog=sandbox_catalog,
        config=OnlineConfig(max_fallbacks=1),
    )
    store.STORE.reset("not_found")
    result = asyncio.run(
        runtime.serve(OnlineRequest(query="refund", category="refund"))
    )
    store.STORE.reset("eligible")
    assert result.status is OnlineStatus.ROUTE_FAILED
    assert result.fallback_depth == 1
    assert result.state is None  # nothing served
    assert "exhausted fallback chain" in result.selection_reason


def test_runtime_never_mutates_catalog(sandbox_catalog) -> None:
    before = tuple(sandbox_catalog.entries)
    _serve(
        sandbox_catalog,
        OnlineRequest(query="refund", category="refund", tier="quality"),
    )
    _serve(
        sandbox_catalog, OnlineRequest(query="q", category="invoice")
    )
    assert sandbox_catalog.entries == before


# ---- telemetry + loop closure --------------------------------------------------------


def _collect(catalog) -> list:
    runtime = OnlineRuntime(
        catalog=catalog, config=OnlineConfig(max_fallbacks=1)
    )
    results = []
    store.STORE.reset("eligible")
    results.append(
        asyncio.run(
            runtime.serve(
                OnlineRequest(query="a", category="refund", tier="quality")
            )
        )
    )
    store.STORE.reset("not_found")
    results.append(
        asyncio.run(
            runtime.serve(OnlineRequest(query="b", category="refund", tier="fast"))
        )
    )
    store.STORE.reset("eligible")
    return results


def test_telemetry_records_and_aggregates(sandbox_catalog, tmp_path) -> None:
    results = _collect(sandbox_catalog)
    telemetry = OnlineTelemetry()
    for result in results:
        telemetry.append(result)

    records = telemetry.records
    assert [record.status for record in records] == ["served", "route_failed"]
    assert records[0].selected_route_id == "B"
    assert records[0].cost == pytest.approx(0.012)

    path = telemetry.write_jsonl(tmp_path / "t.jsonl")
    loaded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert loaded[0]["status"] == "served"
    assert loaded[1]["fallback_depth"] == 1

    stats = {stat.route_id: stat for stat in telemetry.usage_stats()}
    # route B was attempted twice: once served, once as the failed fallback
    assert stats["B"].served_count == 1
    assert stats["B"].failed_count == 1
    assert stats["B"].usage_count == 2


def test_online_results_feed_the_offline_loop(sandbox_catalog) -> None:
    results = _collect(sandbox_catalog)
    trials = online_results_to_trials(results)
    assert [t.execution_status.value for t in trials] == [
        "completed", "layer_error",
    ]
    assert all(t.evaluation is None for t in trials)

    topology = sandbox_catalog.topology
    edges = [(edge.source, edge.target) for edge in topology.edges()]
    obs = build_observation_stats(trials, edges=edges)
    evidence = EvidenceAggregator().build(
        report=obs, results=trials, edges=edges
    )
    assert evidence.node_evidence  # online evidence became offline input
    assert evidence.edge_evidence


def test_record_of_maps_result_fields(sandbox_catalog) -> None:
    results = _collect(sandbox_catalog)
    record = record_of(results[0])
    assert record.category == "refund"
    assert record.tier_preference == "quality"
    assert record.status == "served"


# ---- CLI select (dry run) ---------------------------------------------------------------


def _write(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_cli_select_dry_run(tmp_path, capsys) -> None:
    from capability_runtime.cli import main

    payload = _ranking_payload(topology_version="declared")
    payload["profiles"][0]["canonical"] = "read:[db]\nanalyze:[policy_check]"
    payload["profiles"][1]["canonical"] = "read:[db]\nanalyze:[summarizer]"
    ranking = _write(tmp_path, "ranking.json", payload)
    topology = _PROJECT / "examples" / "topology" / "refund.json"

    code = main(
        [
            "select", "--topology", str(topology), "--ranking", str(ranking),
            "--category", "refund", "--tier", "fast", "--format", "text",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Would select" in out
    assert "dry run; no tool is executed" in out


def test_cli_select_rejects_version_mismatch(tmp_path, capsys) -> None:
    from capability_runtime.cli import main

    payload = _ranking_payload(topology_version="v1")
    payload["profiles"][0]["canonical"] = "read:[db]\nanalyze:[policy_check]"
    payload["profiles"][1]["canonical"] = "read:[db]\nanalyze:[summarizer]"
    ranking = _write(tmp_path, "ranking.json", payload)
    topology = _PROJECT / "examples" / "topology" / "refund.json"

    code = main(
        [
            "select", "--topology", str(topology), "--ranking", str(ranking),
            "--topology-version", "v2",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "version mismatch" in captured.err
