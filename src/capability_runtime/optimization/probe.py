from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from ..core.errors import ProbeError
from ..evaluation.base import Evaluator
from ..fixtures.base import FixtureManager
from ..regression.candidate_route import CandidateRoute
from ..regression.slow.runner import SlowRegressionRunner
from ..route.models import RouteLayer
from ..scenario.models import ScenarioSuite
from ..topology.models import Topology
from ..router.protocol import LayerRouter


class ProbeVerdict(str, Enum):
    """What a directed probe learned about one candidate edge."""

    OBSERVED = "observed"
    NOT_OBSERVED = "not_observed"
    NOT_REACHABLE = "not_reachable"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Evidence from directed probe trials; never a prune decision by itself."""

    edge: tuple[str, str]
    verdict: ProbeVerdict
    total_trials: int
    observed_count: int
    reached_count: int

    def __post_init__(self) -> None:
        if len(self.edge) != 2 or not all(
            isinstance(name, str) and name.strip() for name in self.edge
        ):
            raise ProbeError("Probe edge must be a pair of non-empty strings")
        if self.total_trials < 0:
            raise ProbeError("total_trials cannot be negative")
        if not 0 <= self.observed_count <= self.total_trials:
            raise ProbeError("observed_count must be within [0, total_trials]")
        if not 0 <= self.reached_count <= self.total_trials:
            raise ProbeError("reached_count must be within [0, total_trials]")

    @property
    def observed(self) -> bool:
        return self.verdict is ProbeVerdict.OBSERVED

    @property
    def reachable(self) -> bool:
        return self.verdict in (ProbeVerdict.OBSERVED, ProbeVerdict.NOT_OBSERVED)


def build_directed_seed(
    base: CandidateRoute,
    topology: Topology,
    source: str,
    target: str,
) -> CandidateRoute:
    """Force a candidate edge into a full-width seed, reusing the basefast path.

    A probe exists only to raise the chance the candidate edge is explored; the
    seed keeps the base span so earlier and later layers stay reachable ($147).
    """
    if not topology.has_edge(source, target):
        raise ProbeError(f"Cannot direct a probe along non-edge {source}->{target}")
    src_layer = topology.node(source).spec.layer
    tgt_layer = topology.node(target).spec.layer
    if src_layer == tgt_layer:
        raise ProbeError(f"Cannot build a probe seed for a same-layer pair {source}->{target}")

    base_by_layer = {layer.layer: set(layer.tools) for layer in base.layers}
    layers = [
        tuple(sorted(set(base_by_layer.get(layer.name, ()))))
        for layer in topology.layers()
    ]
    for index, layer in enumerate(topology.layers()):
        if layer.name == src_layer:
            layers[index] = tuple(sorted(set(layers[index]) | {source}))
        if layer.name == tgt_layer:
            layers[index] = tuple(sorted(set(layers[index]) | {target}))
    return CandidateRoute(
        layers=tuple(
            RouteLayer(layer.name, layer_tools)
            for layer, layer_tools in zip(topology.layers(), layers)
        ),
        capabilities=frozenset(),
    )


def edge_observed_in_trace(
    trace,
    *,
    src_layer: str,
    tgt_layer: str,
    source: str,
    target: str,
) -> bool:
    seen_source = False
    for layer_exec in trace.layers:
        if layer_exec.layer == src_layer:
            if source in layer_exec.selected_tools:
                seen_source = True
        elif layer_exec.layer == tgt_layer and seen_source:
            if target in layer_exec.selected_tools:
                return True
    return False


def _reached_target(trace, tgt_layer: str) -> bool:
    return any(layer_exec.layer == tgt_layer for layer_exec in trace.layers)


class ProbeRunner:
    """Run directed Slow Regression probes along candidate edges.

    Reuses SlowRegressionRunner in basefast mode with a directed seed. A probe
    adds breadth evidence (§124): it tells whether an underused edge is still a
    reachable, executable path (OBSERVED) or genuinely idle (NOT_OBSERVED).
    Probe results feed the Slow Validation Gate; they never prune on their own.
    """

    def __init__(
        self,
        *,
        topology: Topology,
        evaluator: Evaluator,
        fixture_manager: FixtureManager | None = None,
        trials_per_scenario: int = 5,
        max_concurrency: int = 1,
        max_tools_per_layer: int = 3,
        topology_version: str = "probe-v1",
        router_config_id: str = "probe-basefast",
        router: LayerRouter | None = None,
    ) -> None:
        if trials_per_scenario < 1:
            raise ProbeError("trials_per_scenario must be positive")
        self._topology = topology
        self._evaluator = evaluator
        self._fixture_manager = fixture_manager
        self._trials = trials_per_scenario
        self._max_concurrency = max_concurrency
        self._max_tools = max_tools_per_layer
        self._topology_version = topology_version
        self._router_config_id = router_config_id
        self._router = router

    async def run(
        self,
        suite: ScenarioSuite,
        source: str,
        target: str,
        *,
        seeds: Mapping[str, CandidateRoute],
    ) -> ProbeResult:
        if not seeds:
            raise ProbeError("Probes require a per-scenario base seed to remain reachable")
        scenario_ids = {scenario.id for scenario in suite.scenarios}
        missing = sorted(scenario_ids - set(seeds))
        if missing:
            raise ProbeError(
                "Probes require a base seed for every scenario; missing: "
                + ", ".join(missing)
            )
        src_layer = self._topology.node(source).spec.layer
        tgt_layer = self._topology.node(target).spec.layer
        directed = {
            scenario.id: build_directed_seed(
                seeds[scenario.id], self._topology, source, target
            )
            for scenario in suite.scenarios
        }

        runner = SlowRegressionRunner(
            topology=self._topology,
            evaluator=self._evaluator,
            fixture_manager=self._fixture_manager,
            seeds=directed,
            trials_per_scenario=self._trials,
            max_concurrency=self._max_concurrency,
            max_tools_per_layer=self._max_tools,
            topology_version=self._topology_version,
            router_config_id=self._router_config_id,
            router=self._router,
        )
        outcome = await runner.run(suite)

        total = len(outcome.results)
        observed = 0
        reached = 0
        for result in outcome.results:
            if edge_observed_in_trace(
                result.trace,
                src_layer=src_layer,
                tgt_layer=tgt_layer,
                source=source,
                target=target,
            ):
                observed += 1
            if _reached_target(result.trace, tgt_layer):
                reached += 1

        if total == 0 or reached == 0:
            verdict = ProbeVerdict.NOT_REACHABLE
        elif observed:
            verdict = ProbeVerdict.OBSERVED
        else:
            verdict = ProbeVerdict.NOT_OBSERVED

        return ProbeResult(
            edge=(source, target),
            verdict=verdict,
            total_trials=total,
            observed_count=observed,
            reached_count=reached,
        )