"""Seed bridge (discovery-routing batch A): freeze fast chains into seeds.

The bridge makes fast's *statically feasible* candidate chains
*execution-witnessed*: shortest chain first, each replayed once, first
survivor frozen. The flagship case is the redundant data.read pair —
the shortest candidate picks source_bad (raises at runtime) and the
exporter must fall through to source_good. Also verifies the empty-route
guard (a candidate whose span starts above layer 0 completes without
running anything and must be rejected) and honest failure accounting.
"""

from __future__ import annotations

import asyncio

import pytest

from capability_runtime import Scenario, ScenarioSuite
from capability_runtime.core.errors import SeedExportError
from capability_runtime.evaluation.base import Evaluator, EvaluationResult
from capability_runtime.optimization.artifacts import declared_fingerprint
from capability_runtime.regression.report import (
    FastRegressionRunner,
    fast_report_from_json,
    fast_report_to_json,
)
from capability_runtime.regression.seed_export import (
    ENTRY_FROZEN,
    ENTRY_NO_CANDIDATES,
    ENTRY_REPLAY_FAILED,
    export_seeds,
    read_seeds,
)

from tests.unit import _seed_bridge_tools as bridge


def _suite() -> ScenarioSuite:
    return ScenarioSuite(
        name="seed_bridge",
        version="0.1",
        scenarios=(
            Scenario(id="S1", query="decide",
                     expected_capabilities=("data.read", "task.decide")),
            Scenario(id="S2", query="decide only",
                     expected_capabilities=("task.decide",)),
            Scenario(id="S3", query="magic",
                     expected_capabilities=("magic.cap",)),
        ),
    )


def _report(suite):
    topology = bridge.build_topology()
    report = asyncio.run(FastRegressionRunner().run(suite, topology))
    return topology, report


class _Pass(Evaluator):
    async def evaluate(self, scenario, result, trace):
        return EvaluationResult(success=True, quality_score=1.0)


class _Fail(Evaluator):
    async def evaluate(self, scenario, result, trace):
        return EvaluationResult(success=False, quality_score=0.0,
                                reason="forced")


def _export(topology, suite, report, **kwargs):
    return asyncio.run(export_seeds(topology, suite, report, **kwargs))


def test_flagship_bad_source_rejected_good_source_frozen() -> None:
    topology, report = _report(_suite())
    payload = _export(topology, _suite(), report)

    entries = {entry.scenario_id: entry for entry in payload.entries}
    # The shortest S1 candidate chains data.read to source_bad (fingerprint
    # order), which raises at runtime; the replay rejects it and the
    # exporter falls through to the source_good chain.
    assert entries["S1"].status == ENTRY_FROZEN
    assert entries["S1"].attempts == 2
    layers = {
        segment.layer: segment.tools
        for segment in payload.routes["S1"].layers
    }
    assert layers["read"] == ("source_good",)
    assert "S1" in payload.payload["seeds"]


def test_span_above_entry_layer_is_rejected_and_accounted() -> None:
    topology, report = _report(_suite())
    payload = _export(topology, _suite(), report)
    entries = {entry.scenario_id: entry for entry in payload.entries}

    # S2's only subset-minimal candidate is the single-layer span
    # [analyze]:[decide]. Replaying it finishes at the read layer without
    # running anything (empty route), so the coverage guard rejects it.
    # RouteSearch emits no non-dominated alternative here.
    assert entries["S2"].status == ENTRY_REPLAY_FAILED
    assert entries["S2"].attempts == 1
    assert "no observed route" in entries["S2"].reason
    assert "S2" not in payload.routes

    # S3: no provider at all -> no candidates, zero attempts.
    assert entries["S3"].status == ENTRY_NO_CANDIDATES
    assert entries["S3"].attempts == 0


def test_evaluator_gates_the_freeze() -> None:
    topology, report = _report(_suite())
    failing = _export(topology, _suite(), report, evaluator=_Fail())
    passing = _export(topology, _suite(), report, evaluator=_Pass())

    by_id_failing = {e.scenario_id: e for e in failing.entries}
    assert by_id_failing["S1"].status == ENTRY_REPLAY_FAILED
    assert "evaluation failed" in by_id_failing["S1"].reason
    by_id_passing = {e.scenario_id: e for e in passing.entries}
    assert by_id_passing["S1"].status == ENTRY_FROZEN


def test_payload_carries_fingerprint_and_roundtrips() -> None:
    topology, report = _report(_suite())
    payload = _export(topology, _suite(), report)
    assert payload.topology_fingerprint == declared_fingerprint(topology)
    assert payload.payload["format_version"] == 2
    assert payload.payload["source"] == "fast-report"

    # fast report JSON roundtrip keeps candidate routes intact
    restored = fast_report_from_json(fast_report_to_json(report))
    re_exported = _export(topology, _suite(), restored)
    assert re_exported.routes.keys() == payload.routes.keys()


def test_read_seeds_v2_and_legacy(tmp_path) -> None:
    topology, report = _report(_suite())
    payload = _export(topology, _suite(), report)
    seeds_path = payload.write(tmp_path / "seeds.json")

    routes, fingerprint = read_seeds(seeds_path)
    assert fingerprint == declared_fingerprint(topology)
    assert set(routes) == set(payload.routes)

    legacy = tmp_path / "legacy.json"
    legacy.write_text(
        '{"S1": {"layers": [{"layer": "read", "tools": ["source_good"]}],'
        ' "capabilities": ["data.read"]}}',
        encoding="utf-8",
    )
    legacy_routes, legacy_fingerprint = read_seeds(legacy)
    assert legacy_fingerprint is None
    assert legacy_routes["S1"].layers[0].tools == ("source_good",)


def test_invalid_arguments_rejected() -> None:
    topology, report = _report(_suite())
    with pytest.raises(SeedExportError):
        _export(topology, _suite(), report, replay_trials=0)
    with pytest.raises(SeedExportError):
        _export(topology, _suite(), report, max_verify=0)


def test_p10_seed_witness_agrees_with_static_coverage_where_witnessed() -> None:
    """Dual-mechanism check (P10): a frozen seed must imply static coverage
    and cover the required capabilities; static-uncovered must never freeze.
    Static-covered-but-unwitnessed (S2) is the documented honest divergence:
    execution witness is strictly stronger than static feasibility."""
    topology, report = _report(_suite())
    payload = _export(topology, _suite(), report)
    static = {r.scenario_id: r for r in report.results}

    for scenario in _suite().scenarios:
        result = static[scenario.id]
        required = frozenset(scenario.expected_capabilities)
        entry = next(e for e in payload.entries if e.scenario_id == scenario.id)
        if result.status.value == "uncovered":
            assert entry.status == ENTRY_NO_CANDIDATES
            continue
        if entry.status == ENTRY_FROZEN:
            route = payload.routes[scenario.id]
            covered = {
                capability
                for segment in route.layers
                for tool in segment.tools
                for capability in topology.node(tool).spec.capabilities
            }
            assert required.issubset(covered)
            assert result.status.value in ("covered", "uncertain")
