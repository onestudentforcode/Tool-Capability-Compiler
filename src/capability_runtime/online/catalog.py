"""RouteCatalog: the version-gated serving view of ranked routes (phase6 §5).

Consumes a Phase 5 ranking JSON payload plus the executable Active Topology.
Only RANKED routes become entries; every route structure is rebuilt from its
``canonical`` form and checked against the topology — a mismatch fails closed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..core.errors import RouteCatalogError
from ..topology.models import Topology

_CANONICAL_SEGMENT = re.compile(r"^(?P<layer>[^:\[\]]+):\[(?P<tools>[^\[\]]*)\]$")


@dataclass(frozen=True, slots=True)
class RouteSegment:
    layer: str
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RouteEntry:
    """One servable route distilled from the ranking evidence."""

    route_id: str
    canonical: str
    segments: tuple[RouteSegment, ...]
    tiers: tuple[str, ...]              # fast / balanced / quality (可为空)
    pareto: bool
    categories: tuple[str, ...]
    trial_count: int
    success_rate: float
    success_confidence_interval: tuple[float, float]
    latency_median: float | None
    cost_mean: float | None
    quality_mean: float | None


def parse_canonical(canonical: str) -> tuple[RouteSegment, ...]:
    """Rebuild segments from ``layer:[a,b]`` lines; strict and deterministic."""
    segments: list[RouteSegment] = []
    for line in canonical.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _CANONICAL_SEGMENT.match(line)
        if match is None:
            raise RouteCatalogError(
                f"canonical segment {line!r} does not match 'layer:[tools]'"
            )
        tools_raw = match.group("tools").strip()
        tools = tuple(part.strip() for part in tools_raw.split(",")) if tools_raw else ()
        if not tools or any(not tool for tool in tools):
            raise RouteCatalogError(
                f"canonical segment {line!r} has no tools"
            )
        segments.append(RouteSegment(layer=match.group("layer").strip(), tools=tools))
    if not segments:
        raise RouteCatalogError("canonical form contains no segments")
    return tuple(segments)


@dataclass(frozen=True, slots=True)
class RouteCatalog:
    """Active Topology + the ranked routes allowed to serve requests."""

    topology: Topology
    topology_version: str
    router_config_id: str
    entries: tuple[RouteEntry, ...]

    def entry_of(self, route_id: str) -> RouteEntry:
        for entry in self.entries:
            if entry.route_id == route_id:
                return entry
        raise RouteCatalogError(f"route {route_id!r} is not in the catalog")

    def candidates(
        self,
        *,
        category: str | None = None,
        tier: str | None = None,
    ) -> tuple[RouteEntry, ...]:
        matched = [
            entry
            for entry in self.entries
            if (category is None or category in entry.categories)
            and (tier is None or tier in entry.tiers)
        ]
        return tuple(sorted(matched, key=lambda item: item.route_id))


def build_catalog(
    topology: Topology,
    ranking: Mapping[str, Any],
    *,
    topology_version: str,
) -> RouteCatalog:
    """Gate versions, rebuild every ranked route, fail closed on any mismatch."""
    ranking_version = str(ranking.get("topology_version", ""))
    if not ranking_version or ranking_version != topology_version:
        raise RouteCatalogError(
            "ranking/topology version mismatch: "
            f"ranking={ranking_version!r} active={topology_version!r}"
        )
    router_config_id = str(ranking.get("router_config_id", ""))

    ranked = {
        str(item.get("route_id"))
        for item in ranking.get("eligibility", ())
        if item.get("status") == "ranked"
    }
    frontier = set(ranking.get("pareto", {}).get("frontier", ()))
    tiers_by_route: dict[str, tuple[str, ...]] = {}
    for item in ranking.get("tier_assignments", ()):
        tiers_by_route[str(item.get("route_id"))] = tuple(
            str(tier) for tier in item.get("tiers", ())
        )

    entries: list[RouteEntry] = []
    for profile in ranking.get("profiles", ()):
        route_id = str(profile.get("route_id", ""))
        if route_id not in ranked:
            continue
        canonical = str(profile.get("canonical", ""))
        try:
            segments = parse_canonical(canonical)
        except RouteCatalogError as exc:
            raise RouteCatalogError(
                f"route {route_id!r}: {exc}"
            ) from exc
        _verify_against_topology(route_id, segments, topology)
        ci = profile.get("success_confidence_interval") or (0.0, 1.0)
        entries.append(
            RouteEntry(
                route_id=route_id,
                canonical=canonical.replace("\n", " "),
                segments=segments,
                tiers=tiers_by_route.get(route_id, ()),
                pareto=route_id in frontier,
                categories=tuple(
                    str(category) for category in profile.get("categories", ())
                ),
                trial_count=int(profile.get("trial_count", 0)),
                success_rate=float(profile.get("business_success_rate", 0.0)),
                success_confidence_interval=(float(ci[0]), float(ci[1])),
                latency_median=_optional_float(profile.get("latency_median")),
                cost_mean=_optional_float(profile.get("cost_mean")),
                quality_mean=_optional_float(profile.get("quality_mean")),
            )
        )
    if not entries:
        raise RouteCatalogError(
            "ranking contains no RANKED routes; nothing to serve"
        )
    return RouteCatalog(
        topology=topology,
        topology_version=topology_version,
        router_config_id=router_config_id,
        entries=tuple(sorted(entries, key=lambda item: item.route_id)),
    )


def _verify_against_topology(
    route_id: str, segments: tuple[RouteSegment, ...], topology: Topology
) -> None:
    layers = {layer.name for layer in topology.layers()}
    for segment in segments:
        if segment.layer not in layers:
            raise RouteCatalogError(
                f"route {route_id!r} references unknown layer {segment.layer!r}"
            )
        for tool in segment.tools:
            if tool not in topology.nodes():
                raise RouteCatalogError(
                    f"route {route_id!r} references unknown tool {tool!r}"
                )
            if topology.node(tool).spec.layer != segment.layer:
                raise RouteCatalogError(
                    f"route {route_id!r}: tool {tool!r} is not in layer "
                    f"{segment.layer!r}"
                )


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
