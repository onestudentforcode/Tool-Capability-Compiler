from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.errors import CounterfactualError
from ..regression.coverage import CoverageStatus
from ..regression.report import CoverageReport, FastRegressionRunner
from ..scenario.models import ScenarioSuite
from ..topology.models import Topology
from ..topology.patch import TopologyPatch, apply_patch


class CounterfactualVerdict(str, Enum):
    """Overall verdict of a counterfactual fast regression run.

    A counterfactual only *concludes* whether coverage was preserved; it never
    approves a prune on its own ($124 / $125).
    """

    PASS = "pass"
    COVERAGE_DROP = "coverage_drop"
    NO_IMPACT = "no_impact"


# CoverageSeverity ranks worst as we move COVERED < UNCERTAIN < UNCOVERED.
_SEVERITY = {
    CoverageStatus.COVERED: 0,
    CoverageStatus.UNCERTAIN: 1,
    CoverageStatus.UNCOVERED: 2,
}


@dataclass(frozen=True, slots=True)
class ScenarioCounterfactual:
    """Per-scenario comparison between the declared and patched fast views."""

    scenario_id: str
    before_status: CoverageStatus
    after_status: CoverageStatus
    before_capabilities: tuple[str, ...]
    after_capabilities: tuple[str, ...]

    @property
    def unchanged(self) -> bool:
        return self.before_status == self.after_status

    @property
    def severity_delta(self) -> int:
        return _SEVERITY[self.after_status] - _SEVERITY[self.before_status]

    @property
    def regressed(self) -> bool:
        """Coverage moved to a worse status (e.g. COVERED -> UNCOVERED, $125)."""
        return self.severity_delta > 0

    @property
    def covered_lost(self) -> bool:
        """A previously covered scenario lost its declared coverage entirely."""
        return (
            self.before_status == CoverageStatus.COVERED
            and self.after_status != CoverageStatus.COVERED
        )


@dataclass(frozen=True, slots=True)
class CounterfactualResult:
    """Evidence, not a decision: which scenarios the patch keeps covered."""

    base_version: str
    patch: TopologyPatch
    before: CoverageReport
    after: CoverageReport
    checks: tuple[ScenarioCounterfactual, ...]

    @property
    def regressed_scenarios(self) -> tuple[ScenarioCounterfactual, ...]:
        return tuple(check for check in self.checks if check.regressed)

    @property
    def covered_lost_scenarios(self) -> tuple[ScenarioCounterfactual, ...]:
        return tuple(check for check in self.checks if check.covered_lost)

    @property
    def unchanged_count(self) -> int:
        return sum(1 for check in self.checks if check.unchanged)

    @property
    def verdict(self) -> CounterfactualVerdict:
        if self.covered_lost_scenarios:
            return CounterfactualVerdict.COVERAGE_DROP
        return (
            CounterfactualVerdict.PASS
            if not self.regressed_scenarios
            else CounterfactualVerdict.NO_IMPACT
        )

    @property
    def verdict_safe(self) -> bool:
        """True when no previously covered scenario lost its coverage ($125)."""
        return not self.covered_lost_scenarios

    def __post_init__(self) -> None:
        if not self.base_version or not self.base_version.strip():
            raise CounterfactualError(
                "CounterfactualResult requires a non-empty base_version"
            )


class CounterfactualRunner:
    """Re-run Fast Regression on the patched view and compare against declared.

    Reuses FastRegressionRunner ($121 Step 5): the counterfactual is a fast,
    metadata-only check, never an execution. Coverage drop of a previously
    covered scenario hard-fails the candidate ($125) and users may stop here;
    an unchanged verdict simply allows validation to continue ($124).
    """

    def __init__(
        self,
        runner: FastRegressionRunner | None = None,
    ) -> None:
        self._runner = runner or FastRegressionRunner()

    async def run(
        self,
        suite: ScenarioSuite,
        base_topology: Topology,
        patch: TopologyPatch,
        *,
        base_version: str = "declared",
    ) -> CounterfactualResult:
        if not base_version or not base_version.strip():
            raise CounterfactualError("base_version must be a non-empty string")

        before = await self._runner.run(
            suite, base_topology, topology_version=base_version
        )
        active = apply_patch(base_topology, patch)
        after = await self._runner.run(
            suite, active, topology_version=f"{base_version}#counterfactual"
        )

        before_by_id = {result.scenario_id: result for result in before.results}
        after_by_id = {result.scenario_id: result for result in after.results}
        if before_by_id.keys() != after_by_id.keys():
            raise CounterfactualError(
                "counterfactual changed the scenario set; patch must not add/remove scenarios"
            )

        checks = tuple(
            ScenarioCounterfactual(
                scenario_id=scenario_id,
                before_status=before_by_id[scenario_id].status,
                after_status=after_by_id[scenario_id].status,
                before_capabilities=before_by_id[scenario_id].covered_capabilities,
                after_capabilities=after_by_id[scenario_id].covered_capabilities,
            )
            for scenario_id in sorted(before_by_id)
        )
        return CounterfactualResult(
            base_version=base_version.strip(),
            patch=patch,
            before=before,
            after=after,
            checks=checks,
        )