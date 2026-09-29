"""OnlineRuntime: route-following execution with bounded fallback (phase6 §8-10).

The route IS the decision — no router, no exploration. A request resolves to
ordered same-tier candidate groups, the balancer picks one, and the runtime
executes it layer by layer through the Phase 3 engine. Whole-layer failure
moves to the next retained candidate until the bounded chain is exhausted;
every attempt (successful or not) is billed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..core.errors import RouteSelectionError
from ..core.metrics import TokenUsage
from ..execution.context import ExecutionContext, ExecutionEnvironment
from ..execution.executor import LayerExecutor, LayerExecutionError
from ..execution.state import ExecutionState
from ..regression.slow.trace import ExecutionTrace, LayerExecution
from ..router.models import RoutingAction, RoutingDecision
from .balancer import LoadBalancer, RoundRobinBalancer
from .catalog import RouteCatalog, RouteEntry
from .fallback import FallbackStep, build_fallback_chain
from .selection import OnlineConfig, OnlineRequest, candidate_groups


class OnlineStatus(str, Enum):
    SERVED = "served"
    ROUTE_FAILED = "route_failed"
    NO_CANDIDATE = "no_candidate"


@dataclass(frozen=True, slots=True)
class OnlineResult:
    request_id: str
    status: OnlineStatus
    selected_route_id: str | None
    selection_reason: str
    fallback_chain: tuple[FallbackStep, ...]
    state: ExecutionState | None
    latency_ms: float
    cost: float | None
    token_usage: TokenUsage
    trace: ExecutionTrace | None
    category: str | None
    tier_preference: str | None

    @property
    def fallback_depth(self) -> int:
        return len(self.fallback_chain)


class OnlineRuntime:
    """Serves requests strictly from the catalog; never mutates it."""

    def __init__(
        self,
        *,
        catalog: RouteCatalog,
        config: OnlineConfig | None = None,
        balancer: LoadBalancer | None = None,
        environment: ExecutionEnvironment = ExecutionEnvironment.SANDBOX,
        max_concurrency: int = 1,
        per_tool_timeout_seconds: float | None = None,
    ) -> None:
        self._catalog = catalog
        self._config = config or OnlineConfig()
        self._balancer = balancer or RoundRobinBalancer()
        self._environment = environment
        self._max_concurrency = max_concurrency
        self._timeout = per_tool_timeout_seconds
        self._counter = 0

    @property
    def catalog(self) -> RouteCatalog:
        return self._catalog

    async def serve(self, request: OnlineRequest) -> OnlineResult:
        self._counter += 1
        request_id = request.request_id or f"req-{self._counter:04d}"
        started = time.perf_counter()

        try:
            groups = candidate_groups(self._catalog, request, self._config)
        except RouteSelectionError as exc:
            return self._finish(
                request,
                request_id,
                OnlineStatus.NO_CANDIDATE,
                selected_route_id=None,
                selection_reason=str(exc),
                fallback_chain=(),
                state=None,
                trace=None,
                started=started,
                metering=(None, TokenUsage()),
            )

        initial_tier, initial_group = groups[0]
        initial = self._balancer.pick(initial_group)
        tier_source = "request" if request.tier else "config"
        reason = (
            f"tier={initial_tier} ({tier_source}), "
            f"{len(initial_group)} candidates, round-robin -> {initial.route_id}"
        )
        chain = build_fallback_chain(
            groups, initial, max_fallbacks=self._config.max_fallbacks
        )
        attempts: list[RouteEntry] = [initial, *chain]

        fallback_chain: list[FallbackStep] = []
        costs: list[float] = []
        tokens = TokenUsage()
        last_trace: ExecutionTrace | None = None
        last_state: ExecutionState | None = None
        last_route = initial
        served = False
        failure_cause = ""

        for index, entry in enumerate(attempts):
            ok, trace, state, cause = await self._execute_entry(
                entry, request, request_id
            )
            last_trace = trace
            last_state = state
            last_route = entry
            for layer in trace.layers:
                for execution in layer.tool_executions:
                    if execution.cost is not None:
                        costs.append(execution.cost)
                    if execution.token_usage is not None:
                        tokens = TokenUsage(
                            input_tokens=tokens.input_tokens
                            + execution.token_usage.input_tokens,
                            output_tokens=tokens.output_tokens
                            + execution.token_usage.output_tokens,
                        )
            if ok:
                served = True
                break
            failure_cause = cause
            if index + 1 < len(attempts):
                fallback_chain.append(
                    FallbackStep(
                        from_route_id=entry.route_id,
                        to_route_id=attempts[index + 1].route_id,
                        cause=cause,
                    )
                )

        status = OnlineStatus.SERVED if served else OnlineStatus.ROUTE_FAILED
        final_reason = (
            reason
            if served
            else f"{reason}; exhausted fallback chain: {failure_cause}"
        )
        return self._finish(
            request,
            request_id,
            status,
            selected_route_id=last_route.route_id,
            selection_reason=final_reason,
            fallback_chain=tuple(fallback_chain),
            state=last_state if served else None,
            trace=last_trace,
            started=started,
            metering=(sum(costs) if costs else None, tokens),
        )

    async def _execute_entry(
        self, entry: RouteEntry, request: OnlineRequest, request_id: str
    ) -> tuple[bool, ExecutionTrace, ExecutionState, str]:
        state = ExecutionState(query=request.query)
        trace = ExecutionTrace(
            trial_id=request_id,
            scenario_id=request.category or "online",
            topology_version=self._catalog.topology_version,
        )
        executor = LayerExecutor(
            context=ExecutionContext(
                environment=self._environment,
                max_concurrency=self._max_concurrency,
                per_tool_timeout_seconds=self._timeout,
            )
        )
        for segment in entry.segments:
            decision = RoutingDecision(
                action=RoutingAction.EXECUTE,
                selected_tools=segment.tools,
                reason=f"online:route-following:{entry.route_id}",
            )
            started_at = datetime.now()
            try:
                executions = await executor.run(
                    [self._catalog.topology.node(tool) for tool in segment.tools],
                    state,
                )
            except LayerExecutionError as exc:
                # whole-layer failure: record the billed attempts, stop here
                trace.add_layer(
                    LayerExecution(
                        layer=segment.layer,
                        available_tools=segment.tools,
                        selected_tools=segment.tools,
                        routing_decision=decision,
                        tool_executions=exc.executions,
                        started_at=started_at,
                        ended_at=datetime.now(),
                    )
                )
                return (
                    False,
                    trace,
                    state,
                    f"route {entry.route_id}: layer {segment.layer!r} failed",
                )
            trace.add_layer(
                LayerExecution(
                    layer=segment.layer,
                    available_tools=segment.tools,
                    selected_tools=segment.tools,
                    routing_decision=decision,
                    tool_executions=executions,
                    started_at=started_at,
                    ended_at=datetime.now(),
                )
            )
        return True, trace, state, ""

    def _finish(
        self,
        request: OnlineRequest,
        request_id: str,
        status: OnlineStatus,
        *,
        selected_route_id,
        selection_reason,
        fallback_chain,
        state,
        trace,
        started,
        metering,
    ) -> OnlineResult:
        cost, tokens = metering
        return OnlineResult(
            request_id=request_id,
            status=status,
            selected_route_id=selected_route_id,
            selection_reason=selection_reason,
            fallback_chain=fallback_chain,
            state=state,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            cost=cost,
            token_usage=tokens,
            trace=trace,
            category=request.category,
            tier_preference=request.tier,
        )
