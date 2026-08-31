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
