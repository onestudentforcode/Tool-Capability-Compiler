"""Public API for the layered tool-topology MVP."""

from .core import (
    DuplicateLayerError,
    DuplicateToolError,
    InvalidCapabilityError,
    InvalidTopologyReferenceError,
    Layer,
    LayerNotFoundError,
    RegistrationError,
    RouteSearchError,
    RouteValidationError,
    ScenarioError,
    ScenarioLoadError,
    ScenarioValidationError,
    ToolNode,
    ToolNotFoundError,
    TopologyBuildError,
    TopologyFrameworkError,
)
from .decorators import tool
from .registry import CapabilityRegistry, LayerRegistry, ToolRegistry
from .regression import (
    CoverageAnalyzer,
    CoverageAnalyzerError,
    CoverageResult,
    CoverageStatus,
    CandidateRoute,
    FailureReason,
    RouteSearcher,
)
from .route import RoutePlan
from .scenario import Scenario, ScenarioLoader, ScenarioSuite
from .topology import ToolEdge, Topology, TopologyBuilder, TopologyValidationWarning

__all__ = [
    "CapabilityRegistry",
    "CandidateRoute",
    "CoverageAnalyzer",
    "CoverageAnalyzerError",
    "CoverageResult",
    "CoverageStatus",
    "DuplicateLayerError",
    "DuplicateToolError",
    "FailureReason",
    "InvalidCapabilityError",
    "InvalidTopologyReferenceError",
    "Layer",
    "LayerNotFoundError",
    "LayerRegistry",
    "RegistrationError",
    "RoutePlan",
    "RouteSearcher",
    "RouteSearchError",
    "RouteValidationError",
    "Scenario",
    "ScenarioError",
    "ScenarioLoadError",
    "ScenarioLoader",
    "ScenarioSuite",
    "ScenarioValidationError",
    "ToolEdge",
    "ToolNode",
    "ToolNotFoundError",
    "ToolRegistry",
    "Topology",
    "TopologyBuildError",
    "TopologyBuilder",
    "TopologyFrameworkError",
    "TopologyValidationWarning",
    "tool",
]
