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


class LayerExecutionError(ExecutionError):
    pass


class RoutingError(SlowRegressionError):
    pass


class InvalidRoutingDecisionError(RoutingError):
    pass


class InvalidToolSelectionError(RoutingError):
    pass


class EvaluationError(SlowRegressionError):
    pass


class TraceSerializationError(SlowRegressionError):
    pass
