"""Baseline persistence and regression diffing for Fast Regression.

A :class:`Baseline` is a versioned snapshot of a :class:`CoverageReport`
captured at a known ``topology_version``. It stores only the per-scenario
``CoverageStatus`` (plus counters) needed to answer "did a tool / topology
change regress any business capability?" without retaining full candidate
routes (phase2.md sections 51-52).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.errors import BaselineLoadError, BaselineSaveError
from .coverage import CoverageStatus
from .report import CoverageReport, FastRegressionResult


@dataclass(frozen=True, slots=True)
class ScenarioStatus:
    scenario_id: str
    status: CoverageStatus


@dataclass(frozen=True, slots=True)
class Baseline:
    suite_name: str
    suite_version: str
    topology_version: str
    total: int
    covered: int
    uncertain: int
    uncovered: int
    results: tuple[ScenarioStatus, ...]

    @property
    def coverage_rate(self) -> float:
        return self.covered / self.total if self.total else 0.0

    @classmethod
    def from_report(cls, report: CoverageReport) -> Baseline:
        counts = {CoverageStatus.COVERED: 0, CoverageStatus.UNCERTAIN: 0, CoverageStatus.UNCOVERED: 0}
        for result in report.results:
            counts[result.status] += 1
        return cls(
            suite_name=report.suite_name,
            suite_version=report.suite_version,
            topology_version=report.topology_version,
            total=report.total,
            covered=counts[CoverageStatus.COVERED],
            uncertain=counts[CoverageStatus.UNCERTAIN],
            uncovered=counts[CoverageStatus.UNCOVERED],
            results=tuple(
                sorted(
                    (ScenarioStatus(r.scenario_id, r.status) for r in report.results),
                    key=lambda item: item.scenario_id,
                )
            ),
        )

    def status_of(self, scenario_id: str) -> CoverageStatus | None:
        for entry in self.results:
            if entry.scenario_id == scenario_id:
                return entry.status
        return None


class BaselineStore:
    """Strict JSON persistence for :class:`Baseline` snapshots."""

    def save(self, baseline: Baseline, path: str | Path) -> None:
        target = Path(path)
        payload = {
            "kind": "fast-regression-baseline",
            "suite_name": baseline.suite_name,
            "suite_version": baseline.suite_version,
            "topology_version": baseline.topology_version,
            "total": baseline.total,
            "covered": baseline.covered,
            "uncertain": baseline.uncertain,
            "uncovered": baseline.uncovered,
            "coverage_rate": baseline.coverage_rate,
            "results": [
                {"scenario_id": item.scenario_id, "status": item.status.value}
                for item in baseline.results
            ],
        }
        try:
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            raise BaselineSaveError(f"Cannot write baseline to {target}: {exc}") from exc

    def load(self, path: str | Path) -> Baseline:
        source = Path(path)
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise BaselineLoadError(f"Cannot read baseline from {source}: {exc}") from exc
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BaselineLoadError(
                f"Baseline file {source} is not valid JSON: {exc}"
            ) from exc
        return self._parse(obj, source)

    def _parse(self, obj: Any, source: Path) -> Baseline:
        if not isinstance(obj, Mapping) or obj.get("kind") != "fast-regression-baseline":
            raise BaselineLoadError(f"Baseline file {source} has unsupported format")
        suite_name = self._require_str(obj, "suite_name", source)
        suite_version = self._require_str(obj, "suite_version", source)
        topology_version = self._require_str(obj, "topology_version", source)
        try:
            total = int(obj["total"])
            covered = int(obj["covered"])
            uncertain = int(obj["uncertain"])
            uncovered = int(obj["uncovered"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BaselineLoadError(f"Baseline file {source} has invalid counters: {exc}") from exc
        results = self._parse_results(obj.get("results"), source)
        return Baseline(
            suite_name=suite_name,
            suite_version=suite_version,
            topology_version=topology_version,
            total=total,
            covered=covered,
            uncertain=uncertain,
            uncovered=uncovered,
            results=results,
        )

    @staticmethod
    def _parse_results(raw: Any, source: Path) -> tuple[ScenarioStatus, ...]:
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise BaselineLoadError(f"Baseline file {source} field 'results' must be an array")
        seen: set[str] = set()
        entries: list[ScenarioStatus] = []
        for index, item in enumerate(raw):
            location = f"{source} results[{index}]"
            if not isinstance(item, Mapping):
                raise BaselineLoadError(f"{location} must be an object")
            scenario_id = item.get("scenario_id")
            status_value = item.get("status")
            if not isinstance(scenario_id, str) or not scenario_id.strip():
                raise BaselineLoadError(f"{location} 'scenario_id' must be a non-empty string")
            if scenario_id in seen:
                raise BaselineLoadError(f"{location} duplicate scenario_id {scenario_id!r}")
            seen.add(scenario_id)
            if not isinstance(status_value, str):
                raise BaselineLoadError(f"{location} 'status' must be a string")
            try:
                status = CoverageStatus(status_value)
            except ValueError as exc:
                raise BaselineLoadError(
                    f"{location} invalid status {status_value!r}"
                ) from exc
            entries.append(ScenarioStatus(scenario_id, status))
        return tuple(sorted(entries, key=lambda item: item.scenario_id))

    @staticmethod
    def _require_str(obj: Mapping[str, Any], key: str, source: Path) -> str:
        value = obj.get(key)
        if not isinstance(value, str) or not value.strip():
            raise BaselineLoadError(f"Baseline file {source} field {key!r} must be a non-empty string")
        return value


@dataclass(frozen=True, slots=True)
class StatusChange:
    scenario_id: str
    before: CoverageStatus
    after: CoverageStatus
    reason_after: str = ""


@dataclass(frozen=True, slots=True)
class RegressionDiff:
    baseline_topology_version: str
    current_topology_version: str
    newly_covered: tuple[str, ...] = ()
    newly_uncovered: tuple[str, ...] = ()
    still_uncovered: tuple[str, ...] = ()
    still_covered: tuple[str, ...] = ()
    status_changed: tuple[StatusChange, ...] = ()

    @property
    def gained_cover(self) -> int:
        return len(self.newly_covered)

    @property
    def lost_cover(self) -> int:
        return len(self.newly_uncovered)


def compute_diff(baseline: Baseline, report: CoverageReport) -> RegressionDiff:
    """Compare a fresh report against a baseline by scenario id."""
    result_by_id = {
        result.scenario_id: result
        for result in report.results
        if result.scenario_id in {item.scenario_id for item in baseline.results}
    }
    newly_covered: list[str] = []
    newly_uncovered: list[str] = []
    still_uncovered: list[str] = []
    still_covered: list[str] = []
    status_changed: list[StatusChange] = []
    for entry in sorted(baseline.results, key=lambda item: item.scenario_id):
        current = result_by_id.get(entry.scenario_id)
        before = entry.status
        if current is None:
            continue
        after = current.status
        if after == CoverageStatus.COVERED and before != CoverageStatus.COVERED:
            newly_covered.append(entry.scenario_id)
        if after == CoverageStatus.UNCOVERED and before != CoverageStatus.UNCOVERED:
            newly_uncovered.append(entry.scenario_id)
        if after == CoverageStatus.UNCOVERED and before == CoverageStatus.UNCOVERED:
            still_uncovered.append(entry.scenario_id)
        if after == CoverageStatus.COVERED and before == CoverageStatus.COVERED:
            still_covered.append(entry.scenario_id)
        if before != after:
            status_changed.append(
                StatusChange(
                    scenario_id=entry.scenario_id,
                    before=before,
                    after=after,
                    reason_after=_reason_text(current),
                )
            )
    return RegressionDiff(
        baseline_topology_version=baseline.topology_version,
        current_topology_version=report.topology_version,
        newly_covered=tuple(sorted(newly_covered)),
        newly_uncovered=tuple(sorted(newly_uncovered)),
        still_uncovered=tuple(sorted(still_uncovered)),
        still_covered=tuple(sorted(still_covered)),
        status_changed=tuple(
            sorted(status_changed, key=lambda item: item.scenario_id)
        ),
    )


def _reason_text(result: FastRegressionResult) -> str:
    parts = [result.reason.value if result.reason else "covered"]
    if result.reason_detail:
        parts.append(result.reason_detail)
    return " - ".join(parts)