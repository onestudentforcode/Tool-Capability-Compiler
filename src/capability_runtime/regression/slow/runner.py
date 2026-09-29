from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from ...core.errors import (
    ExecutionError,
    FixtureError,
    InvalidRoutingDecisionError,
    InvalidToolSelectionError,
    LayerExecutionError,
    RoutingError,
)
from ...core.failure import TrialFailureCategory
from ...core.metrics import TokenUsage
from ...evaluation.base import EvaluationResult, Evaluator, FinalResult
from ...execution.context import ExecutionContext, ExecutionEnvironment
from ...execution.executor import LayerExecutor
from ...execution.state import ExecutionState
from ...fixtures.base import FixtureManager
from ...regression.candidate_route import CandidateRoute
from ...router.filtering import TopologyFilter
from ...router.models import (
    ExpansionPlan,
    RoutingAction,
    RoutingContext,
    RoutingDecision,
    ToolSummary,
    build_expansion_plan,
    validate_decision,
)
from ...router.protocol import LayerRouter
from ...scenario.models import Scenario, ScenarioSuite
from ...topology.models import Topology
from .route import ObservedRoute, extract_observed_route
from .trace import ExecutionTrace, LayerExecution
from .trial import Trial, TrialExecutionStatus, TrialResult


@dataclass(frozen=True, slots=True)
class SlowRunOutcome:
    """Aggregate of all trials, plus scenarios that fell back to free."""

    results: tuple[TrialResult, ...]
    seed_missing_scenarios: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "seed_missing_scenarios", tuple(sorted(set(self.seed_missing_scenarios)))
        )


