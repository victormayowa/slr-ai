"""Agreement between reviewers (Cohen's kappa and PABAK) and proportions with Wilson score intervals."""

import math
from collections.abc import Sequence
from dataclasses import dataclass

Z_95 = 1.959963984540054


@dataclass
class Agreement:
    # Items both reviewers decided.
    n: int
    observed: float | None
    expected: float | None
    kappa: float | None
    # 95% confidence interval for kappa (large-sample standard error); None when it can't be estimated.
    kappa_ci: tuple[float, float] | None
    # Prevalence- and bias-adjusted kappa (Byrt, Bishop & Carlin 1993): kappa with chance agreement set to 1/categories.
    pabak: float | None


def cohens_kappa(pairs: Sequence[tuple[str, str]], categories: Sequence[str]) -> Agreement:
    """Cohen's kappa for two raters over the same items, each pair being (rater A's label, rater B's label)."""
    n = len(pairs)
    if n == 0:
        return Agreement(0, None, None, None, None, None)
    observed = sum(a == b for a, b in pairs) / n
    expected = sum(
        (sum(a == category for a, _ in pairs) / n) * (sum(b == category for _, b in pairs) / n)
        for category in categories
    )
    kappa: float | None = None
    interval: tuple[float, float] | None = None
    if expected < 1:
        kappa = (observed - expected) / (1 - expected)
        se = math.sqrt(observed * (1 - observed) / (n * (1 - expected) ** 2))
        interval = (max(-1.0, kappa - Z_95 * se), min(1.0, kappa + Z_95 * se))
    k = len(categories)
    pabak = (k * observed - 1) / (k - 1) if k > 1 else None
    return Agreement(n, observed, expected, kappa, interval, pabak)


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float] | None:
    """Wilson score interval for a proportion; None when there are no observations."""
    if n <= 0:
        return None
    p = successes / n
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))
