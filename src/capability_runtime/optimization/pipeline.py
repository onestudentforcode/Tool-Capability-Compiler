"""Optimize pipeline: analyze / validate / commit / rollback orchestration.

Three explicit, file-mediated stages over the existing Phase 4 components —
zero implicit chaining: analyze writes a proposal, validate writes a verdict,
commit is the only write to topology versions and refuses patches without a
matching ACCEPT record (optimize-pipeline §0-§4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.errors import ArtifactLoadError, CommitGateError
from ..regression.slow.report import build_slow_regression_report
from ..regression.slow.runner import SlowRegressionRunner
from ..regression.slow.stats import build_observation_stats
from ..scenario.models import Scenario, ScenarioSuite
from ..topology.loader import TopologyLoader
from ..topology.models import Topology
from ..topology.patch import TopologyPatch, apply_patch
from ..topology.version import TopologyVersion, commit_patch, initial_version
from ..evaluation.structured import StructuredEvaluator
from ..fixtures.manager import DefaultFixtureManager
from ..execution.context import ExecutionEnvironment
from .analyzer import split_suite
from .artifacts import (
    declared_fingerprint,
    export_active_payload,
    patch_fingerprint,
    trial_results_from_dir,
)
from .batch import BatchCandidateBuilder
from .candidate import (
    CandidateDetector,
    CandidateStatus,
    PruningConfig,
    ProtectionRegistry,
)
from .counterfactual import CounterfactualRunner
from .evidence import EvidenceAggregator
from .pruning import (
    FastValidationGate,
    RouteDiversityGuard,
    SlowValidationGate,
)


def _sentinel_scenarios(suite: ScenarioSuite) -> list[Scenario]:
    return [
        scenario
        for scenario in suite.scenarios
        if scenario.metadata.get("sentinel") is True
    ]


def _subset_suite(suite: ScenarioSuite, ids) -> ScenarioSuite:
    keep = set(ids)
    return ScenarioSuite(
        name=suite.name,
        version=suite.version,
        description=suite.description,
        scenarios=tuple(s for s in suite.scenarios if s.id in keep),
    )


# ---- ① analyze ---------------------------------------------------------------


async def analyze(
    topology: Topology,
    suite: ScenarioSuite,
    slow_report_dir: str | Path,
    *,
    min_edge_opportunities: int | None = None,
    min_node_availability: int | None = None,
) -> dict[str, Any]:
    """Evidence -> candidate proposal (read-only; Optimization set only §2)."""
    split = split_suite(suite)
    optimization_suite = _subset_suite(suite, split.optimization_ids)
    if not optimization_suite.scenarios:
        raise ArtifactLoadError(
            "optimization split is empty; provide a larger scenario suite"
        )

    all_results = trial_results_from_dir(slow_report_dir)
    optimization_ids = set(split.optimization_ids)
    results = tuple(
        result
        for result in all_results
        if result.trial.scenario_id in optimization_ids
    )
    if not results:
        raise ArtifactLoadError(
            "slow report contains no trials for the optimization split"
        )

    edges = [(edge.source, edge.target) for edge in topology.edges()]
    observation = build_observation_stats(results, edges=edges)

    protection = ProtectionRegistry(
        topology, sentinel_scenarios=_sentinel_scenarios(suite)
    )
    evidence = EvidenceAggregator().build(
        report=observation,
        results=results,
        edges=edges,
        protected_edges=protection.protected_edges(),
    )
    config_kwargs: dict[str, Any] = {}
    if min_edge_opportunities is not None:
        config_kwargs["min_edge_opportunities"] = min_edge_opportunities
    if min_node_availability is not None:
        config_kwargs["min_node_availability"] = min_node_availability
    detector = CandidateDetector(
        config=PruningConfig(**config_kwargs),
        protected_nodes=protection.protected_nodes(),
    )
    candidates = detector.detect(evidence)

    identified_edges = [
        candidate.source + "->" + candidate.target
        for candidate in candidates
        if candidate.status is CandidateStatus.IDENTIFIED
        and candidate.kind == "edge"
    ]
    counterfactual_summary: dict[str, Any] = {"verdict": "skipped"}
    if identified_edges:
        probe_patch = TopologyPatch(disabled_edges=tuple(identified_edges))
        counterfactual = await CounterfactualRunner().run(
            optimization_suite, topology, probe_patch, base_version="declared"
        )
        counterfactual_summary = {
            "verdict": counterfactual.verdict.value,
            "regressed_scenarios": [
                item.scenario_id for item in counterfactual.regressed_scenarios
            ],
        }

    batches = ()
    if identified_edges:
        builder = BatchCandidateBuilder()
        batches = builder.build(
            [
                tuple(key.split("->"))
                for key in identified_edges
                if len(key.split("->")) == 2
            ],
            base_topology_version="declared",
        )
    batch_of: dict[str, str] = {}
    for batch in batches:
        for source, target in batch.edges:
            batch_of[f"{source}->{target}"] = batch.id

    payload: dict[str, Any] = {
        "stage": "analyze",
        "declared_fingerprint": declared_fingerprint(topology),
        "split": {
            "optimization": list(split.optimization_ids),
            "validation": list(split.validation_ids),
            "sentinel": list(split.sentinel_ids),
        },
        "candidates": [
            {
                "kind": candidate.kind,
                "subject": candidate.subject,
                "status": candidate.status.value,
                "reason": candidate.reason.value if candidate.reason else None,
                "opportunity_count": candidate.opportunity_count,
                "usage_rate": candidate.usage_rate,
                "protected": candidate.protected,
                "counterfactual": (
                    counterfactual_summary
                    if candidate.status is CandidateStatus.IDENTIFIED
                    else None
                ),
                "batch": batch_of.get(candidate.subject)
                if candidate.source
                else None,
            }
            for candidate in candidates
        ],
        "patch": {
            "disabled_edges": sorted(identified_edges),
            "disabled_nodes": sorted(
                candidate.subject
                for candidate in candidates
                if candidate.status is CandidateStatus.IDENTIFIED
                and candidate.kind == "node"
            ),
        },
    }
    return payload


# ---- ② validate --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidateConfig:
    trials: int = 3
    expected_facts: tuple[tuple[str, str], ...] = ()
    max_concurrency: int = 1


async def validate(
    topology: Topology,
    suite: ScenarioSuite,
    patch: TopologyPatch,
    *,
    config: ValidateConfig | None = None,
) -> dict[str, Any]:
    """Three ordered gates: fast -> slow -> diversity (§3). REJECT is a verdict."""
    run_config = config or ValidateConfig()
    split = split_suite(suite)
    gate_ids = set(split.validation_ids) | set(split.sentinel_ids)
    if not gate_ids:
        # tiny suites may yield an empty validation split; fall back to the
        # optimization set so the gates still evaluate something (recorded)
        gate_ids = set(split.optimization_ids)
    gate_suite = _subset_suite(suite, gate_ids)

    counterfactual = await CounterfactualRunner().run(
        gate_suite, topology, patch, base_version="declared"
    )
    sentinel_gate_ids = sorted(
        set(split.sentinel_ids)
        | {scenario.id for scenario in _sentinel_scenarios(suite)}
    )
    fast_result = FastValidationGate(
        sentinel_scenarios=sentinel_gate_ids
    ).evaluate(counterfactual)

    slow_result = None
    diversity_result = None
    if fast_result.passed:
        evaluator = StructuredEvaluator(dict(run_config.expected_facts))
        before = await _run_slow(
            topology, gate_suite, evaluator, run_config, "base"
        )
        active = apply_patch(topology, patch)
        after = await _run_slow(
            active, gate_suite, evaluator, run_config, "candidate"
        )
        slow_result = SlowValidationGate().evaluate(
            before=before.report, after=after.report
        )
        # the diversity floor must not false-reject naturally sparse worlds:
        # an unchanged family count is never a collapse (spec 3 / 129)
        before_families = sum(
            1
            for stat in before.observation.route_stats.values()
            if stat.business_success_count > 0
        )
        diversity_result = RouteDiversityGuard(
            min_successful_route_families=max(1, min(2, before_families))
        ).evaluate(
            before_routes=before.observation.route_stats,
            after_routes=after.observation.route_stats,
        )

    accepted = fast_result.passed and (
        slow_result is None or slow_result.passed
    ) and (diversity_result is None or diversity_result.passed)

    failures: list[dict[str, Any]] = [
        {
            "domain": failure.domain,
            "key": failure.key,
            "reason": failure.reason,
            "before": failure.before,
            "after": failure.after,
        }
        for gate_result in (fast_result, slow_result, diversity_result)
        if gate_result is not None
        for failure in getattr(gate_result, "failures", ())
    ]
    return {
        "stage": "validate",
        "verdict": "accept" if accepted else "reject",
        "declared_fingerprint": declared_fingerprint(topology),
        "patch_fingerprint": patch_fingerprint(
            patch.disabled_edges, patch.disabled_nodes
        ),
        "fast": {"passed": fast_result.passed},
        "slow": (
            {"passed": slow_result.passed} if slow_result else {"skipped": True}
        ),
        "diversity": (
            {"passed": diversity_result.passed}
            if diversity_result
            else {"skipped": True}
        ),
        "failures": failures,
        "config": {
            "trials": run_config.trials,
            "expected_facts": [
                list(pair) for pair in run_config.expected_facts
            ],
            "validation_ids": list(split.validation_ids),
            "sentinel_ids": sentinel_gate_ids,
        },
    }


@dataclass
class _SlowRun:
    report: Any
    observation: Any


async def _run_slow(
    topology: Topology, suite: ScenarioSuite, evaluator, config: ValidateConfig, tag: str
) -> _SlowRun:
    runner = SlowRegressionRunner(
        topology=topology,
        evaluator=evaluator,
        fixture_manager=DefaultFixtureManager(),
        trials_per_scenario=config.trials,
        max_concurrency=config.max_concurrency,
        topology_version=f"validate-{tag}",
        router_config_id="optimize-validate",
        environment=ExecutionEnvironment.SANDBOX,
    )
    outcome = await runner.run(suite)
    edges = [(edge.source, edge.target) for edge in topology.edges()]
    observation = build_observation_stats(outcome.results, edges=edges)
    report = build_slow_regression_report(
        outcome,
        observation,
        suite=suite,
        topology=topology,
        topology_version=f"validate-{tag}",
        router_config_id="optimize-validate",
    )
    return _SlowRun(report=report, observation=observation)


# ---- ③ commit / ④ rollback ---------------------------------------------------


def commit(
    topology: Topology,
    original_payload: dict[str, Any],
    patch: TopologyPatch,
    *,
    validation: dict[str, Any],
    version: str,
    versions_dir: str | Path,
    base_version: str = "v1",
) -> dict[str, Any]:
    """The only write to topology versions; ACCEPT record is a hard gate (§4)."""
    if not isinstance(validation, dict) or validation.get("verdict") != "accept":
        raise CommitGateError(
            "commit refused: validation record must carry verdict=accept"
        )
    expected = patch_fingerprint(patch.disabled_edges, patch.disabled_nodes)
    if validation.get("patch_fingerprint") != expected:
        raise CommitGateError(
            "commit refused: patch fingerprint does not match the validation "
            f"record ({validation.get('patch_fingerprint')!r} != {expected!r})"
        )
    if validation.get("declared_fingerprint") != declared_fingerprint(topology):
        raise CommitGateError(
            "commit refused: validation record was produced against a "
            "different declared topology"
        )

    directory = Path(versions_dir)
    directory.mkdir(parents=True, exist_ok=True)
    record_path = directory / f"{version}.json"
    if record_path.exists():
        raise CommitGateError(
            f"version {version!r} already exists; topology versions are "
            "immutable (§101)"
        )

    base = initial_version(topology, version=base_version)
    new_version = commit_patch(base, patch, version=version)
    record = {
        "version": new_version.version,
        "base_version": base_version,
        "declared_fingerprint": declared_fingerprint(topology),
        "patch": {
            "disabled_edges": list(patch.disabled_edges),
            "disabled_nodes": list(patch.disabled_nodes),
        },
        "composed": {
            "disabled_edges": list(new_version.patch.disabled_edges),
            "disabled_nodes": list(new_version.patch.disabled_nodes),
        },
        "validation": {
            "verdict": validation.get("verdict"),
            "patch_fingerprint": validation.get("patch_fingerprint"),
        },
    }
    record_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    topology_path = directory / f"{version}.topology.json"
    topology_path.write_text(
        json.dumps(
            export_active_payload(original_payload, new_version.active),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (directory / "current.json").write_text(
        json.dumps({"version": new_version.version}, indent=2),
        encoding="utf-8",
    )
    return record


def rollback(
    topology: Topology,
    original_payload: dict[str, Any],
    *,
    versions_dir: str | Path,
    to: str | None = None,
) -> dict[str, Any]:
    """Record replay — never an inverse patch (§4 dependencies)."""
    directory = Path(versions_dir)
    fingerprint = declared_fingerprint(topology)

    if to is None:
        record = {
            "version": "declared-restored",
            "base_version": None,
            "declared_fingerprint": fingerprint,
            "patch": {"disabled_edges": [], "disabled_nodes": []},
            "composed": {"disabled_edges": [], "disabled_nodes": []},
            "validation": None,
        }
        active = topology
    else:
        record_path = directory / f"{to}.json"
        if not record_path.is_file():
            raise CommitGateError(
                f"rollback refused: no version record {record_path}"
            )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("declared_fingerprint") != fingerprint:
            raise CommitGateError(
                "rollback refused: record was written against a different "
                "declared topology (fingerprint mismatch)"
            )
        patch = TopologyPatch(
            disabled_edges=tuple(record.get("composed", {}).get("disabled_edges", ())),
            disabled_nodes=tuple(record.get("composed", {}).get("disabled_nodes", ())),
        )
        record = dict(record)
        record["version"] = f"{to}-restored"
        active = apply_patch(topology, patch)

    topology_path = directory / f"{record['version']}.topology.json"
    topology_path.write_text(
        json.dumps(
            export_active_payload(original_payload, active),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (directory / "current.json").write_text(
        json.dumps({"version": record["version"]}, indent=2),
        encoding="utf-8",
    )
    return record


def patch_from_payload(payload: dict[str, Any]) -> TopologyPatch:
    """Load a TopologyPatch from a candidates.json patch block."""
    block = payload.get("patch", {})
    return TopologyPatch(
        disabled_edges=tuple(block.get("disabled_edges", ())),
        disabled_nodes=tuple(block.get("disabled_nodes", ())),
    )


def load_original_payload(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
