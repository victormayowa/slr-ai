"""Statistical conversions, units, stopping rules, agreement, prioritization, value types, and registry numbers."""

import math
from statistics import NormalDist

import pytest

import active_learning
import agreement
import models
import stats_convert
import stopping
import units
from extraction_values import InvalidValue, missing_components, validate, values_agree
from studies import registry_ids


def test_mean_and_sd_from_medians_follow_the_published_formulas():
    n = 50
    xi = 2 * NormalDist().inv_cdf((n - 0.375) / (n + 0.25))
    weight = 4 / (4 + n**0.75)

    from_range = stats_convert.mean_sd_from_median_range(10, 2, 30, n)

    assert from_range.values["mean"] == pytest.approx(weight * 16 + (1 - weight) * 10)
    assert from_range.values["sd"] == pytest.approx(28 / xi)
    assert stats_convert.mean_sd_from_median_iqr(5, 3, 7, 100).values["mean"] == pytest.approx(5)
    # For normal data the IQR is 1.349 SDs, so a large sample recovers an SD of 1.
    assert stats_convert.mean_sd_from_median_iqr(0, -0.6745, 0.6745, 100_000).values["sd"] == pytest.approx(1, rel=1e-3)
    five = stats_convert.mean_sd_from_five_numbers(-1.54, -0.67, 0, 0.67, 1.54, 10)
    assert five.values["mean"] == pytest.approx(0)
    assert 0.9 < five.values["sd"] < 1.1
    with pytest.raises(stats_convert.ConversionError):
        stats_convert.mean_sd_from_median_iqr(9, 3, 7, 40)


def test_confidence_intervals_standard_errors_and_p_values():
    from_ci = stats_convert.sd_from_ci(8, 12, 25)
    assert from_ci.values["t"] == pytest.approx(2.0639, abs=1e-3)
    assert from_ci.values["sd"] == pytest.approx(5 * 4 / (2 * 2.0639), rel=1e-3)
    ratio = stats_convert.se_from_ci_ratio(0.8, 0.64, 1.0)
    assert ratio.values["se"] == pytest.approx(-math.log(0.64) / (2 * 1.959964), rel=1e-6)
    assert stats_convert.se_from_p_value(2.0, 0.05, ratio=False).values["se"] == pytest.approx(2 / 1.959964, rel=1e-5)
    assert stats_convert.sd_from_se(2, 16).values["sd"] == 8
    with pytest.raises(stats_convert.ConversionError):
        stats_convert.se_from_ci_ratio(1.2, 0.8, 1.1)
    with pytest.raises(stats_convert.ConversionError, match="n is required"):
        stats_convert.convert("sd_from_se", {"se": 1})


def test_combining_groups_and_adjusting_cluster_trials():
    combined = stats_convert.combine_groups(10, 5, 1, 10, 7, 1).values
    assert (combined["n"], combined["mean"]) == (20, 6)
    assert combined["sd"] == pytest.approx(math.sqrt((9 + 9 + 100 / 20 * (25 + 49 - 70)) / 19))
    cluster = stats_convert.cluster_adjust(1000, 21, 0.05, events=100).values
    assert cluster["design_effect"] == pytest.approx(2.0)
    assert (cluster["effective_n"], cluster["effective_events"]) == (pytest.approx(500), pytest.approx(50))


def test_units_convert_within_dimensions_and_for_listed_analytes():
    assert units.convert(180, "mg/dL", "mmol/L", "glucose").value == pytest.approx(180 / 18.016)
    assert units.convert(5.2, "mmol/L", "mg/dL", "total_cholesterol").value == pytest.approx(5.2 * 38.67)
    assert units.convert(2, "weeks", "days").value == pytest.approx(14)
    assert units.convert(154, "lbs", "kg").value == pytest.approx(69.853, rel=1e-4)
    assert units.convert(37, "°C", "°F").value == pytest.approx(98.6)
    with pytest.raises(units.UnitError):
        units.convert(1, "mg/dL", "mmol/L")
    with pytest.raises(units.UnitError):
        units.convert(1, "kg", "week")


