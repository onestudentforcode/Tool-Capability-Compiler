"""CompositeRuntime: bounded inner route execution (composite-nodes §5).

Same route each iteration, a persistent inner blackboard, a declared stop
condition and a hard budget — no hidden control flow, no recursion stack.
Metering of every inner execution rolls up onto the outer collector; every
iteration's layer records travel out via the generic detail channel, in a
finally block, so failed composites keep both their detail and their bill.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..core.errors import CompositeExecutionError
from ..core.metrics import TokenUsage
from ..execution.context import ExecutionContext, ExecutionEnvironment
from ..execution.executor import LayerExecutor, LayerExecutionError
from ..execution.state import ArtifactValue, ExecutionState
from ..regression.slow.trace import LayerExecution
from ..resources.metering import attach_detail, current_collector
from ..router.models import RoutingAction, RoutingDecision
from .spec import CompositeSpec, _snake


class CompositeRuntime:
    """The handler of a composite node: run(*consumed_values)."""

    def __init__(self, spec: CompositeSpec) -> None:
        self._spec = spec

    async def run(self, *args: Any) -> Any:
        spec = self._spec
        if len(args) != len(spec.consumes):
            raise CompositeExecutionError(
                f"composite {spec.name!r}: expected {len(spec.consumes)} "
                f"inputs, got {len(args)}"
            )
        state = ExecutionState(query=f"composite:{spec.name}")
        for value in args:
            slot = _snake(type(value).__name__)
            state.add_artifact(
                slot,
                ArtifactValue(value=value, source_tool="<outer>", layer=spec.layer),
            )

        iterations: list[list[LayerExecution]] = []
        failure: Exception | None = None
        try:
            for iteration in range(spec.max_iterations):
                layers: list[LayerExecution] = []
                iterations.append(layers)
                await self._execute_iteration(state, iteration, layers)
                if self._stop_satisfied(state):
                    return self._extract_outputs(state)
            raise CompositeExecutionError(
                f"composite {spec.name!r}: stop condition "
                f"{list(spec.stop_when)} unmet after "
                f"{spec.max_iterations} iterations"
            )
        except Exception as exc:  # noqa: BLE001 - surface after settling
            failure = exc
            raise
        finally:
            attach_detail(tuple(tuple(layers) for layers in iterations))
            self._rollup(iterations)

    async def _execute_iteration(
        self,
        state: ExecutionState,
        iteration: int,
        layers: list[LayerExecution],
    ) -> None:
        spec = self._spec
        executor = LayerExecutor(
            context=ExecutionContext(environment=ExecutionEnvironment.SANDBOX)
        )
        for group in spec.route:
            tools = tuple(sorted(group))
            decision = RoutingDecision(
                action=RoutingAction.EXECUTE,
                selected_tools=tools,
                reason=f"composite:{spec.name}:iter:{iteration}",
            )
            started_at = datetime.now()
            try:
                executions = await executor.run(
                    [spec.topology.node(tool) for tool in tools], state
                )
            except LayerExecutionError as exc:
                layers.append(
                    LayerExecution(
                        layer=spec.topology.node(tools[0]).spec.layer
                        if tools
                        else "?",
                        available_tools=tools,
                        selected_tools=tools,
                        routing_decision=decision,
                        tool_executions=exc.executions,
                        started_at=started_at,
                        ended_at=datetime.now(),
                    )
                )
                raise
            layers.append(
                LayerExecution(
                    layer=spec.topology.node(tools[0]).spec.layer,
                    available_tools=tools,
                    selected_tools=tools,
                    routing_decision=decision,
                    tool_executions=executions,
                    started_at=started_at,
                    ended_at=datetime.now(),
                )
            )

    def _stop_satisfied(self, state: ExecutionState) -> bool:
        names = set(state.names())
        return all(slot in names for slot in self._spec.stop_when)

    def _extract_outputs(self, state: ExecutionState) -> Any:
        produces = self._spec.produces
        if not produces:
            return None
        picked: list[Any] = []
        for expected in produces:
            found = None
            for name in state.names():
                for artifact in state.get_artifacts(name):
                    if isinstance(artifact.value, expected):
                        found = artifact.value
                        break
                if found is not None:
                    break
            if found is None:
                raise CompositeExecutionError(
                    f"composite {self._spec.name!r}: inner blackboard has no "
                    f"{expected.__name__} to produce"
                )
            picked.append(found)
        return picked[0] if len(picked) == 1 else tuple(picked)

    def _rollup(self, iterations) -> None:
        """Replay inner metering onto the outer collector (best effort).

        Direct invocation outside an executor has no outer collector — skip
        silently there (the sandbox's direct-call tests rely on this).
        """
        try:
            collector = current_collector()
        except Exception:  # noqa: BLE001 - no outer context: nothing to roll onto
            return
        tokens = TokenUsage()
        have_tokens = False
        for layers in iterations:
            for layer in layers:
                for execution in layer.tool_executions:
                    for key, count in (execution.access_counts or {}).items():
                        resource, _, access = key.rpartition("[")
                        collector.record_access(
                            resource, access.rstrip("]"), count=count
                        )
                    if execution.token_usage is not None:
                        tokens = TokenUsage(
                            input_tokens=tokens.input_tokens
                            + execution.token_usage.input_tokens,
                            output_tokens=tokens.output_tokens
                            + execution.token_usage.output_tokens,
                        )
                        have_tokens = True
                    if execution.measured_cost is not None:
                        collector.record_measured_cost(execution.measured_cost)
        if have_tokens:
            collector.record_tokens(tokens)
