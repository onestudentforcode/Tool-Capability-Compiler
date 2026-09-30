"""Disk adapters: slow-run artifacts -> in-memory pipeline inputs (§1).

Lossy by design and documented: rebuilt ``TrialResult`` objects keep what the
evidence machinery consumes (trial identity, status, route segments,
evaluation outcome, metering) and degrade error objects to strings — this
never pretends to be a lossless round-trip. Version contradictions between
manifest and traces are refused, mirroring the ranking loader.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..core.errors import ArtifactLoadError
from ..regression.slow.route import ObservedRoute
from ..regression.slow.stats import build_observation_stats
from ..regression.slow.trace import ExecutionTrace
from ..regression.slow.trial import (
    Trial,
    TrialExecutionStatus,
    TrialResult,
)
from ..evaluation.base import EvaluationResult
from ..core.metrics import TokenUsage


def _load_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    traces_path = run_dir / "traces.jsonl"
    if not manifest_path.is_file() or not traces_path.is_file():
        raise ArtifactLoadError(
            f"{run_dir} is not a slow-run directory "
            "(manifest.json and traces.jsonl are required)"
        )
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactLoadError(f"cannot read manifest.json: {exc}") from exc


def trial_results_from_dir(run_dir: str | Path) -> tuple[TrialResult, ...]:
    """Rebuild lightweight TrialResults from traces.jsonl (lossy, §1)."""
    directory = Path(run_dir)
    manifest = _load_manifest(directory)
    results: list[TrialResult] = []
    with (directory / "traces.jsonl").open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(_result_from_payload(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ArtifactLoadError(
                    f"traces.jsonl line {line_number} is unreadable: {exc}"
                ) from exc
    if not results:
        raise ArtifactLoadError("traces.jsonl contains no trials")
    _check_versions(manifest, results)
    return tuple(results)


def observation_report_from_dir(
    run_dir: str | Path, *, edges
):
    """Observation stats rebuilt from the (filtered) trace records."""
    return build_observation_stats(trial_results_from_dir(run_dir), edges=edges)


def _result_from_payload(payload: dict[str, Any]) -> TrialResult:
    trial_raw = payload["trial"]
    trial = Trial(
        id=str(trial_raw["id"]),
        scenario_id=str(trial_raw["scenario_id"]),
        trial_index=int(trial_raw.get("trial_index", 0)),
        topology_version=str(trial_raw["topology_version"]),
        scenario_suite_version=str(trial_raw.get("scenario_suite_version", "")),
        router_config_id=str(trial_raw.get("router_config_id", "")),
    )
    route_payload = payload.get("route")
    route = None
    if route_payload:
        segments = [
            (str(segment["layer"]), tuple(segment["tools"]))
            for segment in route_payload.get("segments", ())
        ]
        if segments:
            route = ObservedRoute.from_layers(segments)
    evaluation_payload = payload.get("evaluation")
    evaluation = None
    if evaluation_payload:
        evaluation = EvaluationResult(
            success=bool(evaluation_payload.get("success")),
            quality_score=evaluation_payload.get("quality_score"),
        )
    tokens = payload.get("token_usage") or {}
    trace_payload = payload.get("trace") or {}
    layers = _rebuild_layers(trace_payload.get("layers", ()))
    return TrialResult(
        trial=trial,
        execution_status=TrialExecutionStatus(
            str(payload.get("execution_status", "completed"))
        ),
        route=route,
        trace=ExecutionTrace(
            trial_id=trial.id,
            scenario_id=trial.scenario_id,
            topology_version=trial.topology_version,
            layers=layers,
        ),
        evaluation=evaluation,
        latency_ms=float(payload.get("latency_ms", 0.0)),
        token_usage=TokenUsage(
            input_tokens=int(tokens.get("input_tokens", 0)),
            output_tokens=int(tokens.get("output_tokens", 0)),
        ),
        cost=payload.get("cost"),
        tool_cost=payload.get("tool_cost"),
        routing_cost=payload.get("routing_cost"),
        evaluation_cost=payload.get("evaluation_cost"),
        access_counts=payload.get("access_counts"),
    )


def _rebuild_layers(layers_payload) -> tuple:
    """Minimal LayerExecution sufficient for observation statistics.

    The statistics consume available/selected tool sets per layer; the
    execution records themselves are not needed, so they rebuild as empty —
    this is the documented lossy part (§1).
    """
    from datetime import datetime

    from ..regression.slow.trace import LayerExecution
    from ..router.models import RoutingAction, RoutingDecision

    rebuilt = []
    for layer_payload in layers_payload:
        selected = tuple(layer_payload.get("selected_tools", ()))
        if not selected:
            continue
        stamp = datetime.now()
        rebuilt.append(
            LayerExecution(
                layer=str(layer_payload.get("layer", "?")),
                available_tools=tuple(layer_payload.get("available_tools", ())),
                selected_tools=selected,
                routing_decision=RoutingDecision(
                    action=RoutingAction.EXECUTE, selected_tools=selected
                ),
                tool_executions=(),
                started_at=stamp,
                ended_at=stamp,
            )
        )
    return tuple(rebuilt)


def _check_versions(manifest: dict[str, Any], results: list[TrialResult]) -> None:
    manifest_pair = (
        str(manifest.get("topology_version", "")),
        str(manifest.get("router_config_id", "")),
    )
    row_pairs = {
        (result.trial.topology_version, result.trial.router_config_id)
        for result in results
    }
    if len(row_pairs) > 1 or (
        manifest_pair != ("", "") and manifest_pair not in row_pairs
    ):
        raise ArtifactLoadError(
            "manifest and traces disagree on topology/router versions; "
            "refusing mixed evidence"
        )


def declared_fingerprint(topology) -> str:
    """A stable hash of the declared search space (rollback dependency §4)."""
    parts = ["layers:" + ",".join(
        f"{layer.name}@{layer.order}" for layer in topology.layers()
    )]
    parts.append("nodes:" + ",".join(
        f"{name}@{topology.node(name).spec.layer}"
        f":{'|'.join(sorted(topology.node(name).spec.capabilities))}"
        for name in topology.nodes()
    ))
    parts.append("edges:" + ",".join(
        f"{edge.source}->{edge.target}" for edge in topology.edges()
    ))
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def patch_fingerprint(disabled_edges, disabled_nodes) -> str:
    """A stable hash of a patch's disabled sets (commit gate dependency)."""
    payload = json.dumps(
        {
            "edges": sorted(disabled_edges),
            "nodes": sorted(disabled_nodes),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def export_active_payload(payload: dict[str, Any], topology_active) -> dict[str, Any]:
    """Loader-compatible active-topology JSON for a patched view.

    Disabled nodes are dropped; providers/workers are materialized from the
    ACTIVE topology's actual adjacency so the exported file reproduces the
    active search space exactly (the declared file is never touched).
    """
    exported = json.loads(json.dumps(dict(payload)))
    active_names = set(topology_active.nodes())
    kept_tools = []
    for item in exported.get("tools", []):
        if str(item.get("name")) not in active_names:
            continue
        name = str(item["name"])
        item["workers"] = sorted(topology_active.successors(name))
        item["providers"] = sorted(topology_active.predecessors(name))
        kept_tools.append(item)
    exported["tools"] = kept_tools
    return exported
