"""Appraisal tool algorithms, GRADE arithmetic (absolute effects, NNT, optimal information size, certainty, informative
statements, overlap), suggested ratings, number verification, and exported code. No database or R needed."""

import pytest

import grading
from appraisal_tools import TOOLS, evaluate, recommend_tools
from certainty_routes import unverified_numbers
from code_export import stata_script
from synthesis_data import BuiltDataset, pooling_assessment

# Appraisal algorithms


def test_rob2_randomization_domain_follows_the_published_algorithm():
    tool = TOOLS["rob2"]
    d1 = tool.domain("d1")
    assert d1 is not None and d1.algorithm is not None

    assert d1.algorithm({"1.1": "Y", "1.2": "Y", "1.3": "N"}) == "low"
    assert d1.algorithm({"1.1": "N", "1.2": "PY", "1.3": "PN"}) == "some_concerns"
    assert d1.algorithm({"1.1": "Y", "1.2": "N", "1.3": "N"}) == "high"
    assert d1.algorithm({"1.1": "Y", "1.2": "NI", "1.3": "Y"}) == "high"
    assert d1.algorithm({"1.1": "Y", "1.2": "Y"}) is None


def test_rob2_overall_is_high_when_any_domain_is_high():
    tool = TOOLS["rob2"]
    domains = {d.key: "low" for d in tool.domains}

    assert evaluate(tool, {}, domains).overall_suggestion == "low"
    assert evaluate(tool, {}, {**domains, "d3": "high"}).overall_suggestion == "high"


def test_amstar2_rates_confidence_from_critical_flaws():
    tool = TOOLS["amstar2"]
    answers = {str(i): "yes" for i in range(1, 17)}

    assert evaluate(tool, answers).overall_suggestion == "high"
    assert evaluate(tool, {**answers, "5": "no", "6": "no"}).overall_suggestion == "moderate"
    assert evaluate(tool, {**answers, "4": "no"}).overall_suggestion == "low"
    assert evaluate(tool, {**answers, "4": "no", "7": "no"}).overall_suggestion == "critically_low"


def test_newcastle_ottawa_stars_convert_to_ahrq_quality():
    tool = TOOLS["nos_cohort"]
    questions = [q.id for d in tool.domains for q in d.questions]
    answers = {q: ("2" if q.startswith("C") else "1") for q in questions}

    assert evaluate(tool, answers).overall_suggestion == "good"
    assert evaluate(tool, {**answers, "S1": "0", "S2": "0"}).overall_suggestion == "fair"
    assert evaluate(tool, {**answers, "C1": "0"}).overall_suggestion == "poor"


def test_tool_recommendations_follow_study_design():
    assert recommend_tools("Randomized controlled trial", "Systematic Review", "PICO")[0].tool == "rob2"
    assert recommend_tools("Cohort", "Systematic Review", "PECO")[0].tool == "robins_e"
    assert recommend_tools("", "Diagnostic test accuracy review", "PIRD")[0].tool == "quadas2"


# GRADE arithmetic


def test_absolute_effects_from_ratios_and_numbers_needed_to_treat():
    rr = grading.absolute_effects("RR", 0.8, 0.7, 0.91, 0.1)
    assert rr is not None
    assert (rr["baseline_per_1000"], rr["intervention_per_1000"], rr["difference_per_1000"]) == (100, 80, -20)
    assert (rr["intervention_per_1000_ci"], rr["difference_per_1000_ci"]) == ([70, 91], [-30, -9])
    assert rr["nnt"] == {"type": "NNTB", "value": 50, "interval": "34 to 112"}
    odds = grading.absolute_effects("OR", 2, 1, 4, 0.2)
    assert odds is not None and odds["intervention_per_1000"] == 333
    hazard = grading.absolute_effects("HR", 0.5, 0.25, 1, 0.2)
    assert hazard is not None and hazard["intervention_per_1000"] == 106
    assert grading.absolute_effects("MD", -1, -2, 0, 0.2) is None


def test_nnt_interval_passes_through_infinity_when_the_risk_difference_includes_zero():
    assert grading.number_needed_to_treat(-0.02, (-0.05, 0.01)) == {
        "type": "NNTB",
        "value": 50,
        "interval": "NNTB 20 to ∞ to NNTH 100",
    }


def test_optimal_information_size():
    assert grading.ois_binary(0.2, relative_risk_reduction=0.25) == 1812
    assert grading.ois_continuous(sd=10, delta=5) == 126


def test_certainty_combines_ratings_within_bounds():
    assert grading.certainty("high", {}) == "high"
    assert grading.certainty("high", {"risk_of_bias": {"rating": -1}, "imprecision": {"rating": -2}}) == "very_low"
    assert grading.certainty("low", {"large_effect": {"rating": 1}}) == "moderate"
    assert grading.certainty("low", {"large_effect": {"rating": 2}, "dose_response": {"rating": 1}}) == "high"


