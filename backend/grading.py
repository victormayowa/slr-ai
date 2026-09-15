"""GRADE certainty of evidence, summary of findings, and interpretation.

Suggested domain ratings are computed from the analysis results and risk of bias assessments with transparent rules;
methodologists set the ratings and justify them. Absolute effects, numbers needed to treat, and optimal information
sizes are arithmetic conversions of the R results. References: GRADE Handbook (2013); Guyatt et al. J Clin Epidemiol
2011 (GRADE guidelines series); Santesso et al. J Clin Epidemiol 2020;119:126-135 (informative statements); Altman BMJ
1998;317:1309 (NNT confidence intervals); Pieper et al. J Clin Epidemiol 2014;67:368-75 (corrected covered area).
"""

import math
from collections.abc import Mapping, Sequence
from statistics import NormalDist, median
from typing import Any

LEVELS = ("very_low", "low", "moderate", "high")
LEVEL_LABELS = {"very_low": "Very low", "low": "Low", "moderate": "Moderate", "high": "High"}
DOWNGRADE_DOMAINS = {
    "risk_of_bias": "Risk of bias",
    "inconsistency": "Inconsistency",
    "indirectness": "Indirectness",
    "imprecision": "Imprecision",
    "publication_bias": "Publication bias",
}
UPGRADE_DOMAINS = {
    "large_effect": "Large effect",
    "dose_response": "Dose-response gradient",
    "plausible_confounding": "All plausible confounding would reduce the effect",
}
HIGH_RISK = {"high", "serious", "critical", "very_high"}
SOME_CONCERNS = {"some_concerns", "moderate", "unclear", "no_information"}
RATIO = {"RR", "OR", "HR", "ROM"}


def certainty(starting: str, domains: Mapping[str, Mapping[str, Any]]) -> str:
    score = LEVELS.index(starting if starting in LEVELS else "high")
    for key in DOWNGRADE_DOMAINS:
        score += max(-2, min(0, int(domains.get(key, {}).get("rating", 0))))
    for key in UPGRADE_DOMAINS:
        score += max(0, min(2, int(domains.get(key, {}).get("rating", 0))))
    return LEVELS[max(0, min(3, score))]


def _z(p: float) -> float:
    return NormalDist().inv_cdf(p)


def ois_binary(
    control_risk: float, relative_risk_reduction: float = 0.25, alpha: float = 0.05, power: float = 0.8
) -> int:
    """Total participants a single adequately powered trial would need (two groups, two-sided alpha)."""
    p1 = control_risk
    p2 = control_risk * (1 - relative_risk_reduction)
    if not 0 < p1 < 1 or p1 == p2:
        return 0
    pbar = (p1 + p2) / 2
    numerator = (
        _z(1 - alpha / 2) * math.sqrt(2 * pbar * (1 - pbar)) + _z(power) * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    ) ** 2
    return 2 * math.ceil(numerator / (p1 - p2) ** 2)


def ois_continuous(sd: float, delta: float, alpha: float = 0.05, power: float = 0.8) -> int:
    if sd <= 0 or delta <= 0:
        return 0
    return 2 * math.ceil(2 * (_z(1 - alpha / 2) + _z(power)) ** 2 * sd**2 / delta**2)


def _risk_with_intervention(measure: str, effect: float, baseline: float) -> float:
    if measure == "RR":
        return min(1.0, baseline * effect)
    if measure == "OR":
        return effect * baseline / (1 - baseline + effect * baseline)
    if measure == "HR":
        return 1 - (1 - baseline) ** effect
    if measure == "RD":
        return max(0.0, min(1.0, baseline + effect))
    raise ValueError(measure)


def absolute_effects(
    measure: str, estimate: float, lower: float, upper: float, baseline: float
) -> dict[str, Any] | None:
    """Anticipated absolute effects per 1000 for a baseline risk, from a ratio (natural scale) or risk difference."""
    if measure not in ("RR", "OR", "HR", "RD") or not 0 <= baseline <= 1:
        return None
    point = _risk_with_intervention(measure, estimate, baseline)
    bounds = sorted(_risk_with_intervention(measure, value, baseline) for value in (lower, upper))
    difference = point - baseline
    difference_bounds = (bounds[0] - baseline, bounds[1] - baseline)
    out = {
        "baseline_per_1000": round(baseline * 1000),
        "intervention_per_1000": round(point * 1000),
        "intervention_per_1000_ci": [round(bounds[0] * 1000), round(bounds[1] * 1000)],
        "difference_per_1000": round(difference * 1000),
        "difference_per_1000_ci": [round(difference_bounds[0] * 1000), round(difference_bounds[1] * 1000)],
    }
    out["nnt"] = number_needed_to_treat(difference, difference_bounds)
    return out


