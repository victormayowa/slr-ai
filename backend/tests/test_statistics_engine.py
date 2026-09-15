"""The R analysis templates, run directly: results are checked against published reference values where they exist
(metafor's BCG vaccine data set) and for structure otherwise. Needs R (backend/scripts/setup_r_env.sh)."""

import asyncio
import random

import pytest

from appraisal_tools import TOOLS
from stats_engine import ANALYSIS_TYPES, StatsRunError, engine_status, render_robvis, run_analysis

pytestmark = pytest.mark.skipif(
    not engine_status().available or bool(engine_status().missing()),
    reason="R or its packages aren't installed: run backend/scripts/setup_r_env.sh and set RSCRIPT_PATH",
)

# dat.bcg (Colditz et al. 1994): trial, TB cases and totals in the vaccinated and control groups, latitude, allocation.
BCG = [
    ("Aronson 1948", 4, 123, 11, 139, 44, "random"),
    ("Ferguson & Simes 1949", 6, 306, 29, 303, 55, "random"),
    ("Rosenthal 1960", 3, 231, 11, 220, 42, "random"),
    ("Hart & Sutherland 1977", 62, 13598, 248, 12867, 52, "random"),
    ("Frimodt-Moller 1973", 33, 5069, 47, 5808, 13, "alternate"),
    ("Stein & Aronson 1953", 180, 1541, 372, 1451, 44, "alternate"),
    ("Vandiviere 1973", 8, 2545, 10, 629, 19, "random"),
    ("TPT Madras 1980", 505, 88391, 499, 88391, 13, "random"),
    ("Coetzee & Berjak 1968", 29, 7499, 45, 7277, 27, "random"),
    ("Rosenthal 1961", 17, 1716, 65, 1665, 42, "systematic"),
    ("Comstock 1974", 186, 50634, 141, 27338, 18, "systematic"),
    ("Comstock & Webster 1969", 5, 2498, 3, 2341, 33, "systematic"),
    ("Comstock 1976", 27, 16913, 29, 17854, 33, "systematic"),
]


def bcg_rows():
    return [
        {
            "study_id": i,
            "study": name,
            "ai": a,
            "n1i": n1,
            "ci": c,
            "n2i": n2,
            "subgroup": alloc,
            "mod_1": lat,
            "rob": "high" if alloc == "systematic" else "low",
        }
        for i, (name, a, n1, c, n2, lat, alloc) in enumerate(BCG, start=1)
    ]


def spec(**overrides):
    base = {
        "measure": "RR",
        "data_type": "binary",
        "ratio": True,
        "model": "random",
        "tau_method": "REML",
        "hksj": False,
        "prediction_interval": True,
        "level": 95,
        "zero_correction": 0.5,
        "subgroup": None,
        "moderators": [],
        "sensitivity": {},
        "publication_bias": {},
        "plot_formats": ["svg"],
        "seed": 1,
    }
    return {**base, **overrides}


def run(kind, spec_values, rows, extra=None):
    template, needed = ANALYSIS_TYPES[kind]
    return run_analysis(template, spec_values, rows, needed, extra)


def plot_names(output):
    return {plot.name for plot in output.plots}


