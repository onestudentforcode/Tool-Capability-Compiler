from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from ..core.errors import FastRegressionError
from ..capability import CapabilityResolver
from ..scenario import Scenario, ScenarioSuite
from ..topology import Topology
from .coverage import (
    CoverageAnalyzer,
    CoverageResult,
    CoverageStatus,
    FailureReason,
)
from .route_search import CandidateRoute


@dataclass(frozen=True, slots=True)
class FastRegressionResult:
    scenario_id: str
    category: str | None
    status: CoverageStatus
    reason: FailureReason | None
    required_capabilities: tuple[str, ...]
    covered_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    candidate_routes: tuple[CandidateRoute, ...]
    confidence: float
    reason_detail: str
    optional_capabilities: tuple[str, ...] = ()
    missing_capability_hints: tuple[str, ...] = ()

    @classmethod
    def from_coverage(
        cls, scenario: Scenario, coverage: CoverageResult
    ) -> FastRegressionResult:
        return cls(
            scenario_id=scenario.id,
            category=scenario.category,
            status=coverage.status,
            reason=coverage.reason,
            required_capabilities=coverage.required_capabilities,
            covered_capabilities=coverage.covered_capabilities,
            missing_capabilities=coverage.missing_capabilities,
            candidate_routes=coverage.candidate_routes,
            confidence=coverage.confidence,
            reason_detail=coverage.reason_detail,
        )


@dataclass(frozen=True, slots=True)
class CategoryCoverage:
    category: str
    total: int
    covered: int
    uncertain: int
    uncovered: int

    @property
    def coverage_rate(self) -> float:
        return self.covered / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class GapEntry:
    capability: str
    count: int
    scenario_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TopologyGapEntry:
    required_capabilities: tuple[str, ...]
    count: int
    scenario_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoverageReport:
    suite_name: str
    suite_version: str
    topology_version: str
    total: int
    covered: int
    uncertain: int
    uncovered: int
    results: tuple[FastRegressionResult, ...]
    categories: tuple[CategoryCoverage, ...]
    missing_capabilities: tuple[GapEntry, ...]
    topology_gaps: tuple[TopologyGapEntry, ...]

    @property
    def coverage_rate(self) -> float:
        return self.covered / self.total if self.total else 0.0