def _whole(value: float) -> int:
    """Round up to a whole number, ignoring floating-point noise (1 / 0.02 must be 50, not 51)."""
    return math.ceil(round(value, 6))


def number_needed_to_treat(difference: float, bounds: tuple[float, float]) -> dict[str, Any] | None:
    """The number needed to treat for an additional beneficial (NNTB, when the risk falls) or harmful (NNTH) outcome,
    with Altman's interval, which runs through infinity when the risk difference's interval includes zero."""
    if difference == 0:
        return None
    low, high = bounds
    if low < 0 < high:
        interval = f"NNTB {_whole(1 / abs(low))} to ∞ to NNTH {_whole(1 / high)}"
    else:
        ends = sorted(_whole(1 / abs(edge)) for edge in bounds if edge != 0)
        interval = f"{ends[0]} to {ends[-1]}" if ends else ""
    return {"type": "NNTB" if difference < 0 else "NNTH", "value": _whole(1 / abs(difference)), "interval": interval}


def _summary_numbers(results: Mapping[str, Any]) -> dict[str, Any]:
    summary = results.get("summary") or results.get("two_stage") or {}
    return dict(summary) if isinstance(summary, Mapping) else {}


def suggested_ratings(
    analysis_type: str,
    measure: str,
    results: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    mid: float | None,
    mid_scale: str,
    starting: str,
) -> dict[str, dict[str, Any]]:
    """Inputs and a suggested rating for each GRADE domain, from an analysis run and its data set."""
    summary = _summary_numbers(results)
    studies = results.get("studies") or []
    ratio = measure in RATIO
    suggestions: dict[str, dict[str, Any]] = {}

    # Risk of bias: the share of the analysis weight from studies at high risk or with some concerns.
    rob_by_study = {row.get("study"): row.get("rob", "") for row in rows}
    weights = [(s.get("study"), s.get("weight")) for s in studies if isinstance(s, Mapping)]
    usable = [(name, w) for name, w in weights if isinstance(w, int | float) and not math.isnan(w)]
    total = sum(w for _, w in usable) or float(len(weights) or 1)
    if not usable:
        usable = [(name, 1.0) for name, _ in weights]
    high = sum(w for name, w in usable if rob_by_study.get(name) in HIGH_RISK) / total
    concerns = sum(w for name, w in usable if rob_by_study.get(name) in SOME_CONCERNS) / total
    unassessed = sum(1 for name, _ in usable if not rob_by_study.get(name))
    rating = -2 if high >= 0.6 else -1 if high >= 0.3 or high + concerns >= 0.5 else 0
    suggestions["risk_of_bias"] = {
        "inputs": {
            "weight_high_risk": high,
            "weight_some_concerns": concerns,
            "studies_without_assessment": unassessed,
        },
        "suggested_rating": rating,
        "reason": (
            f"{high:.0%} of the analysis weight comes from studies at high risk of bias and {concerns:.0%} from "
            "studies with some concerns."
        ),
    }

    # Inconsistency.
    i2 = summary.get("I2")
    pi = (summary.get("pi_lower"), summary.get("pi_upper"))
    pi_crosses = pi[0] is not None and pi[1] is not None and pi[0] < 0 < pi[1]
    if isinstance(i2, int | float):
        rating = -2 if i2 >= 75 and pi_crosses else -1 if i2 >= 50 and pi_crosses else 0
        reason = f"I² = {i2:.0f}%{'; the prediction interval includes no effect' if pi_crosses else ''}."
    else:
        rating, reason = 0, "No heterogeneity statistics for this analysis; judge the consistency of results directly."
    subgroup_p = ((results.get("subgroups") or {}).get("test_for_differences") or {}).get("p_value")
    suggestions["inconsistency"] = {
        "inputs": {
            "I2": i2,
            "tau2": summary.get("tau2"),
            "Q_p_value": summary.get("Q_p_value"),
            "prediction_interval": list(pi),
            "subgroup_difference_p": subgroup_p,
        },
        "suggested_rating": rating,
        "reason": reason,
    }

    suggestions["indirectness"] = {
        "inputs": {},
        "suggested_rating": 0,
        "reason": "Judge the studies' population, intervention, comparator, and outcome against the review question.",
    }

    # Imprecision: the confidence interval against no effect and the minimal important difference, and the OIS.
    lower, upper = summary.get("ci_lower"), summary.get("ci_upper")
    participants = sum((row.get("n1i") or 0) + (row.get("n2i") or 0) for row in rows)
    control_risks = [row["ci"] / row["n2i"] for row in rows if row.get("n2i") and row.get("ci") is not None]
    control_sds = [row["sd2i"] for row in rows if row.get("sd2i")]
    ois = 0
    if control_risks:
        ois = ois_binary(median(control_risks))
    elif control_sds:
        delta = mid if (mid and mid_scale == "units") else 0.2 * median(control_sds)
        ois = ois_continuous(median(control_sds), delta)
    imprecision_inputs: dict[str, Any] = {"participants": participants, "optimal_information_size": ois}
    rating, reasons = 0, []
    if isinstance(lower, int | float) and isinstance(upper, int | float):
        crosses_null = lower < 0 < upper
        imprecision_inputs["ci_includes_no_effect"] = crosses_null
        if crosses_null:
            rating -= 1
            reasons.append("the confidence interval includes no effect")
        if mid and mid_scale == "units" and not ratio and lower < -mid and upper > mid:
            rating -= 1
            reasons.append("the interval includes important benefit and important harm")
    if ois and participants and participants < ois:
        rating -= 1 if rating > -2 else 0
        reasons.append(f"{participants} participants is below the optimal information size of {ois}")
    suggestions["imprecision"] = {
        "inputs": imprecision_inputs,
        "suggested_rating": max(-2, rating),
        "reason": ("Suggested because " + "; ".join(reasons) + ".")
        if reasons
        else "The confidence interval excludes no effect and the information size is adequate.",
    }

    # Publication bias.
    bias = results.get("publication_bias") or {}
    k = summary.get("k") or len(studies)
    egger_p = (bias.get("egger") or {}).get("p_value")
    if isinstance(k, int) and k < 10:
        rating, reason = (
            0,
            "Fewer than 10 studies: funnel plot asymmetry can't be assessed reliably; consider the search's "
            "comprehensiveness and industry funding.",
        )
    elif isinstance(egger_p, int | float) and egger_p < 0.10:
        rating, reason = -1, f"Egger's test suggests small-study effects (p = {egger_p:.3f})."
    else:
        rating, reason = 0, "No evidence of small-study effects from the tests run."
    suggestions["publication_bias"] = {
        "inputs": {
            "k": k,
            "egger_p": egger_p,
            "trim_and_fill_imputed": (bias.get("trim_and_fill") or {}).get("imputed_studies"),
        },
        "suggested_rating": rating,
        "reason": reason,
    }

    # Large effect, for evidence starting at low certainty.
    exp_estimate = summary.get("exp_estimate")
    exp_lower, exp_upper = summary.get("exp_ci_lower"), summary.get("exp_ci_upper")
    rating, reason = 0, "Not applicable."
    if starting == "low" and ratio and exp_estimate is not None and exp_lower is not None and exp_upper is not None:
        point, low, high = float(exp_estimate), float(exp_lower), float(exp_upper)
        if point >= 5 or point <= 0.2:
            rating, reason = 2, "Very large effect (ratio of 5 or more, or 0.2 or less)."
        elif (point >= 2 and low > 1) or (point <= 0.5 and high < 1):
            rating, reason = 1, "Large effect (ratio of 2 or more, or 0.5 or less, excluding no effect)."
    suggestions["large_effect"] = {"inputs": {"estimate": exp_estimate}, "suggested_rating": rating, "reason": reason}
    return suggestions


