"""Statistical conversions for data extraction, each with its formula and reference.

Results are estimates: a reviewer saves them as calculated values, which keep the method and inputs as provenance.
References:
- Wan X, Wang W, Liu J, Tong T. BMC Med Res Methodol 2014;14:135 (SD from median with range or IQR).
- Luo D, Wan X, Liu J, Tong T. Stat Methods Med Res 2018;27:1785-1805 (mean from median with range and/or IQR).
- Shi J, Luo D, Weng H, et al. Res Synth Methods 2020;11:641-654 (SD from the five-number summary).
- Higgins JPT, et al. Cochrane Handbook for Systematic Reviews of Interventions v6.5, chapter 6 (SE, CI, p-value,
  combining groups, change scores) and chapter 23 (cluster-randomized trials).
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import NormalDist

_NORMAL = NormalDist()


class ConversionError(ValueError):
    """Inputs can't be converted. The message is safe to show users."""


@dataclass
class Conversion:
    method: str
    values: dict[str, float]
    formula: str
    reference: str
    inputs: dict[str, float] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)


def _positive(name: str, value: float) -> None:
    if not value > 0:
        raise ConversionError(f"{name} must be greater than zero")


def _sample_size(n: float) -> int:
    if n != int(n) or n < 2:
        raise ConversionError("The sample size must be a whole number of at least 2")
    return int(n)


def _z(level: float) -> float:
    if not 0 < level < 1:
        raise ConversionError("The confidence level must be between 0 and 1, for example 0.95")
    return _NORMAL.inv_cdf(1 - (1 - level) / 2)


# --- Student's t quantile, via the regularized incomplete beta function (Numerical Recipes, section 6.4) ---


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    tiny, qab, qap, qam = 1e-300, a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > tiny else tiny)
        c = 1 + aa / c if abs(1 + aa / c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > tiny else tiny)
        c = 1 + aa / c if abs(1 + aa / c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1) < 3e-14:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1 - front * _beta_continued_fraction(b, a, 1 - x) / b


def student_t_cdf(t: float, df: float) -> float:
    tail = 0.5 * regularized_incomplete_beta(df / 2, 0.5, df / (df + t * t))
    return 1 - tail if t >= 0 else tail


def student_t_ppf(p: float, df: float) -> float:
    if not 0 < p < 1:
        raise ConversionError("The probability must be between 0 and 1")
    low, high = -1e3, 1e3
    for _ in range(200):
        middle = (low + high) / 2
        if student_t_cdf(middle, df) < p:
            low = middle
        else:
            high = middle
    return (low + high) / 2


# --- Mean and SD from medians ---


def _xi(n: int) -> float:
    return 2 * _NORMAL.inv_cdf((n - 0.375) / (n + 0.25))


def _eta(n: int) -> float:
    return 2 * _NORMAL.inv_cdf((0.75 * n - 0.125) / (n + 0.25))


def mean_sd_from_median_range(median: float, minimum: float, maximum: float, n: float) -> Conversion:
    size = _sample_size(n)
    if not minimum <= median <= maximum:
        raise ConversionError("The median must lie between the minimum and maximum")
    weight = 4 / (4 + size**0.75)
    mean = weight * (minimum + maximum) / 2 + (1 - weight) * median
    sd = (maximum - minimum) / _xi(size)
    return Conversion(
        "mean_sd_from_median_range",
        {"mean": mean, "sd": sd},
        "mean = w(a+b)/2 + (1-w)m, w = 4/(4+n^0.75); SD = (b-a)/ξ(n), ξ(n) = 2Φ⁻¹((n-0.375)/(n+0.25))",
        "Luo et al. 2018 (mean); Wan et al. 2014 (SD)",
        {"median": median, "minimum": minimum, "maximum": maximum, "n": size},
        ["The outcome is approximately normally distributed; skewed data make these estimates unreliable."],
    )


