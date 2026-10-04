"""Composite nodes: final capabilities as macro nodes (composite-nodes milestone)."""

from .evidence import InnerObservation, flatten_composite_results
from .runtime import CompositeRuntime
from .spec import (
    MAX_COMPOSITE_DEPTH,
    CompositeSpec,
    build_composite_node,
    composite_depth_by_name,
)

__all__ = [
    "MAX_COMPOSITE_DEPTH",
    "CompositeRuntime",
    "CompositeSpec",
    "InnerObservation",
    "build_composite_node",
    "composite_depth_by_name",
    "flatten_composite_results",
]
