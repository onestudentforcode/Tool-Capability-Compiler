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