def mean_sd_from_median_iqr(median: float, q1: float, q3: float, n: float) -> Conversion:
    size = _sample_size(n)
    if not q1 <= median <= q3:
        raise ConversionError("The median must lie between the first and third quartiles")
    weight = 0.7 + 0.39 / size
    mean = weight * (q1 + q3) / 2 + (1 - weight) * median
    sd = (q3 - q1) / _eta(size)
    return Conversion(
        "mean_sd_from_median_iqr",
        {"mean": mean, "sd": sd},
        "mean = w(q1+q3)/2 + (1-w)m, w = 0.7+0.39/n; SD = (q3-q1)/η(n), η(n) = 2Φ⁻¹((0.75n-0.125)/(n+0.25))",
        "Luo et al. 2018 (mean); Wan et al. 2014 (SD)",
        {"median": median, "q1": q1, "q3": q3, "n": size},
        ["The outcome is approximately normally distributed; skewed data make these estimates unreliable."],
    )


def mean_sd_from_five_numbers(
    minimum: float, q1: float, median: float, q3: float, maximum: float, n: float
) -> Conversion:
    size = _sample_size(n)
    if not minimum <= q1 <= median <= q3 <= maximum:
        raise ConversionError("The values must be ordered: minimum ≤ q1 ≤ median ≤ q3 ≤ maximum")
    w1 = 2.2 / (2.2 + size**0.75)
    w2 = 0.7 - 0.72 / size**0.55
    mean = w1 * (minimum + maximum) / 2 + w2 * (q1 + q3) / 2 + (1 - w1 - w2) * median
    theta1 = (2 + 0.14 * size**0.6) * _NORMAL.inv_cdf((size - 0.375) / (size + 0.25))
    theta2 = (2 + 2 / (0.07 * size**0.6)) * _NORMAL.inv_cdf((0.75 * size - 0.125) / (size + 0.25))
    sd = (maximum - minimum) / theta1 + (q3 - q1) / theta2
    return Conversion(
        "mean_sd_from_five_numbers",
        {"mean": mean, "sd": sd},
        "mean = w1(a+b)/2 + w2(q1+q3)/2 + (1-w1-w2)m, w1 = 2.2/(2.2+n^0.75), w2 = 0.7-0.72/n^0.55; "
        "SD = (b-a)/θ1(n) + (q3-q1)/θ2(n)",
        "Luo et al. 2018 (mean); Shi et al. 2020 (SD)",
        {"minimum": minimum, "q1": q1, "median": median, "q3": q3, "maximum": maximum, "n": size},
        ["The outcome is approximately normally distributed; skewed data make these estimates unreliable."],
    )


# --- Standard errors, confidence intervals, and p-values ---


def sd_from_se(se: float, n: float) -> Conversion:
    _positive("The standard error", se)
    size = _sample_size(n)
    return Conversion(
        "sd_from_se",
        {"sd": se * math.sqrt(size)},
        "SD = SE × √n",
        "Cochrane Handbook 6.5.2.2",
        {"se": se, "n": size},
    )


def sd_from_ci(lower: float, upper: float, n: float, level: float = 0.95) -> Conversion:
    size = _sample_size(n)
    if not upper > lower:
        raise ConversionError("The upper limit must be greater than the lower limit")
    t = student_t_ppf(1 - (1 - level) / 2, size - 1)
    return Conversion(
        "sd_from_ci",
        {"sd": math.sqrt(size) * (upper - lower) / (2 * t), "t": t},
        "SD = √n × (upper − lower) / (2 × t(1−α/2, n−1))",
        "Cochrane Handbook 6.5.2.2 (t distribution, which also suits small samples)",
        {"lower": lower, "upper": upper, "n": size, "level": level},
        ["The interval is for a single group mean."],
    )


def se_from_ci_difference(lower: float, upper: float, level: float = 0.95) -> Conversion:
    if not upper > lower:
        raise ConversionError("The upper limit must be greater than the lower limit")
    return Conversion(
        "se_from_ci_difference",
        {"se": (upper - lower) / (2 * _z(level))},
        "SE = (upper − lower) / (2 × z(1−α/2))",
        "Cochrane Handbook 6.3.1",
        {"lower": lower, "upper": upper, "level": level},
        ["The interval is for a difference (for example a mean difference) and is based on the normal distribution."],
    )


