"""Online routing: serve requests from learned routes (phase6)."""

from .balancer import LoadBalancer, RoundRobinBalancer
from .catalog import (
    RouteCatalog,
    RouteEntry,
    RouteSegment,
    build_catalog,
    parse_canonical,
)
from .fallback import FallbackStep, build_fallback_chain
from .runtime import OnlineResult, OnlineRuntime, OnlineStatus
from .selection import (
    OnlineConfig,
    OnlineRequest,
    candidate_groups,
    resolve_tier_order,
)
from .telemetry import (
    OnlineRecord,
    OnlineTelemetry,
    RouteUsageStats,
    online_results_to_trials,
    record_of,
)

__all__ = [
    "FallbackStep",
    "LoadBalancer",
    "OnlineConfig",
    "OnlineRecord",
    "OnlineRequest",
    "OnlineResult",
    "OnlineRuntime",
    "OnlineStatus",
    "OnlineTelemetry",
    "RouteCatalog",
    "RouteEntry",
    "RouteSegment",
    "RouteUsageStats",
    "RoundRobinBalancer",
    "build_catalog",
    "build_fallback_chain",
    "candidate_groups",
    "online_results_to_trials",
    "parse_canonical",
    "record_of",
    "resolve_tier_order",
]
