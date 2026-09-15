"""Stopping rules for prioritized screening.

The statistical rule is the hypergeometric test of Callaghan & Müller-Hansen (2020, Systematic Reviews 9:273). Treat
the records screened since some point as a random sample of the records that were unscreened at that point. If recall
were below the target, at least K relevant records would be in that population; the test gives the probability of
finding as few relevant records as were found in the sample under that assumption. Every starting point is tried and
the smallest p-value is reported. When p is below alpha, screening can stop with the chosen confidence that recall has
reached the target. The test assumes the sample is no better than random, which holds for records screened in random
order and is conservative when the most likely relevant records were screened first.

A heuristic rule (a run of consecutive irrelevant records) is reported alongside, but gives no statistical guarantee.
"""

import math
from dataclasses import asdict, dataclass, field


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hypergeom_cdf(k: int, population: int, successes: int, draws: int) -> float:
    """P(X <= k) for X ~ Hypergeometric(population, successes in the population, draws)."""
    low = max(0, draws - (population - successes))
    high = min(k, successes, draws)
    if high < low:
        return 0.0
    total = _log_comb(population, draws)
    terms = [
        _log_comb(successes, i) + _log_comb(population - successes, draws - i) - total for i in range(low, high + 1)
    ]
    largest = max(terms)
    return min(1.0, math.exp(largest) * sum(math.exp(term - largest) for term in terms))


@dataclass
class StoppingResult:
    method: str
    can_stop: bool
    total_records: int
    screened: int
    relevant_found: int
    unscreened: int
    recall_target: float
    alpha: float
    p_value: float | None = None
    # Records in the sample that gave the smallest p-value.
    sample_size: int | None = None
    consecutive_irrelevant: int = 0
    heuristic_threshold: int = 0
    heuristic_met: bool = False
    explanation: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def hypergeometric_test(
    sequence: list[bool], total_records: int, recall_target: float = 0.95, alpha: float = 0.05
) -> StoppingResult:
    """Test whether screening can stop. `sequence` holds each screened record's relevance in the order screened."""
    screened, found = len(sequence), sum(sequence)
    unscreened = max(0, total_records - screened)
    consecutive = 0
    for relevant in reversed(sequence):
        if relevant:
            break
        consecutive += 1
    threshold = max(50, math.ceil(0.05 * total_records))
    result = StoppingResult(
        method="hypergeometric",
        can_stop=False,
        total_records=total_records,
        screened=screened,
        relevant_found=found,
        unscreened=unscreened,
        recall_target=recall_target,
        alpha=alpha,
        consecutive_irrelevant=consecutive,
        heuristic_threshold=threshold,
        heuristic_met=consecutive >= threshold,
    )
    if unscreened == 0:
        result.can_stop, result.p_value = True, 0.0
        result.explanation = "Every record has been screened."
        return result
    if found == 0:
        result.explanation = "No relevant records have been found yet, so recall can't be estimated."
        return result

    # Relevant records that would have to be unfound for recall to fall below the target.
    missing = math.floor(found / recall_target) + 1 - found
    suffix_relevant = [0] * (screened + 1)
    for index in range(screened - 1, -1, -1):
        suffix_relevant[index] = suffix_relevant[index + 1] + int(sequence[index])
    best_p, best_size = 1.0, None
    for start in range(screened):
        draws = screened - start
        relevant_in_sample = suffix_relevant[start]
        population = draws + unscreened
        successes = relevant_in_sample + missing
        if successes > population:
            continue
        p = hypergeom_cdf(relevant_in_sample, population, successes, draws)
        if p < best_p:
            best_p, best_size = p, draws
    result.p_value, result.sample_size = best_p, best_size
    result.can_stop = best_p < alpha
    confidence = round((1 - alpha) * 100)
    if result.can_stop:
        result.explanation = (
            f"Recall is at least {recall_target:.0%} with {confidence}% confidence (p = {best_p:.3g}), assuming the "
            f"last {best_size} screened records are no better than a random sample."
        )
    else:
        result.explanation = (
            f"Recall of {recall_target:.0%} can't yet be confirmed with {confidence}% confidence (p = {best_p:.3g}). "
            "Keep screening."
        )
    return result