def se_from_ci_ratio(estimate: float, lower: float, upper: float, level: float = 0.95) -> Conversion:
    for name, value in (("The estimate", estimate), ("The lower limit", lower), ("The upper limit", upper)):
        _positive(name, value)
    if not lower <= estimate <= upper:
        raise ConversionError("The estimate must lie within its confidence interval")
    se = (math.log(upper) - math.log(lower)) / (2 * _z(level))
    return Conversion(
        "se_from_ci_ratio",
        {"log_estimate": math.log(estimate), "se": se},
        "ln(estimate); SE = (ln upper − ln lower) / (2 × z(1−α/2))",
        "Cochrane Handbook 6.3.2",
        {"estimate": estimate, "lower": lower, "upper": upper, "level": level},
        ["For ratio measures (odds ratio, risk ratio, hazard ratio); the SE is on the log scale."],
    )


def se_from_p_value(estimate: float, p: float, ratio: bool) -> Conversion:
    if not 0 < p < 1:
        raise ConversionError("The p-value must be between 0 and 1")
    if ratio:
        _positive("The estimate", estimate)
    effect = math.log(estimate) if ratio else estimate
    if effect == 0:
        raise ConversionError("A null estimate has no standard error derivable from its p-value")
    z = _NORMAL.inv_cdf(1 - p / 2)
    values = {"se": abs(effect) / z, "z": z}
    if ratio:
        values["log_estimate"] = effect
    return Conversion(
        "se_from_p_value",
        values,
        "z = Φ⁻¹(1 − p/2); SE = |estimate| / z" + (" on the log scale" if ratio else ""),
        "Cochrane Handbook 6.3.2",
        {"estimate": estimate, "p": p, "ratio": float(ratio)},
        ["The p-value is exact (not reported as a threshold such as p < 0.05) and comes from a two-sided z test."],
    )


# --- Combining and adjusting groups ---


def combine_groups(n1: float, mean1: float, sd1: float, n2: float, mean2: float, sd2: float) -> Conversion:
    a, b = _sample_size(n1), _sample_size(n2)
    total = a + b
    mean = (a * mean1 + b * mean2) / total
    variance = ((a - 1) * sd1**2 + (b - 1) * sd2**2 + a * b / total * (mean1**2 + mean2**2 - 2 * mean1 * mean2)) / (
        total - 1
    )
    return Conversion(
        "combine_groups",
        {"n": total, "mean": mean, "sd": math.sqrt(variance)},
        "N = N1+N2; M = (N1M1+N2M2)/N; SD = √(((N1−1)SD1² + (N2−1)SD2² + N1N2/N (M1² + M2² − 2M1M2)) / (N−1))",
        "Cochrane Handbook Table 6.5.a",
        {"n1": a, "mean1": mean1, "sd1": sd1, "n2": b, "mean2": mean2, "sd2": sd2},
    )


def cluster_adjust(n: float, average_cluster_size: float, icc: float, events: float | None = None) -> Conversion:
    size = _sample_size(n)
    _positive("The average cluster size", average_cluster_size)
    if not 0 <= icc < 1:
        raise ConversionError("The intracluster correlation must be between 0 and 1")
    design_effect = 1 + (average_cluster_size - 1) * icc
    values = {"design_effect": design_effect, "effective_n": size / design_effect}
    inputs = {"n": size, "average_cluster_size": average_cluster_size, "icc": icc}
    if events is not None:
        values["effective_events"] = events / design_effect
        inputs["events"] = events
    return Conversion(
        "cluster_adjust",
        values,
        "design effect = 1 + (M − 1) × ICC; effective n (and events) = n / design effect",
        "Cochrane Handbook 23.1.4",
        inputs,
        ["The ICC comes from the trial or a similar study; report it and run a sensitivity analysis."],
    )