def test_the_hypergeometric_stopping_test():
    # Population 10 with 4 relevant, 3 drawn: P(X <= 1) = (C(6,3) + 4 C(6,2)) / C(10,3).
    assert stopping.hypergeom_cdf(1, 10, 4, 3) == pytest.approx(80 / 120)

    early = stopping.hypergeometric_test([True, False, True], total_records=1000)
    late = stopping.hypergeometric_test([True] * 20 + [False] * 900, total_records=1000)

    assert early.can_stop is False
    assert late.can_stop is True
    assert late.p_value is not None and late.p_value < 0.05
    assert late.heuristic_met is True
    assert stopping.hypergeometric_test([False] * 10, total_records=50).explanation.startswith("No relevant")


def test_kappa_pabak_and_wilson_intervals():
    pairs = (
        [("include", "include")] * 20
        + [("exclude", "exclude")] * 15
        + [("include", "exclude")] * 5
        + [("exclude", "include")] * 10
    )

    result = agreement.cohens_kappa(pairs, ("include", "exclude"))

    assert result.observed == pytest.approx(0.7)
    assert result.kappa == pytest.approx(0.4)
    assert result.pabak == pytest.approx(0.4)
    assert agreement.wilson_interval(0, 0) is None
    low, high = agreement.wilson_interval(8, 10) or (0, 0)
    assert low < 0.8 < high


def test_prioritization_ranks_records_like_included_ones_first():
    decided = [
        "aspirin randomized trial adults cardiovascular prevention",
        "aspirin placebo randomized cardiovascular events",
        "surgical knee replacement cohort",
        "knee arthroplasty outcomes cohort",
    ]
    pending = ["randomized aspirin trial for cardiovascular prevention", "knee replacement surgery cohort study"]

    model = active_learning.train(decided, [True, True, False, False], decided + pending)

    assert model.score(pending[0]) > 0.5 > model.score(pending[1])
    assert "aspirin" in model.top_terms()
    with pytest.raises(active_learning.NotEnoughDecisions):
        active_learning.train(decided[:2], [True, True], decided)


def _field(field_type: str, options: list[str] | None = None) -> models.ExtractionField:
    return models.ExtractionField(name="Field", field_type=field_type, options=options or [], settings={})


def test_extraction_values_are_validated_and_compared_with_tolerance():
    continuous = _field("continuous")

    assert validate(continuous, {"mean": "5.10", "sd": 1.2, "n": "60"}) == {"mean": 5.1, "sd": 1.2, "n": 60}
    with pytest.raises(InvalidValue):
        validate(continuous, {"n": 60.5})
    with pytest.raises(InvalidValue):
        validate(_field("dichotomous"), {"events": 11, "total": 10})
    with pytest.raises(InvalidValue):
        validate(_field("categorical", ["RCT", "Cohort"]), {"choice": "Case series"})
    assert missing_components(continuous, {"mean": 5, "n": 60}) == ["sd"]
    assert values_agree(continuous, ({"mean": 5.0, "n": 60}, False), ({"mean": 5.04, "n": 60}, False), 0, 0.01)
    assert not values_agree(continuous, ({"mean": 5.0, "n": 60}, False), ({"mean": 5.2, "n": 60}, False), 0, 0.01)
    assert values_agree(_field("text"), ({"text": "Placebo "}, False), ({"text": "placebo"}, False))
    assert values_agree(_field("text"), (None, True), (None, True))


def test_trial_registration_numbers_are_found_and_normalized():
    text = (
        "Registered at ClinicalTrials.gov (nct01234567) and ISRCTN12345678; "
        "EudraCT 2010-012345-67; ACTRN12618000123456."
    )

    assert registry_ids(text) == ["NCT01234567", "ISRCTN12345678", "2010-012345-67", "ACTRN12618000123456"]