def effect_direction(
    measure: str, summary: Mapping[str, Any], absolute: Mapping[str, Any] | None, mid: float | None, mid_scale: str
) -> str:
    """ "reduction", "increase", or "little_or_no_difference" in the outcome."""
    estimate = summary.get("estimate")
    lower, upper = summary.get("ci_lower"), summary.get("ci_upper")
    if not isinstance(estimate, int | float):
        return "unknown"
    if mid:
        if mid_scale == "per_1000" and absolute:
            magnitude = abs(absolute["difference_per_1000"])
        elif mid_scale == "units":
            magnitude = abs(estimate)
        else:
            magnitude = None
        if magnitude is not None and magnitude < mid:
            return "little_or_no_difference"
    elif isinstance(lower, int | float) and isinstance(upper, int | float) and lower < 0 < upper:
        return "little_or_no_difference"
    return "reduction" if estimate < 0 else "increase"


def informative_statement(level: str, direction: str, intervention: str, outcome: str) -> str:
    """GRADE informative statement wording (Santesso et al. 2020)."""
    if level == "very_low" or direction == "unknown":
        return f"The evidence is very uncertain about the effect of {intervention} on {outcome}."
    verb = {"high": "results in", "moderate": "likely results in", "low": "may result in"}[level]
    effect = {
        "reduction": "a reduction in",
        "increase": "an increase in",
        "little_or_no_difference": "little to no difference in",
    }[direction]
    return f"{intervention[:1].upper()}{intervention[1:]} {verb} {effect} {outcome}."