class FastRegressionRunner:
    """Run metadata-only Gold or resolver-backed Discovery regression."""

    def __init__(
        self,
        analyzer: CoverageAnalyzer | None = None,
        resolver: CapabilityResolver | None = None,
    ) -> None:
        self._analyzer = analyzer or CoverageAnalyzer()
        self._resolver = resolver

    async def run(
        self,
        suite: ScenarioSuite,
        topology: Topology,
        *,
        topology_version: str = "declared",
    ) -> CoverageReport:
        if not isinstance(topology_version, str) or not topology_version.strip():
            raise FastRegressionError("topology_version must be a non-empty string")

        available_capabilities = frozenset(
            capability
            for name in topology.nodes()
            for capability in topology.node(name).spec.capabilities
        )
        results = tuple(
            [
                await self._analyze_scenario(
                    scenario, topology, available_capabilities
                )
                for scenario in suite.scenarios
            ]
        )
        counts = Counter(result.status for result in results)
        return CoverageReport(
            suite_name=suite.name,
            suite_version=suite.version,
            topology_version=topology_version.strip(),
            total=len(results),
            covered=counts[CoverageStatus.COVERED],
            uncertain=counts[CoverageStatus.UNCERTAIN],
            uncovered=counts[CoverageStatus.UNCOVERED],
            results=results,
            categories=self._category_coverage(results),
            missing_capabilities=self._gap_entries(
                results, FailureReason.MISSING_CAPABILITY
            ),
            topology_gaps=self._topology_gap_entries(results),
        )

    async def _analyze_scenario(
        self,
        scenario: Scenario,
        topology: Topology,
        available_capabilities: frozenset[str],
    ) -> FastRegressionResult:
        if scenario.is_gold:
            coverage = self._analyzer.analyze(
                topology, set(scenario.expected_capabilities)
            )
            return FastRegressionResult.from_coverage(scenario, coverage)

        if self._resolver is None:
            return FastRegressionResult(
                scenario_id=scenario.id,
                category=scenario.category,
                status=CoverageStatus.UNCERTAIN,
                reason=FailureReason.INVALID_SCENARIO,
                required_capabilities=(),
                covered_capabilities=(),
                missing_capabilities=(),
                candidate_routes=(),
                confidence=0.0,
                reason_detail=(
                    "Query-only scenario requires a CapabilityResolver from Step 7"
                ),
            )

        resolution = await self._resolver.resolve(
            scenario.query, available_capabilities
        )
        if resolution.missing_capability_hints:
            required = tuple(
                sorted(
                    set(resolution.required)
                    | set(resolution.missing_capability_hints)
                )
            )
            return FastRegressionResult(
                scenario_id=scenario.id,
                category=scenario.category,
                status=CoverageStatus.UNCOVERED,
                reason=FailureReason.MISSING_CAPABILITY,
                required_capabilities=required,
                covered_capabilities=(),
                missing_capabilities=resolution.missing_capability_hints,
                candidate_routes=(),
                confidence=resolution.confidence,
                reason_detail=resolution.reasoning or "Resolver discovered capability gaps",
                optional_capabilities=resolution.optional,
                missing_capability_hints=resolution.missing_capability_hints,
            )
        if not resolution.required:
            return FastRegressionResult(
                scenario_id=scenario.id,
                category=scenario.category,
                status=CoverageStatus.UNCERTAIN,
                reason=FailureReason.AMBIGUOUS_CAPABILITY,
                required_capabilities=(),
                covered_capabilities=(),
                missing_capabilities=(),
                candidate_routes=(),
                confidence=resolution.confidence,
                reason_detail=resolution.reasoning or "Resolver found no required capability",
                optional_capabilities=resolution.optional,
            )
        coverage = self._analyzer.analyze(
            topology,
            set(resolution.required),
            resolution_confidence=resolution.confidence,
        )
        result = FastRegressionResult.from_coverage(scenario, coverage)
        return FastRegressionResult(
            scenario_id=result.scenario_id,
            category=result.category,
            status=result.status,
            reason=result.reason,
            required_capabilities=result.required_capabilities,
            covered_capabilities=result.covered_capabilities,
            missing_capabilities=result.missing_capabilities,
            candidate_routes=result.candidate_routes,
            confidence=result.confidence,
            reason_detail=result.reason_detail or resolution.reasoning or "",
            optional_capabilities=resolution.optional,
            missing_capability_hints=resolution.missing_capability_hints,
        )

    @staticmethod
    def _category_coverage(
        results: tuple[FastRegressionResult, ...],
    ) -> tuple[CategoryCoverage, ...]:
        grouped: dict[str, list[FastRegressionResult]] = defaultdict(list)
        for result in results:
            grouped[result.category or "uncategorized"].append(result)
        categories: list[CategoryCoverage] = []
        for category in sorted(grouped):
            items = grouped[category]
            counts = Counter(item.status for item in items)
            categories.append(
                CategoryCoverage(
                    category=category,
                    total=len(items),
                    covered=counts[CoverageStatus.COVERED],
                    uncertain=counts[CoverageStatus.UNCERTAIN],
                    uncovered=counts[CoverageStatus.UNCOVERED],
                )
            )
        return tuple(categories)

    @staticmethod
    def _gap_entries(
        results: tuple[FastRegressionResult, ...], reason: FailureReason
    ) -> tuple[GapEntry, ...]:
        affected: dict[str, set[str]] = defaultdict(set)
        for result in results:
            if result.reason != reason:
                continue
            capabilities = result.missing_capabilities or result.required_capabilities
            for capability in capabilities:
                affected[capability].add(result.scenario_id)
        return tuple(
            GapEntry(
                capability=capability,
                count=len(affected[capability]),
                scenario_ids=tuple(sorted(affected[capability])),
            )
            for capability in sorted(affected)
        )

    @staticmethod
    def _topology_gap_entries(
        results: tuple[FastRegressionResult, ...],
    ) -> tuple[TopologyGapEntry, ...]:
        affected: dict[tuple[str, ...], set[str]] = defaultdict(set)
        for result in results:
            if result.reason == FailureReason.TOPOLOGY_DISCONNECTED:
                affected[result.required_capabilities].add(result.scenario_id)
        return tuple(
            TopologyGapEntry(
                required_capabilities=capabilities,
                count=len(affected[capabilities]),
                scenario_ids=tuple(sorted(affected[capabilities])),
            )
            for capabilities in sorted(affected)
        )
