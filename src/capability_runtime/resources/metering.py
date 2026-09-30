"""Metering core: per-tool-call collectors attached via contextvars.

The executor mounts one :class:`MeteringCollector` per tool invocation; every
resource handle used inside that invocation records into it (access counts,
precise tokens, measured cost). The tool body contains no metering code —
metering happens inside handles and settles at the call boundary
(resource-metering §2).

Honesty tiers (resource-metering §1):

    DECLARED    no handle traffic; billing stays at the declared cost_per_call
    MEASURED    handle traffic produced precise tokens / cost / access counts
    ESTIMATED   an explicit estimator produced token numbers (never sold as
                measured)
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..core.errors import MeteringContextError
from ..core.metrics import TokenUsage


class MeteringSource(str, Enum):
    DECLARED = "declared"
    MEASURED = "measured"
    ESTIMATED = "estimated"


@dataclass(frozen=True, slots=True)
class MeteringRecord:
    """One metered resource access (metadata only — never payload content)."""

    resource: str
    access: str
    count: int = 1
    latency_ms: float = 0.0


def _merge_tokens(current: TokenUsage | None, extra: TokenUsage) -> TokenUsage:
    if current is None:
        return extra
    return TokenUsage(
        input_tokens=current.input_tokens + extra.input_tokens,
        output_tokens=current.output_tokens + extra.output_tokens,
    )


class MeteringCollector:
    """The sink for one tool invocation; mounted by ToolExecutor."""

    def __init__(self) -> None:
        self._records: list[MeteringRecord] = []
        self._tokens: TokenUsage | None = None
        self._estimated_tokens = False
        self._measured_cost: float | None = None

    def record_access(
        self,
        resource: str,
        access: str,
        *,
        count: int = 1,
        latency_ms: float = 0.0,
    ) -> None:
        if not resource.strip() or not access.strip():
            raise MeteringContextError(
                "metered access needs a non-empty resource name and access mode"
            )
        self._records.append(
            MeteringRecord(resource=resource, access=access, count=count,
                           latency_ms=latency_ms)
        )

    def record_tokens(self, usage: TokenUsage) -> None:
        self._tokens = _merge_tokens(self._tokens, usage)

    def record_estimated_tokens(self, usage: TokenUsage) -> None:
        self._tokens = _merge_tokens(self._tokens, usage)
        self._estimated_tokens = True

    def record_measured_cost(self, cost: float) -> None:
        self._measured_cost = (self._measured_cost or 0.0) + cost

    @property
    def access_counts(self) -> dict[str, int]:
        merged: dict[str, int] = {}
        for record in self._records:
            key = f"{record.resource}[{record.access}]"
            merged[key] = merged.get(key, 0) + record.count
        return merged

    @property
    def tokens(self) -> TokenUsage | None:
        return self._tokens

    @property
    def measured_cost(self) -> float | None:
        return self._measured_cost

    @property
    def source(self) -> MeteringSource:
        # the weakest claim present wins: mixing estimated tokens with
        # measured traffic must never be sold as fully measured
        if self._estimated_tokens and self._tokens is not None:
            return MeteringSource.ESTIMATED
        if (
            self._records
            or self._measured_cost is not None
            or self._tokens is not None
        ):
            return MeteringSource.MEASURED
        return MeteringSource.DECLARED


_current_collector: contextvars.ContextVar[MeteringCollector | None] = (
    contextvars.ContextVar("metering_collector", default=None)
)


def current_collector() -> MeteringCollector:
    """The collector of the tool invocation we are inside, or an error."""
    collector = _current_collector.get()
    if collector is None:
        raise MeteringContextError(
            "metering recorded outside a tool-call context; resource handles "
            "must be used inside a tool executed by ToolExecutor"
        )
    return collector


def mount_collector() -> contextvars.Token:
    """Executor-side: attach a fresh collector to the current task."""
    return _current_collector.set(MeteringCollector())


def unmount_collector(token: contextvars.Token) -> MeteringCollector:
    """Executor-side: detach and hand back the collector for settlement."""
    collector = _current_collector.get()
    _current_collector.reset(token)
    return collector  # type: ignore[return-value]


def estimate_tokens(text: str) -> int:
    """Rough char-based token estimate — ESTIMATED tier only."""
    return max(1, len(text) // 4)


@dataclass(frozen=True, slots=True)
class DriftFinding:
    """A declared cost_per_call that no longer matches measured reality.

    A fact for humans — never an automatic modification (resource-metering §8).
    """

    tool: str
    declared_cost: float
    measured_cost_mean: float
    drift_ratio: float
    sample_count: int

    @property
    def summary(self) -> str:
        return (
            f"metadata_review_candidate: tool {self.tool!r} declared "
            f"{self.declared_cost:.4f} vs measured {self.measured_cost_mean:.4f} "
            f"(drift {self.drift_ratio:+.0%}, n={self.sample_count})"
        )


def drift_findings(
    per_tool_measured: dict[str, tuple[float, int]],
    cost_per_call: dict[str, float],
    *,
    threshold: float = 0.5,
) -> tuple[DriftFinding, ...]:
    """Compare measured tool costs against declared ones; report drift only."""
    findings: list[DriftFinding] = []
    for tool in sorted(per_tool_measured):
        declared = cost_per_call.get(tool)
        if declared is None or declared <= 0.0:
            continue
        measured_mean, sample_count = per_tool_measured[tool]
        ratio = (measured_mean - declared) / declared
        if abs(ratio) >= threshold:
            findings.append(
                DriftFinding(
                    tool=tool,
                    declared_cost=declared,
                    measured_cost_mean=measured_mean,
                    drift_ratio=ratio,
                    sample_count=sample_count,
                )
            )
    return tuple(findings)


def metered(
    name: str,
    *,
    access: str = "read",
    invoke: Callable[..., Awaitable[Any]],
    estimate: Callable[[str], int] | None = None,
) -> Callable[..., Awaitable[Any]]:
    """Wrap any async callable as a metered resource handle (§6).

    Recipe for external clients (sql / redis / http — apps inject the client,
    the core stays dependency-free)::

        orders = metered("mysql.orders", access="read",
                         invoke=my_existing_async_get)
        row = await orders("ORD-1")        # counted onto the current tool call

    With ``estimate`` the wrapper additionally records estimated tokens from
    the prompt text (ESTIMATED tier — explicit, never sold as measured).
    """
    if not callable(invoke):
        from ..core.errors import ResourceHandleError

        raise ResourceHandleError(
            f"metered resource {name!r} needs a callable invoke"
        )

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        import time

        collector = current_collector()
        started = time.perf_counter()
        try:
            result = await invoke(*args, **kwargs)
        finally:
            latency = (time.perf_counter() - started) * 1000.0
            collector.record_access(name, access, latency_ms=latency)
        if estimate is not None:
            prompt = next((a for a in args if isinstance(a, str)), "")
            collector.record_estimated_tokens(
                TokenUsage(input_tokens=estimate(prompt))
            )
        return result

    return wrapper