ETD_CRITERIA: list[dict[str, Any]] = [
    {
        "key": "problem",
        "label": "Is the problem a priority?",
        "options": ["No", "Probably no", "Probably yes", "Yes", "Varies", "Don't know"],
    },
    {
        "key": "desirable_effects",
        "label": "How substantial are the desirable anticipated effects?",
        "options": ["Trivial", "Small", "Moderate", "Large", "Varies", "Don't know"],
    },
    {
        "key": "undesirable_effects",
        "label": "How substantial are the undesirable anticipated effects?",
        "options": ["Large", "Moderate", "Small", "Trivial", "Varies", "Don't know"],
    },
    {
        "key": "certainty",
        "label": "What is the overall certainty of the evidence of effects?",
        "options": ["Very low", "Low", "Moderate", "High", "No included studies"],
    },
    {
        "key": "values",
        "label": "Is there important uncertainty about or variability in how much people value the main outcomes?",
        "options": [
            "Important uncertainty or variability",
            "Possibly important uncertainty or variability",
            "Probably no important uncertainty or variability",
            "No important uncertainty or variability",
        ],
    },
    {
        "key": "balance",
        "label": "Does the balance of desirable and undesirable effects favour the intervention or the comparison?",
        "options": [
            "Favours the comparison",
            "Probably favours the comparison",
            "Doesn't favour either",
            "Probably favours the intervention",
            "Favours the intervention",
            "Varies",
            "Don't know",
        ],
    },
    {
        "key": "resources",
        "label": "How large are the resource requirements (costs)?",
        "options": [
            "Large costs",
            "Moderate costs",
            "Negligible costs and savings",
            "Moderate savings",
            "Large savings",
            "Varies",
            "Don't know",
        ],
    },
    {
        "key": "resources_certainty",
        "label": "What is the certainty of the evidence of resource requirements?",
        "options": ["Very low", "Low", "Moderate", "High", "No included studies"],
    },
    {
        "key": "cost_effectiveness",
        "label": "Does the cost-effectiveness favour the intervention or the comparison?",
        "options": [
            "Favours the comparison",
            "Probably favours the comparison",
            "Doesn't favour either",
            "Probably favours the intervention",
            "Favours the intervention",
            "Varies",
            "No included studies",
        ],
    },
    {
        "key": "equity",
        "label": "What would be the impact on health equity?",
        "options": [
            "Reduced",
            "Probably reduced",
            "Probably no impact",
            "Probably increased",
            "Increased",
            "Varies",
            "Don't know",
        ],
    },
    {
        "key": "acceptability",
        "label": "Is the intervention acceptable to key stakeholders?",
        "options": ["No", "Probably no", "Probably yes", "Yes", "Varies", "Don't know"],
    },
    {
        "key": "feasibility",
        "label": "Is the intervention feasible to implement?",
        "options": ["No", "Probably no", "Probably yes", "Yes", "Varies", "Don't know"],
    },
]
RECOMMENDATION_TYPES = [
    "Strong recommendation against the intervention",
    "Conditional recommendation against the intervention",
    "Conditional recommendation for either the intervention or the comparison",
    "Conditional recommendation for the intervention",
    "Strong recommendation for the intervention",
]
CONCLUSION_FIELDS = [
    "recommendation_type",
    "recommendation",
    "justification",
    "subgroup_considerations",
    "implementation",
    "monitoring",
    "research_priorities",
]


def corrected_covered_area(matrix: Sequence[Sequence[bool]]) -> float | None:
    """CCA = (N - r) / (r c - r): N inclusions in the study-by-review matrix, r studies, c reviews."""
    rows = [row for row in matrix if any(row)]
    if not rows:
        return None
    r, c = len(rows), len(rows[0])
    if c < 2:
        return None
    n = sum(sum(1 for cell in row if cell) for row in rows)
    return (n - r) / (r * c - r)


def overlap_label(cca: float | None) -> str:
    if cca is None:
        return "Not enough reviews to compare"
    if cca < 0.05:
        return "Slight overlap"
    if cca < 0.10:
        return "Moderate overlap"
    if cca < 0.15:
        return "High overlap"
    return "Very high overlap"


def limitations(assessments: Sequence[Mapping[str, Any]], notes: Sequence[str] = ()) -> list[str]:
    """Limitation statements from the GRADE ratings and analysis notes, without free-form invention."""
    statements: list[str] = []
    for assessment in assessments:
        outcome = assessment["outcome"]
        domains = assessment.get("domains") or {}
        for key, label in DOWNGRADE_DOMAINS.items():
            entry = domains.get(key) or {}
            rating = int(entry.get("rating", 0))
            if rating < 0:
                severity = "very serious" if rating <= -2 else "serious"
                rationale = f": {entry.get('rationale')}" if entry.get("rationale") else ""
                statements.append(f"{outcome}: certainty was rated down for {severity} {label.lower()}{rationale}")
    statements.extend(notes)
    return statements
