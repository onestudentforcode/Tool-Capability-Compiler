import pytest

from capability_runtime import (
    DatasetSplit,
    OptimizationError,
    Scenario,
    ScenarioSuite,
    split_ids,
    split_suite,
)


def _suite(n=20):
    scenarios = tuple(
        Scenario(
            id=f"scenario_{i:03d}",
            query=f"query {i}",
            expected_capabilities=("cap.a",),
        )
        for i in range(n)
    )
    return ScenarioSuite(name="t", version="1.0", scenarios=scenarios)


def test_split_suite_produces_three_disjoint_sets() -> None:
    suite = _suite(100)
    split = split_suite(suite)
    assert isinstance(split, DatasetSplit)
    all_ids = set(split.optimization_ids) | set(split.validation_ids) | set(split.sentinel_ids)
    assert len(all_ids) == len(split.optimization_ids) + len(split.validation_ids) + len(split.sentinel_ids)
    assert len(all_ids) == 100
    assert split.total == 100


def test_split_suite_is_deterministic() -> None:
    suite = _suite(100)
    a = split_suite(suite)
    b = split_suite(suite)
    assert a.optimization_ids == b.optimization_ids
    assert a.validation_ids == b.validation_ids
    assert a.sentinel_ids == b.sentinel_ids


def test_split_suite_salt_changes_partition() -> None:
    suite = _suite(100)
    a = split_suite(suite, salt="salt-a")
    b = split_suite(suite, salt="salt-b")
    assert a.optimization_ids != b.optimization_ids


def test_split_suite_ratio_controls_sizes() -> None:
    suite = _suite(1000)
    split = split_suite(
        suite,
        validation_ratio=0.30,
        sentinel_ratio=0.10,
    )
    # With 1000 scenarios, ratios should be within reasonable bounds.
    assert 0.25 < len(split.validation_ids) / 1000 < 0.35
    assert 0.05 < len(split.sentinel_ids) / 1000 < 0.15
    assert len(split.optimization_ids) + len(split.validation_ids) + len(split.sentinel_ids) == 1000


def test_split_suite_rejects_overlapping_ratios() -> None:
    suite = _suite(10)
    with pytest.raises(OptimizationError):
        split_suite(suite, validation_ratio=0.6, sentinel_ratio=0.5)


def test_split_suite_rejects_negative_ratios() -> None:
    suite = _suite(10)
    with pytest.raises(OptimizationError):
        split_suite(suite, validation_ratio=-0.1)
    with pytest.raises(OptimizationError):
        split_suite(suite, sentinel_ratio=-0.1)


def test_split_ids_works_with_id_list() -> None:
    ids = [f"s{i}" for i in range(100)]
    split = split_ids(ids, validation_ratio=0.2, sentinel_ratio=0.05)
    assert isinstance(split, DatasetSplit)
    assert split.total == 100


def test_split_ids_dedupes() -> None:
    ids = ["a", "a", "b", "c", "d", "e"]
    split = split_ids(ids)
    assert split.total == 5


def test_split_ids_empty_rejected() -> None:
    with pytest.raises(OptimizationError):
        split_ids([])


def test_dataset_split_empty_optimization_rejected() -> None:
    # If every scenario ends up in sentinel (extreme ratio), the split is invalid.
    # But with a small suite and large sentinel ratio, optimization could be empty.
    # Let's test by constructing a DatasetSplit directly.
    with pytest.raises(OptimizationError):
        DatasetSplit(
            optimization_ids=(),
            validation_ids=("v1",),
            sentinel_ids=("s1",),
        )


def test_dataset_split_overlap_rejected() -> None:
    with pytest.raises(OptimizationError):
        DatasetSplit(
            optimization_ids=("a", "b"),
            validation_ids=("b", "c"),
            sentinel_ids=("d",),
        )
