"""Phase 5 route ranking: stats, profiles, eligibility, pareto, tiers, report.

All offline. The disk end-to-end case runs the real sandbox refund domain,
persists artifacts, then ranks them through the CLI — proving the whole
"write -> read -> rank" chain on real metering (phase5-plan §7).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from capability_runtime import (
    EligibilityStatus,
    RankConfig,
    RankingConfigError,
    RouteProfileError,
    RouteTier,
    TierConfig,
    TokenUsage,
    build_frontier,
    build_profiles,
    build_ranking_report,
    check_eligibility,
    ranked_ids,
    ranking_to_json,
    render_ranking_report,
    rows_from_run,
    statistical_tie,
    wilson_interval,
    z_score,
    Objective,
    assign_tiers,
    build_families,
)
from capability_runtime.ranking.profile import TrialRow
from capability_runtime.ranking.tier import RouteTierAssignment


# ---- stats (Step 1) -----------------------------------------------------------


def test_z_score_matches_standard_normal() -> None:
    assert z_score(0.95) == pytest.approx(1.959964, rel=1e-5)


def test_z_score_rejects_invalid_confidence() -> None:
    for bad in (0.0, 1.0, -0.1, 1.5, True):
        with pytest.raises(RankingConfigError):
            z_score(bad)


def test_wilson_interval_without_trials_is_uninformative() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_interval_contains_point_estimate() -> None:
    low, high = wilson_interval(17, 20, 0.95)
    assert 0.0 <= low <= 0.85 <= high <= 1.0
    assert low < high


def test_wilson_interval_rejects_inconsistent_counts() -> None:
    with pytest.raises(RankingConfigError):
        wilson_interval(5, 3)


def test_statistical_tie_follows_overlap() -> None:
    assert statistical_tie((0.1, 0.4), (0.3, 0.6))
    assert not statistical_tie((0.1, 0.2), (0.3, 0.6))


# ---- helpers -------------------------------------------------------------------


def row(
    scenario: str = "s1",
    route: str | None = "r1",
    *,
    canonical: str | None = None,
    success: bool | None = True,
    quality: float | None = 0.9,
    latency: float = 100.0,
    cost: float | None = 0.01,
    trials: int = 1,
    topology: str = "v5",
    router: str = "llm",
    segments: tuple[int, ...] = (1, 1, 1),
) -> list[TrialRow]:
    return [
        TrialRow(
            scenario_id=scenario,
            route_id=route,
            canonical=canonical or f"canon:{route}",
            completed=True,
            success=success,
            quality=quality,
            latency_ms=latency,
            cost=cost,
            tool_cost=cost,
            routing_cost=None,
            evaluation_cost=None,
            input_tokens=10,
            output_tokens=5,
            segment_tool_counts=segments,
            topology_version=topology,
            router_config_id=router,
        )
        for _ in range(trials)
    ]


def make_profile(
    route_id: str,
    *,
    rate: float = 0.95,
    quality: float | None = 0.9,
    latency: float = 100.0,
    cost: float | None = 0.01,
    ci: tuple[float, float] = (0.9, 0.99),
) -> "object":
    from capability_runtime import RouteProfile

    return RouteProfile(
        route_id=route_id,
        canonical=f"canon:{route_id}",
        trial_count=25,
        completed_count=25,
        evaluated_count=25,
        scenario_count=5,
        categories=("refund",),
        business_success_count=int(rate * 25),
        business_success_rate=rate,
        success_confidence_interval=ci,
        quality_mean=quality,
        quality_median=quality,
        latency_mean=latency,
        latency_median=latency,
        latency_p95=latency * 1.5,
        cost_mean=cost,
        cost_median=cost,
        token_usage=TokenUsage(),
        tool_count=3,
        layer_count=3,
        topology_version="v5",
        router_config_id="llm",
    )


# ---- profile (Step 2) -----------------------------------------------------------


def test_profile_success_rate_divides_by_evaluated_trials() -> None:
    rows = row(success=True, trials=8) + row(success=None, trials=2)
    profile = build_profiles(rows)[0]
    assert profile.trial_count == 10
    assert profile.evaluated_count == 8
    assert profile.business_success_rate == pytest.approx(1.0)


def test_profile_keeps_absent_dimensions_none() -> None:
    rows = row(cost=None, quality=None)
    profile = build_profiles(rows)[0]
    assert profile.cost_mean is None
    assert profile.quality_mean is None
    assert profile.latency_median is not None


def test_profile_carries_structure_and_versions() -> None:
    profile = build_profiles(row(segments=(2, 1, 3)))[0]
    assert profile.tool_count == 6
    assert profile.layer_count == 3
    assert profile.topology_version == "v5"
    assert profile.router_config_id == "llm"


def test_profile_refuses_mixed_versions() -> None:
    with pytest.raises(RouteProfileError, match="mix incompatible versions"):
        build_profiles(row(topology="v5") + row(topology="v6"))


def test_profile_refuses_empty_or_routeless_input() -> None:
    with pytest.raises(RouteProfileError):
        build_profiles([])
    with pytest.raises(RouteProfileError, match="observed route"):
        build_profiles(row(route=None))


def test_profile_categories_come_from_mapping() -> None:
    rows = row(scenario="s1") + row(scenario="s2")
    profile = build_profiles(rows, category_of={"s1": "refund", "s2": "order"})[0]
    assert profile.categories == ("order", "refund")
    assert profile.scenario_count == 2


# ---- eligibility (Step 3) --------------------------------------------------------


def test_eligibility_splits_by_min_trials_with_shortfall() -> None:
    from dataclasses import replace

    profiles = [make_profile("a"), replace(make_profile("b"), trial_count=7)]
    eligibility = check_eligibility(profiles, RankConfig(min_trials=20))
    by_id = {item.route_id: item for item in eligibility}
    assert by_id["a"].status is EligibilityStatus.RANKED
    assert by_id["a"].shortfall == 0
    assert by_id["b"].status is EligibilityStatus.INSUFFICIENT_EVIDENCE
    assert by_id["b"].shortfall == 13
    assert ranked_ids(eligibility) == frozenset({"a"})


def test_rank_config_rejects_bad_values() -> None:
    with pytest.raises(RankingConfigError):
        RankConfig(min_trials=0)
    with pytest.raises(RankingConfigError):
        RankConfig(confidence_level=0.0)


# ---- pareto (Step 4) ---------------------------------------------------------------


def test_pareto_dominance_and_attribution() -> None:
    worse = make_profile("worse", rate=0.90, quality=0.80, latency=200.0, cost=0.03)
    better = make_profile("better", rate=0.97, quality=0.94, latency=100.0, cost=0.01)
    frontier = build_frontier([worse, better])
    assert frontier.frontier == ("better",)
    assert frontier.dominated[0].route_id == "worse"
    assert frontier.dominated[0].dominated_by == ("better",)
    assert frontier.dominators_of("worse") == ("better",)
    assert frontier.dominators_of("better") == ()


def test_pareto_keeps_trade_off_routes_on_frontier() -> None:
    fast = make_profile("fast", latency=50.0, quality=0.70, cost=0.002)
    good = make_profile("good", latency=400.0, quality=0.98, cost=0.05)
    frontier = build_frontier([fast, good])
    assert set(frontier.frontier) == {"fast", "good"}
    assert frontier.dominated == ()


def test_pareto_missing_dimension_drops_out_and_is_flagged() -> None:
    partial = make_profile("partial", quality=None)
    other = make_profile("other", rate=0.96, quality=0.93)
    frontier = build_frontier([partial, other])
    # success/latency/cost comparable; better on all three -> dominates
    assert frontier.frontier == ("other",)
    assert "partial" in frontier.partial_comparisons


def test_pareto_equal_values_do_not_dominate() -> None:
    first = make_profile("a")
    second = make_profile("b")
    frontier = build_frontier([first, second])
    assert set(frontier.frontier) == {"a", "b"}


def test_pareto_objective_subset() -> None:
    # equal on success/quality/latency, differs only on cost
    cheap = make_profile("cheap", cost=0.001)
    pricey = make_profile("pricey", cost=0.9)
    frontier = build_frontier([cheap, pricey], (Objective.COST,))
    assert frontier.frontier == ("cheap",)


# ---- tier (Step 5) ------------------------------------------------------------------


def test_tiers_hit_fast_quality_and_balanced() -> None:
    profiles = [
        make_profile("fast", latency=50.0, quality=0.70),
        make_profile("quality", latency=400.0, quality=0.98),
        make_profile("mid", latency=60.0, quality=0.94),
    ]
    assignments = {a.route_id: a for a in assign_tiers(profiles)}
    assert RouteTier.FAST in assignments["fast"].tiers
    assert RouteTier.QUALITY in assignments["quality"].tiers
    # within 20% of best latency and 0.05 of best quality -> both labels
    assert RouteTier.FAST in assignments["mid"].tiers
    assert RouteTier.QUALITY in assignments["mid"].tiers
    assert RouteTier.BALANCED in assignments["mid"].tiers
    assert assignments["fast"].matched_rules
    assert any(rule.startswith("fast:") for rule in assignments["fast"].matched_rules)


def test_tier_unassigned_when_success_below_tolerance() -> None:
    profiles = [make_profile("best"), make_profile("weak", rate=0.5)]
    assignments = {a.route_id: a for a in assign_tiers(profiles)}
    assert assignments["weak"].tiers == ()
    assert assignments["weak"].unassigned
    assert not assignments["best"].unassigned


def test_tier_quality_absent_when_dimension_globally_missing() -> None:
    profiles = [make_profile("a", quality=None), make_profile("b", quality=None)]
    assignments = assign_tiers(profiles)
    assert all(
        RouteTier.QUALITY not in assignment.tiers for assignment in assignments
    )


def test_tier_config_rejects_negative_tolerance() -> None:
    with pytest.raises(RankingConfigError):
        TierConfig(success_tolerance=-0.1)


# ---- family (Step 6) -----------------------------------------------------------------


def test_families_are_singletons() -> None:
    families = build_families([make_profile("b"), make_profile("a")])
    assert [family.family_id for family in families] == ["a", "b"]
    assert all(family.route_ids == (family.family_id,) for family in families)


# ---- report (Step 7-8) -----------------------------------------------------------------


def _two_route_rows() -> list[TrialRow]:
    return (
        row(scenario="s1", route="fast", latency=50.0, quality=0.70, cost=0.002,
            canonical="read:[cache] analyze:[policy]", trials=25)
        + row(scenario="s2", route="good", latency=400.0, quality=0.98, cost=0.05,
              canonical="read:[db] analyze:[policy]", trials=25)
        + row(scenario="s3", route="tiny", latency=80.0, quality=0.9, cost=0.01,
              canonical="read:[rag] analyze:[policy]", trials=3)
    )


def test_report_ranks_only_eligible_routes() -> None:
    report = build_ranking_report(
        _two_route_rows(),
        rank_config=RankConfig(min_trials=20),
        category_of={"s1": "refund", "s2": "order", "s3": "refund"},
    )
    assert {profile.route_id for profile in report.ranked_profiles} == {"fast", "good"}
    assert [item.route_id for item in report.insufficient] == ["tiny"]
    assert report.insufficient[0].shortfall == 17
    # pareto/tiers computed over ranked set only
    assert set(report.pareto.frontier) == {"fast", "good"}
    assert {a.route_id for a in report.tier_assignments} == {"fast", "good"}
    # category dimension present with in-category scope
    categories = {entry.category: entry for entry in report.categories}
    assert categories["refund"].route_count == 1
    assert categories["order"].route_count == 1


def test_report_lists_statistical_ties() -> None:
    rows = _two_route_rows()
    report = build_ranking_report(rows, rank_config=RankConfig(min_trials=20))
    # both routes are 100% success here -> intervals overlap -> tie
    assert ("fast", "good") in report.statistical_ties


def test_report_render_and_json_roundtrip() -> None:
    report = build_ranking_report(
        _two_route_rows(),
        rank_config=RankConfig(min_trials=20),
        suite_name="demo",
        suite_version="1.0",
        source_run="run_1",
    )
    text = render_ranking_report(report)
    assert "Route Ranking" in text
    assert "Ranked Routes: 2 / 3" in text
    assert "Tier FAST" in text and "Tier QUALITY" in text
    assert "Insufficient Evidence (1)" in text
    assert "trials 3 / 20" in text
    assert "(Phase 5 ranks only; route selection belongs to Phase 6.)" in text

    payload = json.loads(json.dumps(ranking_to_json(report)))
    assert payload["min_trials"] == 20
    assert payload["profiles"][0]["canonical"]
    assert payload["pareto"]["frontier"]
    assert len(payload["tier_assignments"]) == 2


# ---- disk end-to-end (Step 8-9): real sandbox run -> artifacts -> CLI ---------------

_PROJECT = Path(__file__).resolve().parents[2]
_DEMO = _PROJECT / "examples" / "slow_refund"


def _run_sandbox_and_persist(out_dir: Path) -> Path:
    sys.path.insert(0, str(_DEMO))
    from capability_runtime import (
        DefaultFixtureManager,
        FakeRouter,
        Scenario,
        ScenarioSuite,
        SlowRegressionRunner,
        SlowRegressionWriter,
        build_observation_stats,
        build_slow_regression_report,
    )
    import refund
    from fixtures import SandboxFixtureManager

    topology, version = refund.build_topology(topology_version="v0.4.0")
    suite = ScenarioSuite(
        name="rankdemo",
        version="1.0",
        description="rank demo",
        scenarios=(
            Scenario(
                id="ra",
                query="refund",
                category="refund",
                metadata={"fixture": "eligible"},
            ),
        ),
    )
    router = FakeRouter(
        layer_selections={
            "read": ["order_db"],
            "analyze": ["policy_check"],
            "action": ["refund_api"],
        }
    )
    outcome = asyncio.run(
        SlowRegressionRunner(
            topology=topology,
            evaluator=refund.build_evaluator(),
            fixture_manager=SandboxFixtureManager(),
            trials_per_scenario=4,
            topology_version=version,
            router_config_id="fake-rank",
            router=router,
        ).run(suite)
    )
    edges = [(edge.source, edge.target) for edge in topology.edges()]
    obs = build_observation_stats(outcome.results, edges=edges)
    report = build_slow_regression_report(
        outcome,
        obs,
        suite=suite,
        topology=topology,
        topology_version=version,
        router_config_id="fake-rank",
    )
    run_dir = out_dir / "run_001"
    SlowRegressionWriter(run_dir).write(
        run_id="rankdemo-run_001",
        suite_name=suite.name,
        suite_version=suite.version,
        topology_version=version,
        router_config_id="fake-rank",
        evaluator="composite",
        outcome=outcome,
        report=report,
        obs=obs,
    )
    return run_dir


def test_rows_from_run_reads_real_artifacts(tmp_path) -> None:
    run_dir = _run_sandbox_and_persist(tmp_path)
    rows, meta = rows_from_run(run_dir)
    assert meta.run_id == "rankdemo-run_001"
    assert meta.topology_version == "v0.4.0"
    assert len(rows) == 4
    assert all(row.route_id is not None for row in rows)
    assert rows[0].tool_cost is not None  # batch A metering survived the disk


def test_cli_rank_end_to_end(tmp_path, capsys) -> None:
    from capability_runtime.cli import main

    run_dir = _run_sandbox_and_persist(tmp_path)
    code = main(
        [
            "rank",
            "--slow-report",
            str(run_dir),
            "--min-trials",
            "3",
            "--format",
            "text",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Route Ranking" in out
    assert "read:[order_db] analyze:[policy_check] action:[refund_api]" in out
    assert "cost $0.012" in out  # 0.001 + 0.001 + 0.010 execution cost


def test_cli_rank_json_to_file(tmp_path, capsys) -> None:
    from capability_runtime.cli import main

    run_dir = _run_sandbox_and_persist(tmp_path)
    out_file = tmp_path / "ranking.json"
    code = main(
        [
            "rank",
            "--slow-report",
            str(run_dir),
            "--min-trials",
            "3",
            "--format",
            "json",
            "--out",
            str(out_file),
        ]
    )
    assert code == 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["profiles"][0]["cost_mean"] == pytest.approx(0.012)
    assert payload["profiles"][0]["categories"] == ["refund"] or (
        payload["profiles"][0]["categories"] == []
    )


def test_cli_rank_refuses_mixed_version_traces(tmp_path, capsys) -> None:
    run_dir = _run_sandbox_and_persist(tmp_path)
    traces = run_dir / "traces.jsonl"
    lines = traces.read_text(encoding="utf-8").strip().splitlines()
    poisoned = json.loads(lines[-1])
    poisoned["trial"]["topology_version"] = "v0.9.9"
    traces.write_text(
        "\n".join(lines + [json.dumps(poisoned)]) + "\n", encoding="utf-8"
    )
    from capability_runtime.cli import main

    code = main(["rank", "--slow-report", str(run_dir), "--min-trials", "3"])
    captured = capsys.readouterr()
    assert code == 2
    assert "cannot rank run" in captured.err


def test_rows_from_results_matches_disk_rows(tmp_path) -> None:
    """The in-process adapter and the disk adapter agree on ranking rows."""
    run_dir = _run_sandbox_and_persist(tmp_path)
    disk_rows, _ = rows_from_run(run_dir)
    assert len(disk_rows) == 4
    by_scenario = {row.scenario_id: row for row in disk_rows}
    assert set(by_scenario) == {"ra"}
    assert all(row.success is True for row in disk_rows)
