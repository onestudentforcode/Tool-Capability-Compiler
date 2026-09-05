from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeAlias

from ..core.errors import ExecutionError

ExecutionInputs: TypeAlias = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ArtifactValue:
    """A single tool output branded with its source, for traceability."""

    value: Any
    source_tool: str
    layer: str
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if not isinstance(self.source_tool, str) or not self.source_tool.strip():
            raise ExecutionError("ArtifactValue source_tool must be a non-empty string")
        if not isinstance(self.layer, str) or not self.layer.strip():
            raise ExecutionError("ArtifactValue layer must be a non-empty string")


@dataclass(slots=True)
class ExecutionState:
    """Cross-layer blackboard. Same-name outputs from different tools are kept,
    never overwritten (multi-source artifact)."""

    query: str
    scenario_inputs: ExecutionInputs = field(default_factory=dict)
    final_response: Any = None
    _artifacts: dict[str, list[ArtifactValue]] = field(
        default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise ExecutionError("ExecutionState query must be a non-empty string")
        if not isinstance(self.scenario_inputs, Mapping):
            raise ExecutionError("ExecutionState scenario_inputs must be a mapping")
        self.scenario_inputs = dict(self.scenario_inputs)

    def add_artifact(self, name: str, artifact: ArtifactValue) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ExecutionError("Artifact name must be a non-empty string")
        if not isinstance(artifact, ArtifactValue):
            raise ExecutionError("Artifact must be an ArtifactValue instance")
        self._artifacts.setdefault(name, []).append(artifact)

    def get_artifacts(self, name: str) -> tuple[ArtifactValue, ...]:
        return tuple(self._artifacts.get(name, ()))

    def latest(self, name: str) -> ArtifactValue | None:
        artifacts = self._artifacts.get(name)
        return artifacts[-1] if artifacts else None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._artifacts))