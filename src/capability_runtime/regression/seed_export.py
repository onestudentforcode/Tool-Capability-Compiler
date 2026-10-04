"""Freeze fast-regression feasible chains into replay-verified seeds.

The seed lifecycle (discovery-routing.md §0): fast's RouteSearch proves
chains *statically feasible*; this module makes them *execution-witnessed*.
Every candidate chain is replayed once against the real topology and only
chains that complete — and, when an evaluator is supplied, pass evaluation —
are frozen into ``seeds.json`` together with the declared-topology
fingerprint, so a topology change makes stale seeds fail loudly instead of
silently degrading.

Coverage authority stays with static fast (batch A ruling): a replay
failure means "no witness found", never "uncovered". Uncovered scenarios
have no chains and are honestly absent from the payload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..core.errors import SeedExportError
from ..scenario import Scenario, ScenarioSuite
from ..topology import Topology
from .candidate_route import CandidateRoute
from .coverage import CoverageStatus
from .report import CoverageReport, route_from_json, route_to_json

SEEDS_FORMAT_VERSION = 2
KEY_FORMAT_VERSION = "format_version"
KEY_TOPOLOGY_FINGERPRINT = "topology_fingerprint"
KEY_SEEDS = "seeds"
KEY_SUITE = "suite"
KEY_SOURCE = "source"
KEY_GENERATED_AT = "generated_at"

SOURCE_FAST_REPORT = "fast-report"

ENTRY_FROZEN = "frozen"
ENTRY_NO_CANDIDATES = "no-candidates"
ENTRY_REPLAY_FAILED = "replay-failed"
ENTRY_MISSING_FROM_REPORT = "missing-from-report"


@dataclass(frozen=True, slots=True)
class SeedEntry:
    """Per-scenario freeze outcome: what was tried and what won."""

    scenario_id: str
    status: str
    attempts: int
    reason: str
    route: CandidateRoute | None = None


@dataclass(frozen=True, slots=True)
class SeedsPayload:
    """The v2 seeds document plus the structured view over it."""

    topology_fingerprint: str
    routes: dict[str, CandidateRoute]
    entries: tuple[SeedEntry, ...]
    payload: dict

    @property
    def frozen_count(self) -> int:
        return sum(1 for entry in self.entries if entry.status == ENTRY_FROZEN)

    def write(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return out


async def export_seeds(
    topology: Topology,
    suite: ScenarioSuite,
    report: CoverageReport,
    *,
    replay_trials: int = 1,
    max_verify: int = 3,
    evaluator=None,
) -> SeedsPayload:
    """Replay-verify fast candidate chains and freeze the survivors.

    Chains are tried shortest-first (RouteSearch orders candidates by
    tool count). Success criterion: every replayed trial COMPLETED with a
    non-empty observed route covering the expected capabilities; when an
    evaluator is supplied its verdict must pass as well. Uncovered
    scenarios carry no chains and are skipped without attempts.
    """
    from ..optimization.artifacts import declared_fingerprint

    if replay_trials < 1:
        raise SeedExportError("replay_trials must be a positive integer")
    if max_verify < 1:
        raise SeedExportError("max_verify must be a positive integer")

    fingerprint = declared_fingerprint(topology)
    by_id = {result.scenario_id: result for result in report.results}
    routes: dict[str, CandidateRoute] = {}
    entries: list[SeedEntry] = []

    for scenario in suite.scenarios:
        result = by_id.get(scenario.id)
        if result is None:
            entries.append(
                SeedEntry(
                    scenario_id=scenario.id,
                    status=ENTRY_MISSING_FROM_REPORT,
                    attempts=0,
                    reason="fast report has no result for this scenario",
                )
            )
            continue
        if result.status is CoverageStatus.UNCOVERED or not result.candidate_routes:
            entries.append(
                SeedEntry(
                    scenario_id=scenario.id,
                    status=ENTRY_NO_CANDIDATES,
                    attempts=0,
                    reason=f"status={result.status.value}",
                )
            )
            continue

        frozen: CandidateRoute | None = None
        attempts = 0
        last_reason = ""
        for candidate in list(result.candidate_routes)[:max_verify]:
            attempts += 1
            ok, reason = await replay_verified(
                topology, scenario, candidate, replay_trials, evaluator
            )
            if ok:
                frozen = candidate
                break
            last_reason = reason
        if frozen is None:
            entries.append(
                SeedEntry(
                    scenario_id=scenario.id,
                    status=ENTRY_REPLAY_FAILED,
                    attempts=attempts,
                    reason=last_reason,
                )
            )
            continue
        routes[scenario.id] = frozen
        entries.append(
            SeedEntry(
                scenario_id=scenario.id,
                status=ENTRY_FROZEN,
                attempts=attempts,
                reason="",
                route=frozen,
            )
        )

    return build_seed_payload(topology, suite, source=SOURCE_FAST_REPORT, entries=entries)


def build_seed_payload(
    topology: Topology,
    suite: ScenarioSuite,
    *,
    source: str,
    entries: list[SeedEntry],
) -> SeedsPayload:
    """Assemble the v2 seeds document shared by all discovery paths."""
    from ..optimization.artifacts import declared_fingerprint  # lazy: optimization imports regression.slow

    fingerprint = declared_fingerprint(topology)
    routes = {
        entry.scenario_id: entry.route
        for entry in entries
        if entry.status == ENTRY_FROZEN and entry.route is not None
    }
    payload = {
        KEY_FORMAT_VERSION: SEEDS_FORMAT_VERSION,
        KEY_TOPOLOGY_FINGERPRINT: fingerprint,
        KEY_SUITE: {"name": suite.name, "version": suite.version},
        KEY_SOURCE: source,
        KEY_GENERATED_AT: datetime.now().isoformat(timespec="seconds"),
        KEY_SEEDS: {
            scenario_id: route_to_json(route)
            for scenario_id, route in sorted(routes.items())
        },
        "entries": [
            {
                "scenario_id": entry.scenario_id,
                "status": entry.status,
                "attempts": entry.attempts,
                "reason": entry.reason,
                "route": route_to_json(entry.route) if entry.route else None,
            }
            for entry in entries
        ],
    }
    return SeedsPayload(
        topology_fingerprint=fingerprint,
        routes=dict(sorted(routes.items())),
        entries=tuple(entries),
        payload=payload,
    )


async def replay_verified(
    topology: Topology,
    scenario: Scenario,
    route: CandidateRoute,
    replay_trials: int,
    evaluator,
) -> tuple[bool, str]:
    """One deterministic replay of ``route``; returns (ok, failure reason)."""
    from ..evaluation.structured import StructuredEvaluator
    from .slow.runner import SlowRegressionRunner
    from .slow.trial import TrialExecutionStatus

    single = ScenarioSuite(
        name="seed-replay", version="1", scenarios=(scenario,)
    )
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=evaluator if evaluator is not None else StructuredEvaluator({}),
        seeds={scenario.id: route},
        trials_per_scenario=replay_trials,
        topology_version="seed-replay",
        router_config_id="seed-export",
    )
    outcome = await runner.run(single)
    required = frozenset(scenario.expected_capabilities)
    for result in outcome.results:
        if result.execution_status is not TrialExecutionStatus.COMPLETED:
            detail = (
                result.failure_category.value
                if result.failure_category
                else result.execution_status.value
            )
            return False, f"replay {result.trial.id}: {detail}"
        if evaluator is not None and (
            result.evaluation is None or not result.evaluation.success
        ):
            return False, f"replay {result.trial.id}: evaluation failed"
        observed = result.route
        if observed is None:
            return False, f"replay {result.trial.id}: no observed route"
        covered = {
            capability
            for segment in observed.segments
            for tool in segment.tools
            for capability in topology.node(tool).spec.capabilities
        }
        if not required.issubset(covered):
            missing = sorted(required - covered)
            return False, (
                f"replay {result.trial.id}: observed route does not cover "
                f"{', '.join(missing)}"
            )
    return True, ""


def read_seeds(path: str | Path) -> tuple[dict[str, CandidateRoute], str | None]:
    """Read seeds.json in either format; returns (routes, fingerprint|None).

    v2 files carry a header (format_version / topology_fingerprint / seeds
    map); legacy files are a plain ``{scenario_id: route}`` map with no
    fingerprint and therefore never trigger the mismatch check.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SeedExportError(f"seeds file must be a JSON object: {path}")
    if raw.get(KEY_FORMAT_VERSION) == SEEDS_FORMAT_VERSION and KEY_SEEDS in raw:
        seed_map = raw[KEY_SEEDS]
        fingerprint = raw.get(KEY_TOPOLOGY_FINGERPRINT)
    else:
        seed_map = raw
        fingerprint = None
    routes: dict[str, CandidateRoute] = {}
    for scenario_id, item in seed_map.items():
        if not isinstance(item, dict) or "layers" not in item:
            raise SeedExportError(
                f"seeds file {path}: entry {scenario_id!r} is not a route"
            )
        routes[scenario_id] = route_from_json(item)
    return routes, (fingerprint if isinstance(fingerprint, str) else None)
