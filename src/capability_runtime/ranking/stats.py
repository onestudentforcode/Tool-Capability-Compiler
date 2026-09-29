"""Wilson score intervals and statistical-tie detection (phase5 §9).

Pure functions, no state: a success-rate point estimate is never shown
without its interval, and two routes whose intervals overlap are a tie —
never a fabricated ordering.
"""

from __future__ import annotations

from statistics import NormalDist

from ..core.errors import RankingConfigError


def z_score(confidence_level: float) -> float:
    """Two-sided normal quantile for a confidence level in the open (0, 1)."""
    if (
        isinstance(confidence_level, bool)
        or not 0.0 < confidence_level < 1.0
    ):
        raise RankingConfigError(
            "confidence_level must be within the open interval (0, 1)"
        )
    return NormalDist().inv_cdf((1.0 + confidence_level) / 2.0)


def wilson_interval(
    successes: int, trials: int, confidence_level: float = 0.95
) -> tuple[float, float]:
    """Wilson score interval for a binomial success rate.

    ``trials == 0`` returns ``(0.0, 1.0)`` — maximally uninformative, never
    a fabricated zero-width guess.
    """
    if trials < 0 or successes < 0 or successes > trials:
        raise RankingConfigError(
            "wilson_interval requires 0 <= successes <= trials"
        )
    if trials == 0:
        return (0.0, 1.0)
    z = z_score(confidence_level)
    p = successes / trials
    denominator = 1.0 + z * z / trials
    center = (p + z * z / (2 * trials)) / denominator
    spread = (
        z
        * (p * (1 - p) / trials + z * z / (4 * trials * trials)) ** 0.5
        / denominator
    )
    return (max(0.0, center - spread), min(1.0, center + spread))


def intervals_overlap(
    first: tuple[float, float], second: tuple[float, float]
) -> bool:
    """True when two closed intervals share at least one point."""
    return first[0] <= second[1] and second[0] <= first[1]


def statistical_tie(
    first: tuple[float, float], second: tuple[float, float]
) -> bool:
    """Two success rates whose confidence intervals overlap are a TIE."""
    return intervals_overlap(first, second)
