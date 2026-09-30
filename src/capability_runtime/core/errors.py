class TopologyFrameworkError(Exception):
    """Base class for public framework errors."""


class RegistrationError(TopologyFrameworkError):
    pass


class InvalidCapabilityError(RegistrationError):
    pass


class DuplicateLayerError(RegistrationError):
    pass


class DuplicateToolError(RegistrationError):
    pass


class LayerNotFoundError(TopologyFrameworkError):
    pass


class ToolNotFoundError(TopologyFrameworkError):
    pass


class TopologyBuildError(TopologyFrameworkError):
    pass


class InvalidTopologyReferenceError(TopologyBuildError):
    pass


class RouteValidationError(TopologyFrameworkError):
    pass


class ScenarioError(TopologyFrameworkError):
    pass


class ScenarioLoadError(ScenarioError):
    pass


class ScenarioValidationError(ScenarioError):
    pass


class CoverageAnalyzerError(TopologyFrameworkError):
    pass


class RouteSearchError(TopologyFrameworkError):
    pass


class FastRegressionError(TopologyFrameworkError):
    pass


class BaselineError(FastRegressionError):
    pass


class BaselineSaveError(BaselineError):
    pass


class BaselineLoadError(BaselineError):
    pass


class CapabilityResolutionError(TopologyFrameworkError):
    pass


class SlowRegressionError(TopologyFrameworkError):
    pass


class FixtureError(SlowRegressionError):
    pass


class FixtureSetupError(FixtureError):
    pass


class FixtureResetError(FixtureError):
    pass


class FixtureTeardownError(FixtureError):
    pass


class ExecutionError(SlowRegressionError):
    pass


class ToolExecutionError(ExecutionError):
    pass


class SchemaMismatchError(ExecutionError):
    """Tool argument resolution failed (missing / type-mismatched input)."""


class TimeoutExecutionError(ExecutionError):
    """A tool invocation exceeded its configured timeout."""


class LayerExecutionError(ExecutionError):
    """Every selected tool of a layer failed.

    Carries the layer's tool-execution records so the trace still shows — and
    bills — what was attempted before the trial stopped.
    """

    def __init__(self, message: str, executions: tuple = ()) -> None:
        super().__init__(message)
        self.executions = tuple(executions)


class RoutingError(SlowRegressionError):
    pass


class InvalidRoutingDecisionError(RoutingError):
    pass


class InvalidToolSelectionError(RoutingError):
    def __init__(self, message: str, *, unknown_tools: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.unknown_tools = tuple(unknown_tools)


class EvaluationError(SlowRegressionError):
    pass


class TraceSerializationError(SlowRegressionError):
    pass


class OptimizationError(TopologyFrameworkError):
    pass


class EvidenceError(OptimizationError):
    pass


class TopologyPatchError(OptimizationError):
    pass


class CounterfactualError(OptimizationError):
    pass


class ProbeError(OptimizationError):
    pass


class PruningError(OptimizationError):
    pass


class ValidationGateError(OptimizationError):
    pass


class TopologyVersioningError(OptimizationError):
    pass


class RankingError(TopologyFrameworkError):
    """Phase 5 route ranking failure root (profile / config)."""


class RouteProfileError(RankingError):
    """Trace rows are inconsistent: mixed versions or unreadable payloads."""


class RankingConfigError(RankingError):
    """RankConfig / TierConfig parameters are invalid."""


class OnlineRoutingError(TopologyFrameworkError):
    """Phase 6 online routing failure root (catalog / selection)."""


class RouteCatalogError(OnlineRoutingError):
    """Ranking/topology mismatch or an un-reconstructable route structure."""


class RouteSelectionError(OnlineRoutingError):
    """No ranked candidate satisfies the request's category/tier preference."""


class MeteringError(TopologyFrameworkError):
    """Resource-handle metering failure root (resource-metering milestone)."""


class ResourceHandleError(MeteringError):
    """A resource handle was constructed or used incorrectly."""


class MeteringContextError(MeteringError):
    """Metering was recorded outside a tool-call context (no collector)."""


class CompositeError(TopologyFrameworkError):
    """Composite-node failure root (composite-nodes milestone)."""


class CompositeSpecError(CompositeError):
    """Invalid composite declaration (route/budget/depth/self-reference)."""


class CompositeExecutionError(CompositeError):
    """Runtime failure inside a composite (stop condition unmet, etc.)."""