class SlowRegressionRunner:
    """Orchestrates Scenario x N Trials: fixture -> execute -> evaluate.

    With a fast CandidateRoute seed per scenario, each layer's exploration is an
    ExpansionPlan (seed baseline plus sibling variants); without one the
    scenario falls back to free exploration (all reachable tools, capped) and is
    flagged as seed_missing.
    """

    def __init__(
        self,
        *,
        topology: Topology,
        evaluator: Evaluator,
        fixture_manager: FixtureManager | None = None,
        seeds: Mapping[str, CandidateRoute] | None = None,
        trials_per_scenario: int = 5,
        max_concurrency: int = 1,
        max_tools_per_layer: int = 3,
        topology_version: str = "1.0",
        environment: ExecutionEnvironment = ExecutionEnvironment.SANDBOX,
        router_config_id: str = "basefast",
        router: LayerRouter | None = None,
        per_tool_timeout_seconds: float | None = None,
    ) -> None:
        if trials_per_scenario < 1:
            raise ExecutionError("trials_per_scenario must be positive")
        if per_tool_timeout_seconds is not None and (
            isinstance(per_tool_timeout_seconds, bool) or per_tool_timeout_seconds <= 0
        ):
            raise ExecutionError("per_tool_timeout_seconds must be positive or None")
        self._topology = topology
        self._evaluator = evaluator
        self._fixture_manager = fixture_manager
        self._seeds = dict(seeds) if seeds else {}
        self._trials = trials_per_scenario
        self._max_concurrency = max_concurrency
        self._max_tools = max_tools_per_layer
        self._topology_version = topology_version
        self._environment = environment
        self._router_config_id = router_config_id
        self._router = router
        self._timeout = per_tool_timeout_seconds
        self._filter = TopologyFilter(topology)

    async def run(self, suite: ScenarioSuite) -> SlowRunOutcome:
        results: list[TrialResult] = []
        seed_missing: set[str] = set()
        for scenario in suite.scenarios:
            seed = self._seeds.get(scenario.id)
            if seed is None:
                seed_missing.add(scenario.id)
            for index in range(self._trials):
                trial = Trial(
                    id=f"{scenario.id}#{index:03d}",
                    scenario_id=scenario.id,
                    trial_index=index,
                    topology_version=self._topology_version,
                    scenario_suite_version=suite.version,
                    router_config_id=self._router_config_id,
                )
                results.append(await self._run_one(scenario, trial, seed))
        return SlowRunOutcome(
            results=tuple(results),
            seed_missing_scenarios=tuple(seed_missing),
        )

    async def _run_one(
        self, scenario: Scenario, trial: Trial, seed: CandidateRoute | None
    ) -> TrialResult:
        start_wall = time.perf_counter()
        state = ExecutionState(query=scenario.query)
        status = TrialExecutionStatus.COMPLETED
        failure_category: TrialFailureCategory | None = None

        if self._fixture_manager is not None:
            try:
                state.scenario_inputs = await self._fixture_manager.setup(scenario, trial)
            except FixtureError:
                return self._finalize(
                    trial,
                    TrialExecutionStatus.FIXTURE_ERROR,
                    start_wall,
                    route=None,
                    failure_category=TrialFailureCategory.FIXTURE_ERROR,
                )

        trace = ExecutionTrace(
            trial_id=trial.id,
            scenario_id=scenario.id,
            topology_version=self._topology_version,
            started_at=datetime.now(),
        )
        previous_selected: tuple[str, ...] = ()
        try:
            for layer in self._topology.layers():
                layer_name = layer.name
                available = self._filter.available_tools(layer_name, previous_selected, state)
                if self._router is not None:
                    decision = await self._route_with_router(
                        layer_name, available, state, trace
                    )
                    selection = decision.selected_tools
                else:
                    selection = self._select(available, seed, layer_name, trial.trial_index)
                    decision = RoutingDecision(
                        action=(
                            RoutingAction.EXECUTE if selection else RoutingAction.FINISH
                        ),
                        selected_tools=selection,
                        reason="basefast-expansion" if seed else "free",
                    )
                started_at = datetime.now()
                if not selection:
                    trace.add_layer(
                        LayerExecution(
                            layer=layer_name,
                            available_tools=available,
                            selected_tools=(),
                            routing_decision=decision,
                            tool_executions=(),
                            started_at=started_at,
                            ended_at=started_at,
                        )
                    )
                    break
                executor = LayerExecutor(
                    context=ExecutionContext(
                        environment=self._environment,
                        max_concurrency=self._max_concurrency,
                        per_tool_timeout_seconds=self._timeout,
                    )
                )
                try:
                    tool_executions = await executor.run(
                        [self._topology.node(name) for name in selection], state
                    )
                except LayerExecutionError as exc:
                    # Whole-layer failure still records what was attempted —
                    # and billed — before the trial stops (phase3 §42).
                    trace.add_layer(
                        LayerExecution(
                            layer=layer_name,
                            available_tools=available,
                            selected_tools=selection,
                            routing_decision=decision,
                            tool_executions=exc.executions,
                            started_at=started_at,
                            ended_at=datetime.now(),
                        )
                    )
                    raise
                ended_at = datetime.now()
                trace.add_layer(
                    LayerExecution(
                        layer=layer_name,
                        available_tools=available,
                        selected_tools=selection,
                        routing_decision=decision,
                        tool_executions=tool_executions,
                        started_at=started_at,
                        ended_at=ended_at,
                    )
                )
                previous_selected = selection
        except InvalidToolSelectionError as exc:
            status = TrialExecutionStatus.ROUTING_ERROR
            failure_category = self._selection_category(exc)
        except InvalidRoutingDecisionError:
            status = TrialExecutionStatus.ROUTING_ERROR
            failure_category = TrialFailureCategory.TOOL_SELECTION_ERROR
        except RoutingError:
            status = TrialExecutionStatus.ROUTING_ERROR
        except LayerExecutionError:
            status = TrialExecutionStatus.LAYER_ERROR
        except ExecutionError:
            status = TrialExecutionStatus.TOOL_ERROR

        route = self._safe_route(trace)

        if self._fixture_manager is not None:
            try:
                await self._fixture_manager.teardown(scenario, trial)
            except FixtureError:
                if status is TrialExecutionStatus.COMPLETED:
                    status = TrialExecutionStatus.FIXTURE_ERROR
                    failure_category = TrialFailureCategory.FIXTURE_ERROR

        evaluation = None
        if status is TrialExecutionStatus.COMPLETED:
            try:
                evaluation = await self._evaluate(scenario, state, trace)
            except Exception as exc:  # noqa: BLE001 - evaluator failure != business failure
                status = TrialExecutionStatus.EVALUATION_ERROR
                failure_category = TrialFailureCategory.EVALUATION_ERROR
                evaluation = None
            else:
                if evaluation is not None and not evaluation.success:
                    failure_category = evaluation.category

        return self._finalize(
            trial,
            status,
            start_wall,
            trace=trace,
            route=route,
            evaluation=evaluation,
            failure_category=failure_category,
        )

    def _selection_category(self, exc: InvalidToolSelectionError) -> TrialFailureCategory:
        """MISSING_TOOL when a picked tool does not exist; else TOOL_SELECTION_ERROR."""
        if exc.unknown_tools and not set(exc.unknown_tools).issubset(
            set(self._topology.nodes())
        ):
            return TrialFailureCategory.MISSING_TOOL
        return TrialFailureCategory.TOOL_SELECTION_ERROR

    async def _route_with_router(
        self,
        layer_name: str,
        available: tuple[str, ...],
        state: ExecutionState,
        trace: ExecutionTrace,
    ) -> RoutingDecision:
        summaries = tuple(
            ToolSummary.from_tool_node(self._topology.node(name)) for name in available
        )
        context = RoutingContext(
            query=state.query,
            current_layer=layer_name,
            available_tools=summaries,
            topology_version=self._topology_version,
            state_summary=state,
            previous_layers=trace.layers,
        )
        decision = await self._router.route(context)
        validate_decision(decision, available, self._max_tools)
        return decision

    def _select(
        self,
        available: tuple[str, ...],
        seed: CandidateRoute | None,
        layer_name: str,
        trial_index: int,
    ) -> tuple[str, ...]:
        if seed is not None:
            plan = build_expansion_plan(
                layer=layer_name,
                available_tools=available,
                seed_tools=self._seed_tools_for_layer(seed, layer_name),
                max_tools_per_layer=self._max_tools,
            )
            if not plan.candidate_sets:
                return ()
            return plan.candidate_sets[trial_index % len(plan.candidate_sets)]
        return tuple(sorted(available))[: self._max_tools]

    @staticmethod
    def _seed_tools_for_layer(seed: CandidateRoute, layer_name: str) -> tuple[str, ...]:
        return next(
            (entry.tools for entry in seed.layers if entry.layer == layer_name),
            (),
        )

    @staticmethod
    def _safe_route(trace: ExecutionTrace) -> ObservedRoute | None:
        if not any(layer.tool_executions for layer in trace.layers):
            return None
        try:
            return extract_observed_route(trace)
        except ExecutionError:
            return None

    async def _evaluate(
        self, scenario: Scenario, state: ExecutionState, trace: ExecutionTrace
    ) -> EvaluationResult:
        return await self._evaluator.evaluate(
            scenario,
            FinalResult(response=state.final_response, state_snapshot=state),
            trace,
        )

    def _finalize(
        self,
        trial: Trial,
        status: TrialExecutionStatus,
        start_wall: float,
        *,
        trace: ExecutionTrace | None = None,
        route: ObservedRoute | None = None,
        evaluation: EvaluationResult | None = None,
        failure_category: TrialFailureCategory | None = None,
    ) -> TrialResult:
        if trace is None:
            trace = ExecutionTrace(
                trial_id=trial.id,
                scenario_id=trial.scenario_id,
                topology_version=trial.topology_version,
                started_at=datetime.now(),
            )
        tool_cost = self._sum_costs(
            execution.cost for layer in trace.layers for execution in layer.tool_executions
        )
        routing_cost = self._sum_costs(
            layer.routing_decision.routing_cost for layer in trace.layers
        )
        # execution cost = tool + routing; judge cost stays separate (phase3 §80)
        if tool_cost is None and routing_cost is None:
            execution_cost = None
        else:
            execution_cost = (tool_cost or 0.0) + (routing_cost or 0.0)
        return TrialResult(
            trial=trial,
            execution_status=status,
            route=route,
            trace=trace,
            evaluation=evaluation,
            latency_ms=(time.perf_counter() - start_wall) * 1000.0,
            token_usage=self._aggregate_tokens(trace),
            cost=execution_cost,
            tool_cost=tool_cost,
            routing_cost=routing_cost,
            evaluation_cost=evaluation.cost if evaluation is not None else None,
            failure_category=failure_category,
        )

    @staticmethod
    def _sum_costs(values) -> float | None:
        """Sum known costs; all-None stays None so absence is never faked as 0."""
        known = [value for value in values if value is not None]
        return sum(known) if known else None

    @staticmethod
    def _aggregate_tokens(trace: ExecutionTrace) -> TokenUsage:
        total_input = 0
        total_output = 0
        for layer in trace.layers:
            usage = layer.routing_decision.token_usage
            if usage is not None:
                total_input += usage.input_tokens
                total_output += usage.output_tokens
            for execution in layer.tool_executions:
                if execution.token_usage is not None:
                    total_input += execution.token_usage.input_tokens
                    total_output += execution.token_usage.output_tokens
        return TokenUsage(input_tokens=total_input, output_tokens=total_output)