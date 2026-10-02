"""Model-driven seed discovery (discovery-routing batch B).

The model — any :class:`LayerRouter`, typically :class:`LLMRouter` over a
local Ollama — sits in the routing seat for one discovery run per scenario:
it picks tools from the legal pool layer by layer, the tools really execute,
and the observed route is the model's proposed chain. The chain is then
**replay-verified deterministically** (the same criterion as batch A) before
it may be frozen: a model chain that cannot reproduce its own success is
recorded as ``replay-failed``, never frozen.

Discovery failures (routing errors, failing tools, failed evaluation) are
recorded as ``discovery-failed`` with the trial's failure category — they
are evidence, not exceptions. Coverage authority stays with static fast.
"""

from __future__ import annotations

from collections.abc import Callable

from ..evaluation.structured import StructuredEvaluator
from ..route.models import RouteLayer
from ..scenario import Scenario, ScenarioSuite
from ..topology import Topology
from .candidate_route import CandidateRoute
from .seed_export import (
    ENTRY_FROZEN,
    ENTRY_REPLAY_FAILED,
    SeedEntry,
    SeedsPayload,
    build_seed_payload,
    replay_verified,
)
from .slow.runner import SlowRegressionRunner
from .slow.trial import TrialExecutionStatus

SOURCE_MODEL_DISCOVERY = "model-discovery"
ENTRY_DISCOVERY_FAILED = "discovery-failed"


def _candidate_from_observed(observed, scenario: Scenario) -> CandidateRoute:
    return CandidateRoute(
        layers=tuple(
            RouteLayer(segment.layer, segment.tools)
            for segment in observed.segments
        ),
        capabilities=frozenset(scenario.expected_capabilities),
    )


async def discover_seeds(
    topology: Topology,
    suite: ScenarioSuite,
    *,
    router_factory: Callable[[Scenario], object],
    replay_trials: int = 1,
    discovery_trials: int = 1,
    evaluator=None,
) -> SeedsPayload:
    """Discover per-scenario chains via a router, freeze replay-verified ones.

    ``router_factory(scenario)`` builds the router for one scenario's
    discovery run (e.g. an :class:`LLMRouter` bound to the local Ollama, or
    a :class:`ScenarioScriptedRouter` for offline determinism). Success
    criterion mirrors batch A: the discovery trial must COMPLETE with a
    non-empty observed route covering the expected capabilities — and, when
    an evaluator is supplied, pass evaluation — and the chain must then
    replay deterministically.
    """
    from ..core.errors import SeedExportError

    if discovery_trials < 1:
        raise SeedExportError("discovery_trials must be a positive integer")
    if replay_trials < 1:
        raise SeedExportError("replay_trials must be a positive integer")

    entries: list[SeedEntry] = []
    for scenario in suite.scenarios:
        router = router_factory(scenario)
        single = ScenarioSuite(
            name="seed-discovery", version="1", scenarios=(scenario,)
        )
        runner = SlowRegressionRunner(
            topology=topology,
            evaluator=evaluator if evaluator is not None else StructuredEvaluator({}),
            router=router,
            trials_per_scenario=discovery_trials,
            topology_version="seed-discovery",
            router_config_id="model-discovery",
        )
        outcome = await runner.run(single)

        discovered: CandidateRoute | None = None
        failure_reason = ""
        for result in outcome.results:
            if result.execution_status is not TrialExecutionStatus.COMPLETED:
                detail = (
                    result.failure_category.value
                    if result.failure_category
                    else result.execution_status.value
                )
                failure_reason = f"discovery {result.trial.id}: {detail}"
                continue
            if result.route is None:
                failure_reason = (
                    f"discovery {result.trial.id}: no observed route"
                )
                continue
            if evaluator is not None and (
                result.evaluation is None or not result.evaluation.success
            ):
                failure_reason = (
                    f"discovery {result.trial.id}: evaluation failed"
                )
                continue
            discovered = _candidate_from_observed(result.route, scenario)
            break

        if discovered is None:
            entries.append(
                SeedEntry(
                    scenario_id=scenario.id,
                    status=ENTRY_DISCOVERY_FAILED,
                    attempts=1,
                    reason=failure_reason or "discovery produced no results",
                )
            )
            continue

        ok, reason = await replay_verified(
            topology, scenario, discovered, replay_trials, evaluator
        )
        if not ok:
            # the proposed chain travels with the entry: reviewers see what
            # the model actually picked even when verification refuses it
            entries.append(
                SeedEntry(
                    scenario_id=scenario.id,
                    status=ENTRY_REPLAY_FAILED,
                    attempts=1,
                    reason=reason,
                    route=discovered,
                )
            )
            continue
        entries.append(
            SeedEntry(
                scenario_id=scenario.id,
                status=ENTRY_FROZEN,
                attempts=1,
                reason="",
                route=discovered,
            )
        )

    return build_seed_payload(
        topology, suite, source=SOURCE_MODEL_DISCOVERY, entries=entries
    )