def test_pairwise_random_effects_matches_metafor_reference_values():
    output = run(
        "pairwise",
        spec(
            subgroup={"label": "Allocation", "prespecified": True},
            moderators=[{"column": "mod_1", "label": "Absolute latitude", "type": "numeric"}],
            sensitivity={
                "leave_one_out": True,
                "influence": True,
                "exclude_high_risk_of_bias": True,
                "alternative_estimators": ["DL"],
            },
            publication_bias={
                "funnel": True,
                "contour": True,
                "egger": True,
                "begg": True,
                "trim_fill": True,
                "pet_peese": True,
                "selection_model": True,
            },
        ),
        bcg_rows(),
    )

    summary = output.results["summary"]
    # metafor documentation: rma(yi, vi, data = dat.bcg RR) gives -0.7145 (SE 0.1798), tau^2 0.3132, I^2 92.22%.
    assert summary["k"] == 13
    assert summary["estimate"] == pytest.approx(-0.7145, abs=5e-4)
    assert summary["se"] == pytest.approx(0.1798, abs=5e-4)
    assert summary["tau2"] == pytest.approx(0.3132, abs=1e-3)
    assert summary["I2"] == pytest.approx(92.22, abs=0.05)
    assert summary["exp_estimate"] == pytest.approx(0.4894, abs=5e-4)
    assert summary["pi_lower"] < summary["ci_lower"]
    slope = output.results["meta_regression"]["coefficients"][1]
    # Mixed-effects meta-regression on absolute latitude: slope -0.0291 (SE 0.0072).
    assert (slope["estimate"], slope["se"]) == (pytest.approx(-0.0291, abs=5e-4), pytest.approx(0.0072, abs=5e-4))
    sensitivity = output.results["sensitivity"]
    assert len(sensitivity["leave_one_out"]) == 13
    assert sensitivity["alternative_estimators"][0]["tau2"] == pytest.approx(0.3088, abs=1e-3)
    assert len(sensitivity["excluding_high_risk_of_bias"]["excluded"]) == 4
    assert output.results["subgroups"]["test_for_differences"]["df"] == 2
    bias = output.results["publication_bias"]
    assert {"egger", "begg", "trim_and_fill", "pet_peese", "selection_model"} <= set(bias)
    assert not any("error" in value for value in bias.values() if isinstance(value, dict))
    assert {"forest", "funnel", "influence", "trim_and_fill", "meta_regression"} <= plot_names(output)
    assert output.r_version.startswith("R version 4")
    assert output.packages["metafor"]
    assert "sessionInfo" in output.session_info or "R version" in output.session_info


def test_mantel_haenszel_and_hartung_knapp_options_run():
    mh = run("pairwise", spec(model="mantel_haenszel", measure="OR"), bcg_rows())
    knha = run("pairwise", spec(hksj=True), bcg_rows())

    assert mh.results["summary"]["method"] == "MH"
    assert knha.results["summary"]["test"] == "knha"
    assert knha.results["summary"]["se"] > 0


def test_analysis_errors_are_reported_in_plain_language():
    with pytest.raises(StatsRunError, match="At least two studies"):
        run("pairwise", spec(), bcg_rows()[:1])


def test_network_meta_analysis_ranks_treatments():
    arms = [
        ("S1", "Aspirin", 10, 100), ("S1", "Placebo", 15, 100),
        ("S2", "Placebo", 12, 120), ("S2", "Clopidogrel", 20, 115),
        ("S3", "Aspirin", 14, 90), ("S3", "Clopidogrel", 18, 95),
        ("S4", "Placebo", 16, 80), ("S4", "Aspirin", 12, 85), ("S4", "Clopidogrel", 8, 82),
        ("S5", "Placebo", 19, 110), ("S5", "Aspirin", 11, 100),
    ]  # fmt: skip
    rows = [{"study_id": i, "study": s, "treatment": t, "events": e, "n": n} for i, (s, t, e, n) in enumerate(arms)]

    output = run("nma", spec(measure="OR", reference_treatment="Placebo", small_values="desirable"), rows)

    assert output.results["model"]["treatments"] == 3
    assert len(output.results["league"]) == 6
    assert {row["treatment"] for row in output.results["p_scores"]} == {"Aspirin", "Placebo", "Clopidogrel"}
    assert "network" in plot_names(output)


def test_diagnostic_accuracy_bivariate_model():
    tables = [(45, 5, 10, 90), (30, 10, 8, 70), (60, 15, 20, 150), (25, 5, 5, 40), (50, 8, 12, 110)]
    rows = [
        {"study_id": i, "study": f"Study {i}", "TP": tp, "FN": fn, "FP": fp, "TN": tn}
        for i, (tp, fn, fp, tn) in enumerate(tables, start=1)
    ]

    output = run("dta", spec(data_type="dta"), rows)

    summary = output.results["summary"]
    assert 0.75 < summary["sensitivity"] < 0.95
    assert 0.8 < summary["specificity"] < 0.95
    assert summary["sensitivity_ci"][0] < summary["sensitivity"] < summary["sensitivity_ci"][1]
    assert len(output.results["studies"]) == 5
    assert "sroc" in plot_names(output)


