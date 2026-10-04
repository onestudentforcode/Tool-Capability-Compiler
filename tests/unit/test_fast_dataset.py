"""Battlefield Hardening Batch E: the fast-regression scenario asset.

`examples/datasets/customer_service.fast.json` is a first-class business
dataset (phase2 §70): 60 scenarios across 5 categories with a designed
70% covered / 15% uncertain (query-only) / 15% uncovered (capability or
topology gap) distribution against the declared refund topology
(battlefield-hardening §6).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

from capability_runtime import (
    CoverageStatus,
    FailureReason,
    FastRegressionRunner,
    ScenarioLoader,
    TopologyLoader,
)

_PROJECT = Path(__file__).resolve().parents[2]
_DATASET = _PROJECT / "examples" / "datasets" / "customer_service.fast.json"
_TOPOLOGY = _PROJECT / "examples" / "topology" / "refund.json"


def _report():
    suite = ScenarioLoader().load_file(str(_DATASET))
    topology = TopologyLoader().load_file(str(_TOPOLOGY))
    return asyncio.run(
        FastRegressionRunner().run(suite, topology, topology_version="declared")
    )


def test_dataset_is_valid_and_five_categories() -> None:
    suite = ScenarioLoader().load_file(str(_DATASET))
    assert len(suite.scenarios) == 60
    categories = {s.category for s in suite.scenarios if s.category}
    assert categories == {"order", "refund", "email", "user", "invoice"}
    # ids unique and sorted output is stable
    ids = [s.id for s in suite.scenarios]
    assert len(set(ids)) == len(ids)


def test_designed_distribution_is_exact() -> None:
    report = _report()
    assert report.total == 60
    assert report.covered == 42
    assert report.uncertain == 9
    assert report.uncovered == 9
    assert report.coverage_rate == 0.7


def test_uncovered_splits_into_capability_and_topology_gaps() -> None:
    report = _report()
    reasons = Counter(
        result.reason for result in report.results
        if result.status is CoverageStatus.UNCOVERED
    )
    assert reasons[FailureReason.MISSING_CAPABILITY] == 7
    assert reasons[FailureReason.TOPOLOGY_DISCONNECTED] == 2


def test_sentinel_scenarios_are_flagged_in_metadata() -> None:
    suite = ScenarioLoader().load_file(str(_DATASET))
    sentinels = {
        s.id for s in suite.scenarios if s.metadata.get("sentinel") is True
    }
    # one covered refund sentinel + one uncovered invoice sentinel
    assert sentinels == {"refund_001", "invoice_201"}
    report = _report()
    by_id = {r.scenario_id: r.status for r in report.results}
    assert by_id["refund_001"] is CoverageStatus.COVERED
    assert by_id["invoice_201"] is CoverageStatus.UNCOVERED


def test_category_report_locates_the_blind_zones() -> None:
    report = _report()
    by_category = {entry.category: entry for entry in report.categories}
    # email.send exists but is unreachable (providers=[]) -> every executed
    # email scenario fails; user/invoice have no providers at all.
    assert by_category["refund"].covered > 0
    assert by_category["email"].covered == 0
    assert by_category["user"].covered == 0
    assert by_category["invoice"].covered == 0