@pytest.mark.parametrize(
    ("level", "direction", "expected"),
    [
        ("high", "reduction", "Aspirin results in a reduction in myocardial infarction."),
        ("moderate", "reduction", "Aspirin likely results in a reduction in myocardial infarction."),
        ("low", "little_or_no_difference", "Aspirin may result in little to no difference in myocardial infarction."),
        (
            "very_low",
            "increase",
            "The evidence is very uncertain about the effect of aspirin on myocardial infarction.",
        ),
    ],
)
def test_informative_statements_use_grade_wording(level, direction, expected):
    assert grading.informative_statement(level, direction, "aspirin", "myocardial infarction") == expected


def test_corrected_covered_area():
    matrix = [[True, True], [True, False], [True, True]]

    assert grading.corrected_covered_area(matrix) == pytest.approx(2 / 3)
    assert grading.overlap_label(0.04) == "Slight overlap"
    assert grading.overlap_label(0.2) == "Very high overlap"


def test_suggested_ratings_flag_risk_of_bias_inconsistency_and_imprecision():
    results = {
        "summary": {
            "k": 4,
            "estimate": -0.1,
            "ci_lower": -0.4,
            "ci_upper": 0.2,
            "I2": 80,
            "pi_lower": -1,
            "pi_upper": 0.8,
        },
        "studies": [
            {"study": "A", "weight": 40},
            {"study": "B", "weight": 30},
            {"study": "C", "weight": 20},
            {"study": "D", "weight": 10},
        ],
    }
    rows = [
        {"study": "A", "rob": "high", "ai": 10, "n1i": 50, "ci": 12, "n2i": 50},
        {"study": "B", "rob": "high", "ai": 8, "n1i": 40, "ci": 9, "n2i": 40},
        {"study": "C", "rob": "low", "ai": 5, "n1i": 30, "ci": 6, "n2i": 30},
        {"study": "D", "rob": "low", "ai": 3, "n1i": 20, "ci": 4, "n2i": 20},
    ]

    suggestions = grading.suggested_ratings("pairwise", "RR", results, rows, None, "", "high")

    assert suggestions["risk_of_bias"]["suggested_rating"] == -2
    assert suggestions["inconsistency"]["suggested_rating"] == -2
    assert suggestions["imprecision"]["suggested_rating"] == -2
    assert suggestions["imprecision"]["inputs"]["participants"] == 280
    assert suggestions["publication_bias"]["suggested_rating"] == 0
    assert "Fewer than 10 studies" in suggestions["publication_bias"]["reason"]


def test_limitations_come_only_from_ratings_and_notes():
    statements = grading.limitations(
        [{"outcome": "Mortality", "domains": {"imprecision": {"rating": -1, "rationale": "Few events."}}}],
        ["Pairwise: fewer than 10 studies."],
    )

    assert statements == [
        "Mortality: certainty was rated down for serious imprecision: Few events.",
        "Pairwise: fewer than 10 studies.",
    ]


# Interpretation checks and exports


def test_numbers_not_in_the_summary_of_findings_are_flagged():
    rows = ["RR 0.80 (0.70 to 0.91)", "-20 per 1000 (-30 to -9)", "5 (1200)"]

    assert (
        unverified_numbers("Aspirin probably reduces heart attacks (RR 0.8; 20 fewer per 1000; 5 studies).", rows) == []
    )
    assert unverified_numbers("About 2% fewer people had a heart attack.", rows) == []
    assert unverified_numbers("Aspirin cut heart attacks by 45% in 12 trials.", rows) == ["12", "45"]


def test_stata_script_mirrors_the_pairwise_model():
    script = stata_script(
        {
            "measure": "RR",
            "data_type": "binary",
            "model": "random",
            "tau_method": "REML",
            "hksj": True,
            "prediction_interval": True,
            "publication_bias": {"egger": True},
        }
    )

    assert "meta esize ai treat_non ci control_non, esize(lnrratio) random(reml) studylabel(study)" in script
    assert "meta summarize, se(khartung) predinterval eform" in script
    assert "meta bias, egger" in script
    assert stata_script({"measure": "HR", "data_type": "dta"}) == ""


def test_pooling_check_recommends_swim_when_most_studies_lack_data():
    rows = [{"study_id": 1}, {"study_id": 2}]
    excluded = [{"study_id": i, "study": str(i), "reason": "No data"} for i in range(3, 6)]
    built = BuiltDataset(rows, excluded, {"model": "random", "hksj": True}, "locked", None)

    assert pooling_assessment("pairwise", built, {})["recommendation"] == "swim"
    assert (
        pooling_assessment("pairwise", BuiltDataset(rows[:1], [], {}, "locked", None), {})["recommendation"]
        == "not_possible"
    )
