from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import AbstractSet, Protocol

from ..core.capability import validate_capability_name
from ..core.errors import CapabilityResolutionError, InvalidCapabilityError


@dataclass(frozen=True, slots=True)
class CapabilityResolution:
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    missing_capability_hints: tuple[str, ...] = ()
    confidence: float = 1.0
    reasoning: str | None = None

    def __post_init__(self) -> None:
        required = self._normalize(self.required, "required")
        optional = self._normalize(self.optional, "optional")
        hints = self._normalize(
            self.missing_capability_hints, "missing_capability_hints"
        )
        overlap = sorted(set(required) & set(optional))
        if overlap:
            raise CapabilityResolutionError(
                "Capabilities cannot be both required and optional: "
                + ", ".join(overlap)
            )
        hinted_existing = sorted(set(hints) & (set(required) | set(optional)))
        if hinted_existing:
            raise CapabilityResolutionError(
                "Missing hints cannot also be resolved capabilities: "
                + ", ".join(hinted_existing)
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise CapabilityResolutionError(
                "Capability resolution confidence must be between 0.0 and 1.0"
            )
        if self.reasoning is not None and not isinstance(self.reasoning, str):
            raise CapabilityResolutionError("Resolution reasoning must be a string or null")
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "optional", optional)
        object.__setattr__(self, "missing_capability_hints", hints)
        object.__setattr__(
            self,
            "reasoning",
            self.reasoning.strip() if self.reasoning is not None else None,
        )

    @staticmethod
    def _normalize(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
        items = tuple(values)
        if len(items) != len(set(items)):
            raise CapabilityResolutionError(
                f"Capability resolution {field_name} contains duplicates"
            )
        try:
            return tuple(sorted(validate_capability_name(item) for item in items))
        except InvalidCapabilityError as exc:
            raise CapabilityResolutionError(
                f"Capability resolution {field_name} contains an invalid capability"
            ) from exc


class CapabilityResolver(Protocol):
    async def resolve(
        self,
        query: str,
        available_capabilities: AbstractSet[str],
    ) -> CapabilityResolution: ...


class FakeCapabilityResolver:
    """Deterministic resolver for tests and local discovery workflows."""

    def __init__(
        self,
        resolutions: Mapping[str, CapabilityResolution],
        *,
        default: CapabilityResolution | None = None,
    ) -> None:
        self._resolutions = dict(resolutions)
        self._default = default
        self.calls: list[str] = []

    async def resolve(
        self,
        query: str,
        available_capabilities: AbstractSet[str],
    ) -> CapabilityResolution:
        if not isinstance(query, str) or not query.strip():
            raise CapabilityResolutionError("Resolver query must be a non-empty string")
        available = frozenset(
            validate_capability_name(item) for item in available_capabilities
        )
        self.calls.append(query)
        resolution = self._resolutions.get(query, self._default)
        if resolution is None:
            raise CapabilityResolutionError(
                f"Fake resolver has no resolution for query: {query}"
            )
        unavailable = sorted(
            (set(resolution.required) | set(resolution.optional)) - available
        )
        if unavailable:
            raise CapabilityResolutionError(
                "Resolver selected unavailable capabilities instead of returning hints: "
                + ", ".join(unavailable)
            )
        invalid_hints = sorted(set(resolution.missing_capability_hints) & available)
        if invalid_hints:
            raise CapabilityResolutionError(
                "Missing capability hints are already available: "
                + ", ".join(invalid_hints)
            )
        return resolution
