"""Office battlefield scenario suite structure checks.

Acceptance (docs/acceptance/office-battlefield.md §6): the full 60-scenario
suite spans four families with the 42/9/9 covered/uncertain/uncovered split,
declares corpus fixtures for every scenario, and marks exactly three critical
sentinel documents. Uncovered scenarios deliberately reference capabilities
from the milestone's deferred list, so fast regression stays honest.

The loader itself performs strict schema validation (unknown fields, capability
format, duplicate ids), so a successful load is already one assertion deep.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from capability_runtime import ScenarioLoader  # noqa: E402
from capability_runtime.scenario.models import Scenario  # noqa: E402

SCENARIOS_PATH = _ROOT / "examples" / "office" / "scenarios.json"

TOTAL_SCENARIOS = 60
FAMILY_COUNTS = {
    "doc_report": 20,
    "slide_deck": 15,
    "sheet_analysis": 15,
    "mail_comms": 10,
}
COVERAGE_COUNTS = {"covered": 42, "uncertain": 9, "uncovered": 9}
FIXTURE_COUNTS = {"clean": 30, "messy": 12, "sparse": 10, "conflict": 8}

ALLOWED_FIXTURES = frozenset(FIXTURE_COUNTS)
ALLOWED_DISCOVERY = frozenset({"ambiguous", "low_confidence"})
CONFLICT_FAMILIES = frozenset({"doc_report", "slide_deck", "mail_comms"})

# Deferred capabilities (office-battlefield.md §10). Referencing any of these
# marks a scenario as uncovered by the declared topology.
DEFERRED_CAPABILITIES = frozenset(
    {
        "doc.merge",
        "pdf.render",
        "mail.send",
        "image.find",
        "calendar.schedule",
        "xlsx.write_formula",
        "web.search",
    }
)

_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@pytest.fixture(scope="module")
def suite():
    return ScenarioLoader().load_file(SCENARIOS_PATH)


@pytest.fixture(scope="module")
def scenarios(suite):
    return suite.scenarios


def classify(scenario: Scenario) -> str:
    """Uncovered: references a deferred capability; uncertain: carries a
    discovery hint; covered: everything else."""
    if any(cap in DEFERRED_CAPABILITIES for cap in scenario.expected_capabilities):
        return "uncovered"
    if "discovery" in scenario.metadata:
        return "uncertain"
    return "covered"


def is_table_chain(scenario: Scenario) -> bool:
    """Shape F/G sheet scenarios plus the table.parse-led deferred sheet
    scenarios. The spec's >=8-messy / >=6-sparse floors cannot both hold over
    the 12 strict shape-F/G scenarios alone (8 + 6 > 12), so the deferred
    sheet scenarios that still parse a table count into the chain pool."""
    return (
        scenario.category == "sheet_analysis"
        and "table.parse" in scenario.expected_capabilities
    )


def test_suite_totals_and_unique_ids(scenarios) -> None:
    assert len(scenarios) == TOTAL_SCENARIOS

    identifiers = [scenario.id for scenario in scenarios]
    duplicates = sorted(
        identifier
        for identifier, count in Counter(identifiers).items()
        if count > 1
    )
    assert duplicates == []
    for identifier in identifiers:
        assert _SNAKE_CASE_PATTERN.fullmatch(identifier), identifier

    queries = [scenario.query for scenario in scenarios]
    assert all(query.strip() for query in queries)
    assert len(set(queries)) == TOTAL_SCENARIOS, "queries must not repeat verbatim"


def test_family_counts_match_spec(scenarios) -> None:
    assert all(scenario.category is not None for scenario in scenarios)
    counts = Counter(scenario.category for scenario in scenarios)
    assert dict(counts) == FAMILY_COUNTS


def test_coverage_split_matches_spec(scenarios) -> None:
    counts = Counter(classify(scenario) for scenario in scenarios)
    assert dict(counts) == COVERAGE_COUNTS


def test_uncovered_scenarios_reference_deferred_capabilities(scenarios) -> None:
    uncovered = [s for s in scenarios if classify(s) == "uncovered"]
    assert uncovered, "the suite must keep fast regression honest"
    for scenario in uncovered:
        referenced = [
            cap for cap in scenario.expected_capabilities if cap in DEFERRED_CAPABILITIES
        ]
        assert referenced, scenario.id
        # Deferred capabilities are the uncovering marker only: covered and
        # uncertain scenarios must stay inside the declared topology.
    covered_or_uncertain = [
        s for s in scenarios if classify(s) in {"covered", "uncertain"}
    ]
    for scenario in covered_or_uncertain:
        assert not (
            set(scenario.expected_capabilities) & DEFERRED_CAPABILITIES
        ), scenario.id


def test_sentinels_are_three_critical_doc_reports(scenarios) -> None:
    sentinels = [s for s in scenarios if s.metadata.get("sentinel") is True]
    assert len(sentinels) == 3
    for scenario in sentinels:
        assert scenario.category == "doc_report", scenario.id
        assert scenario.metadata["fixture"] == "clean", scenario.id
        assert scenario.metadata["priority"] == "critical", scenario.id
        # Sentinels must be covered scenarios: they protect real topology edges.
        assert classify(scenario) == "covered", scenario.id


def test_fixture_distribution_matches_spec(scenarios) -> None:
    for scenario in scenarios:
        assert scenario.metadata["fixture"] in ALLOWED_FIXTURES, scenario.id

    counts = Counter(scenario.metadata["fixture"] for scenario in scenarios)
    assert dict(counts) == FIXTURE_COUNTS

    # Conflict fixtures only hit covered scenarios whose fact extraction or
    # quality judging is affected by contradicting sources.
    for scenario in scenarios:
        if scenario.metadata["fixture"] == "conflict":
            assert scenario.category in CONFLICT_FAMILIES, scenario.id
            assert classify(scenario) == "covered", scenario.id

    # Table chains skew messy/sparse (>=8 messy, >=6 sparse).
    messy_chains = sum(
        1
        for s in scenarios
        if is_table_chain(s) and s.metadata["fixture"] == "messy"
    )
    sparse_chains = sum(
        1
        for s in scenarios
        if is_table_chain(s) and s.metadata["fixture"] == "sparse"
    )
    assert messy_chains >= 8, messy_chains
    assert sparse_chains >= 6, sparse_chains


def test_metadata_shapes_are_canonical(scenarios) -> None:
    for scenario in scenarios:
        metadata = dict(scenario.metadata)
        assert "fixture" in metadata, scenario.id

        if metadata.get("sentinel") is True:
            assert set(metadata) == {"fixture", "sentinel", "priority"}, scenario.id
            assert metadata["priority"] == "critical", scenario.id
            assert "discovery" not in metadata, scenario.id
        else:
            assert "sentinel" not in metadata, scenario.id
            assert "priority" not in metadata, scenario.id
            if "discovery" in metadata:
                assert set(metadata) == {"fixture", "discovery"}, scenario.id
                assert metadata["discovery"] in ALLOWED_DISCOVERY, scenario.id
            else:
                assert set(metadata) == {"fixture"}, scenario.id


def test_expected_capabilities_are_lowercase_dot(scenarios) -> None:
    for scenario in scenarios:
        assert scenario.expected_capabilities, scenario.id
        for capability in scenario.expected_capabilities:
            assert _CAPABILITY_PATTERN.fullmatch(capability), (
                f"{scenario.id}: {capability!r}"
            )