def test_bayesian_meta_analysis():
    output = run("bayesian", spec(tau_prior_scale=0.5, mu_prior_sd=None), bcg_rows())

    summary = output.results["summary"]
    assert summary["mu_lower"] < summary["mu_median"] < summary["mu_upper"] < 0
    assert summary["tau_median"] > 0
    assert summary["probability_effect_below_zero"] > 0.95
    assert output.results["priors"]["tau"] == "half-normal(scale = 0.5)"


def test_dependent_effects_with_robust_variance_estimation():
    effects = [
        (1, "Mortality", 0.8, 0.6, 1.05), (1, "Stroke", 0.7, 0.5, 0.98),
        (2, "Mortality", 0.9, 0.7, 1.15), (2, "Stroke", 0.85, 0.6, 1.2),
        (3, "Mortality", 0.75, 0.55, 1.02), (4, "Mortality", 0.95, 0.8, 1.13),
        (4, "Stroke", 0.88, 0.7, 1.1), (5, "Mortality", 0.7, 0.5, 0.98), (6, "Stroke", 0.82, 0.62, 1.08),
    ]  # fmt: skip
    rows = [
        {"study_id": sid, "study": f"Study {sid}", "effect": name, "estimate": e, "ci_lower": lo, "ci_upper": hi}
        for sid, name, e, lo, hi in effects
    ]

    output = run("rve", spec(data_type="generic"), rows)

    summary = output.results["summary"]
    assert (summary["k_effects"], summary["k_studies"]) == (9, 6)
    assert summary["exp_ci_lower"] < summary["exp_estimate"] < summary["exp_ci_upper"]
    assert summary["df"] > 0


def test_individual_participant_data_one_and_two_stage_models():
    generator = random.Random(7)
    lines = ["study,treatment,outcome,age"]
    for study in ("Trial A", "Trial B", "Trial C", "Trial D"):
        for person in range(200):
            treatment = person % 2
            risk = 0.35 if treatment == 0 else 0.22
            lines.append(f"{study},{treatment},{int(generator.random() < risk)},{round(generator.gauss(0, 1), 2)}")
    rows = [
        {"study_id": i, "study": s, "participants": 200}
        for i, s in enumerate(("Trial A", "Trial B", "Trial C", "Trial D"))
    ]

    output = run("ipd", spec(outcome_type="binary", adjust_for=["age"]), rows, {"ipd.csv": "\n".join(lines).encode()})

    assert len(output.results["two_stage"]["studies"]) == 4
    assert output.results["two_stage"]["estimate"] < 0
    one_stage = output.results["one_stage"]
    assert one_stage["participants"] == 800
    assert one_stage["exp_ci_lower"] < one_stage["exp_estimate"] < one_stage["exp_ci_upper"]


def test_synthesis_without_meta_analysis_counts_directions():
    rows = [
        {"study_id": 1, "study": "A", "direction": "benefit", "outcome_domain": "Pain"},
        {"study_id": 2, "study": "B", "direction": "benefit", "outcome_domain": "Pain"},
        {"study_id": 3, "study": "C", "direction": "harm", "outcome_domain": "Function"},
        {"study_id": 4, "study": "D", "direction": "no_clear_difference", "outcome_domain": "Function"},
    ]

    output = run("swim", spec(data_type="swim"), rows)

    votes = output.results["vote_counting"]
    assert (votes["benefit"], votes["harm"], votes["no_clear_difference"]) == (2, 1, 1)
    assert 0 < votes["p_value"] <= 1
    assert "effect_direction" in plot_names(output)


def test_risk_of_bias_traffic_light_plot():
    tool = TOOLS["rob2"]
    domains = [{"key": d.key, "label": d.label, "kind": d.kind} for d in tool.domains]
    rows = [
        {"study": "Aronson 1948", "outcome": "TB", "domains": {d.key: "low" for d in tool.domains}, "overall": "low"},
        {
            "study": "Comstock 1974",
            "outcome": "TB",
            "domains": {**{d.key: "low" for d in tool.domains}, "d2": "high"},
            "overall": "high",
        },
    ]

    for kind in ("traffic_light", "summary"):
        content, media_type = asyncio.run(render_robvis(tool, domains, rows, kind, "svg"))
        assert media_type == "image/svg+xml"
        assert b"<svg" in content[:500]