def sd_of_change(sd_baseline: float, sd_final: float, correlation: float) -> Conversion:
    _positive("The baseline SD", sd_baseline)
    _positive("The final SD", sd_final)
    if not -1 <= correlation <= 1:
        raise ConversionError("The correlation must be between -1 and 1")
    variance = sd_baseline**2 + sd_final**2 - 2 * correlation * sd_baseline * sd_final
    return Conversion(
        "sd_of_change",
        {"sd": math.sqrt(max(0.0, variance))},
        "SD(change) = √(SD_baseline² + SD_final² − 2 × r × SD_baseline × SD_final)",
        "Cochrane Handbook 6.5.2.8",
        {"sd_baseline": sd_baseline, "sd_final": sd_final, "correlation": correlation},
        ["r comes from a study reporting all three SDs, or is assumed and tested in sensitivity analysis."],
    )


def events_from_percentage(percentage: float, n: float) -> Conversion:
    size = _sample_size(n)
    if not 0 <= percentage <= 100:
        raise ConversionError("The percentage must be between 0 and 100")
    return Conversion(
        "events_from_percentage",
        {"events": round(percentage / 100 * size)},
        "events = round(percentage / 100 × n)",
        "Cochrane Handbook 6.4",
        {"percentage": percentage, "n": size},
        ["The percentage is of the participants analysed (n), not of those randomized."],
    )


CONVERSIONS: dict[str, tuple[Callable[..., Conversion], str, tuple[str, ...]]] = {
    "mean_sd_from_median_range": (
        mean_sd_from_median_range,
        "Mean and SD from median and range",
        ("median", "minimum", "maximum", "n"),
    ),
    "mean_sd_from_median_iqr": (
        mean_sd_from_median_iqr,
        "Mean and SD from median and interquartile range",
        ("median", "q1", "q3", "n"),
    ),
    "mean_sd_from_five_numbers": (
        mean_sd_from_five_numbers,
        "Mean and SD from median, IQR, and range",
        ("minimum", "q1", "median", "q3", "maximum", "n"),
    ),
    "sd_from_se": (sd_from_se, "SD from standard error", ("se", "n")),
    "sd_from_ci": (sd_from_ci, "SD from a confidence interval of a mean", ("lower", "upper", "n", "level")),
    "se_from_ci_difference": (
        se_from_ci_difference,
        "SE of a difference from its confidence interval",
        ("lower", "upper", "level"),
    ),
    "se_from_ci_ratio": (
        se_from_ci_ratio,
        "Log estimate and SE of a ratio (OR, RR, HR) from its confidence interval",
        ("estimate", "lower", "upper", "level"),
    ),
    "se_from_p_value": (se_from_p_value, "SE from an exact p-value", ("estimate", "p", "ratio")),
    "combine_groups": (
        combine_groups,
        "Combine two groups (for example two arms)",
        ("n1", "mean1", "sd1", "n2", "mean2", "sd2"),
    ),
    "cluster_adjust": (
        cluster_adjust,
        "Effective sample size for a cluster-randomized trial",
        ("n", "average_cluster_size", "icc", "events"),
    ),
    "sd_of_change": (sd_of_change, "SD of change from baseline", ("sd_baseline", "sd_final", "correlation")),
    "events_from_percentage": (events_from_percentage, "Events from a percentage", ("percentage", "n")),
}
OPTIONAL_INPUTS = {
    ("sd_from_ci", "level"),
    ("se_from_ci_difference", "level"),
    ("se_from_ci_ratio", "level"),
    ("cluster_adjust", "events"),
}


def convert(method: str, inputs: dict[str, float]) -> Conversion:
    if method not in CONVERSIONS:
        raise ConversionError(f"Unknown conversion: {method}")
    function, _, names = CONVERSIONS[method]
    arguments = {}
    for name in names:
        if name in inputs and inputs[name] is not None:
            value = inputs[name]
            if not isinstance(value, int | float) or not math.isfinite(value):
                raise ConversionError(f"{name} must be a number")
            arguments[name] = bool(value) if name == "ratio" else float(value)
        elif (method, name) not in OPTIONAL_INPUTS:
            raise ConversionError(f"{name} is required")
    return function(**arguments)


def catalog() -> list[dict]:
    return [
        {
            "key": key,
            "label": label,
            "inputs": [{"name": name, "optional": (key, name) in OPTIONAL_INPUTS} for name in names],
        }
        for key, (_, label, names) in CONVERSIONS.items()
    ]
