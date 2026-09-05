from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from ..core.errors import OptimizationError
from ..scenario.models import ScenarioSuite


@dataclass(frozen=True, slots=True)
class DatasetSplit:
    """Deterministic three-way split of a ScenarioSuite (Step 11).

    Splits are derived from ``hash(scenario_id)`` so the same suite always
    produces the same partition. Validation and Sentinel scenarios **never**
    participate in candidate generation (overfitting guard, §128).
    """

    optimization_ids: tuple[str, ...]
    validation_ids: tuple[str, ...]
    sentinel_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        optimization = set(self.optimization_ids)
        validation = set(self.validation_ids)
        sentinel = set(self.sentinel_ids)
        all_ids = optimization | validation | sentinel
        if len(all_ids) != len(optimization) + len(validation) + len(sentinel):
            raise OptimizationError("Dataset split scenarios must be disjoint")
        if not optimization:
            raise OptimizationError("Optimization set cannot be empty")

    @property
    def total(self) -> int:
        return len(self.optimization_ids) + len(self.validation_ids) + len(self.sentinel_ids)


def _hash_fraction(scenario_id: str, salt: str) -> float:
    digest = hashlib.sha256(f"{salt}:{scenario_id}".encode("utf-8")).digest()
    # Interpret first 8 bytes as a value in [0, 1).
    value = int.from_bytes(digest[:8], byteorder="big") / (1 << 64)
    return value


def split_suite(
    suite: ScenarioSuite,
    *,
    validation_ratio: float = 0.20,
    sentinel_ratio: float = 0.05,
    salt: str = "phase4-split-v1",
) -> DatasetSplit:
    """Deterministically split a suite into optimization / validation / sentinel.

    The split uses the 64-bit prefix of SHA-256(salt + scenario_id) to assign
    each scenario to one of three buckets. Ordering is preserved inside each
    bucket, matching the suite's scenario order (deterministic output rule).

    - ``validation_ratio`` and ``sentinel_ratio`` must be non-negative and sum
      to less than 1.0; the remainder goes to the optimization set.
    - Sentinel scenarios are the *highest-priority* holdout: they come from
      the top of the hash space so the same scenarios are always sentinels
      regardless of ratio adjustments.
    """
    if isinstance(validation_ratio, bool) or validation_ratio < 0.0:
        raise OptimizationError("validation_ratio must be non-negative")
    if isinstance(sentinel_ratio, bool) or sentinel_ratio < 0.0:
        raise OptimizationError("sentinel_ratio must be non-negative")
    if validation_ratio + sentinel_ratio >= 1.0:
        raise OptimizationError(
            "validation_ratio + sentinel_ratio must be less than 1.0"
        )

    optimization: list[str] = []
    validation: list[str] = []
    sentinel: list[str] = []

    sentinel_cutoff = 1.0 - sentinel_ratio
    validation_cutoff = sentinel_cutoff - validation_ratio

    for scenario in suite.scenarios:
        value = _hash_fraction(scenario.id, salt)
        if value >= sentinel_cutoff:
            sentinel.append(scenario.id)
        elif value >= validation_cutoff:
            validation.append(scenario.id)
        else:
            optimization.append(scenario.id)

    return DatasetSplit(
        optimization_ids=tuple(optimization),
        validation_ids=tuple(validation),
        sentinel_ids=tuple(sentinel),
    )


def split_ids(
    scenario_ids: Iterable[str],
    *,
    validation_ratio: float = 0.20,
    sentinel_ratio: float = 0.05,
    salt: str = "phase4-split-v1",
) -> DatasetSplit:
    """Variant that accepts an iterable of IDs instead of a full ScenarioSuite."""
    ids = tuple(dict.fromkeys(scenario_ids))  # dedupe + preserve order
    if not ids:
        raise OptimizationError("Cannot split an empty scenario id list")

    if isinstance(validation_ratio, bool) or validation_ratio < 0.0:
        raise OptimizationError("validation_ratio must be non-negative")
    if isinstance(sentinel_ratio, bool) or sentinel_ratio < 0.0:
        raise OptimizationError("sentinel_ratio must be non-negative")
    if validation_ratio + sentinel_ratio >= 1.0:
        raise OptimizationError(
            "validation_ratio + sentinel_ratio must be less than 1.0"
        )

    optimization: list[str] = []
    validation: list[str] = []
    sentinel: list[str] = []

    sentinel_cutoff = 1.0 - sentinel_ratio
    validation_cutoff = sentinel_cutoff - validation_ratio

    for scenario_id in ids:
        value = _hash_fraction(scenario_id, salt)
        if value >= sentinel_cutoff:
            sentinel.append(scenario_id)
        elif value >= validation_cutoff:
            validation.append(scenario_id)
        else:
            optimization.append(scenario_id)

    return DatasetSplit(
        optimization_ids=tuple(optimization),
        validation_ids=tuple(validation),
        sentinel_ids=tuple(sentinel),
    )