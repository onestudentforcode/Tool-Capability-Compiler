"""JSONL + companion-file persistence for Slow Regression runs.

Step 14 writer dumps one Trial structure per line into ``traces.jsonl`` plus a
``manifest.json`` and per-dimension JSON stats, matching the recommended output
layout (phase3 §104-106):

    artifacts/slow_regression/
        run_<runid>/
            manifest.json
            report.json
            traces.jsonl
            node_stats.json
            edge_stats.json
            route_stats.json

Serialization is depth-first and intentionally lossy on opaque objects: unknown
values degrade to ``str(value)`` so credentials or large objects never leak
verbatim (phase3 §103). Failures raise :class:`TraceSerializationError`, which
is distinct from a business or evaluation failure.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ...core.errors import ExecutionError, TraceSerializationError
from ...core.metrics import TokenUsage
from .report import SlowRegressionReport
from .route import ObservedRoute
from .stats import ObservationReport
from .trial import TrialResult


def _to_json(value: Any) -> Any:
    """Recursively reduce a trial structure to JSON-safe primitives."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, TokenUsage):
        return {"input_tokens": value.input_tokens, "output_tokens": value.output_tokens}
    if isinstance(value, ObservedRoute):
        return {
            "route_id": value.route_id,
            "canonical": value.canonical,
            "segments": [
                {"layer": segment.layer, "tools": list(segment.tools)}
                for segment in value.segments
            ],
        }
    if isinstance(value, ExecutionError):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _to_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_to_json(item) for item in value]
    if is_dataclass(value):
        return {field.name: _to_json(getattr(value, field.name)) for field in fields(value)}
    return str(value)


def serialize_trial_result(result: TrialResult) -> dict[str, Any]:
    try:
        return _to_json(result)
    except Exception as exc:  # noqa: BLE001 - any failure is a serialization failure
        raise TraceSerializationError(
            f"Cannot serialize trial {result.trial.id!r}: {exc}"
        ) from exc


def _obs_to_json(stats: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [_to_json(item) for item in stats.values()]


@dataclass(frozen=True, slots=True)
class RunManifest:
    run_id: str
    suite_name: str
    suite_version: str
    topology_version: str
    router_config_id: str
    created_at: str
    trial_count: int
    scenario_count: int
    evaluator: str


class SlowRegressionWriter:
    """Writes one slow-regression run's artifacts into an output directory."""

    def __init__(self, out_dir: str | Path) -> None:
        self._out_dir = Path(out_dir)

    @property
    def output_dir(self) -> Path:
        return self._out_dir

    def write(
        self,
        *,
        run_id: str,
        suite_name: str,
        suite_version: str,
        topology_version: str,
        router_config_id: str,
        evaluator: str,
        outcome: Any,
        report: SlowRegressionReport,
        obs: ObservationReport,
    ) -> dict[str, Path]:
        """Persist a full run; returns the path of each written artifact."""
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            manifest = RunManifest(
                run_id=run_id,
                suite_name=suite_name,
                suite_version=suite_version,
                topology_version=topology_version,
                router_config_id=router_config_id,
                created_at=datetime.now().isoformat(),
                trial_count=len(outcome.results),
                scenario_count=obs.scenario_count,
                evaluator=evaluator,
            )
            self._out_dir.joinpath("manifest.json").write_text(
                self._dumps(manifest), encoding="utf-8"
            )
            self._out_dir.joinpath("report.json").write_text(
                self._dumps(report), encoding="utf-8"
            )
            with self._out_dir.joinpath("traces.jsonl").open(
                "w", encoding="utf-8"
            ) as file:
                for result in outcome.results:
                    file.write(json.dumps(serialize_trial_result(result)) + "\n")
            self._out_dir.joinpath("node_stats.json").write_text(
                self._dumps(_obs_to_json(obs.node_stats)), encoding="utf-8"
            )
            self._out_dir.joinpath("edge_stats.json").write_text(
                self._dumps(_obs_to_json(obs.edge_stats)), encoding="utf-8"
            )
            self._out_dir.joinpath("route_stats.json").write_text(
                self._dumps(_obs_to_json(obs.route_stats)), encoding="utf-8"
            )
        except TraceSerializationError:
            raise
        except Exception as exc:  # noqa: BLE001 - unify IO/config failures
            raise TraceSerializationError(f"cannot write slow run {run_id!r}: {exc}") from exc

        return {
            name: self._out_dir.joinpath(name)
            for name in (
                "manifest.json",
                "report.json",
                "traces.jsonl",
                "node_stats.json",
                "edge_stats.json",
                "route_stats.json",
            )
        }

    @staticmethod
    def _dumps(value: Any) -> str:
        try:
            return json.dumps(_to_json(value), indent=2, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001 - unify serialization failures
            raise TraceSerializationError(f"cannot serialize output: {exc}") from exc