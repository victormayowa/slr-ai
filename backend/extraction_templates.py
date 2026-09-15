"""Starting extraction forms for common review types. Applying a template adds its fields; nothing is removed.

Items follow the Cochrane Handbook checklist of items to consider in data collection (chapter 5), CHARMS for
prediction model studies (Moons et al. 2014), QUADAS-2 data items for diagnostic accuracy, and common qualitative
evidence synthesis extraction items.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldSpec:
    name: str
    section: str
    field_type: str
    per_arm: bool = False
    required: bool = False
    options: tuple[str, ...] = ()
    unit: str = ""
    help_text: str = ""
    outcome: str = ""
    timepoint: str = ""


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    description: str
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)


STUDY_DESIGNS = (
    "Randomized controlled trial (parallel)",
    "Cluster-randomized trial",
    "Crossover trial",
    "Non-randomized controlled study",
    "Prospective cohort",
    "Retrospective cohort",
    "Case-control",
    "Cross-sectional",
    "Other",
)

_CHARACTERISTICS = (
    FieldSpec("Study design", "Methods", "categorical", required=True, options=STUDY_DESIGNS),
    FieldSpec("Trial registration", "Methods", "text", help_text="Registry and number, for example NCT01234567"),
    FieldSpec("Country or countries", "Methods", "text"),
    FieldSpec("Setting", "Methods", "text"),
    FieldSpec("Recruitment period", "Methods", "text"),
    FieldSpec("Funding source", "Methods", "text"),
    FieldSpec("Conflicts of interest declared", "Methods", "boolean"),
    FieldSpec("Total participants randomized or enrolled", "Participants", "integer", required=True),
    FieldSpec("Inclusion criteria", "Participants", "long_text"),
    FieldSpec("Exclusion criteria", "Participants", "long_text"),
    FieldSpec("Mean or median age", "Participants", "number", unit="year"),
    FieldSpec("Female participants (%)", "Participants", "number"),
    FieldSpec("Follow-up duration", "Methods", "number", unit="week"),
)

_ARMS = (
    FieldSpec("Intervention or comparator", "Interventions", "long_text", per_arm=True, required=True),
    FieldSpec("Dose, frequency, and duration", "Interventions", "text", per_arm=True),
    FieldSpec("Co-interventions", "Interventions", "text", per_arm=True),
    FieldSpec("Participants randomized", "Interventions", "integer", per_arm=True, required=True),
    FieldSpec("Participants analysed", "Interventions", "integer", per_arm=True),
)

TEMPLATES: dict[str, Template] = {
    "intervention": Template(
        "intervention",
        "Intervention review",
        "Study characteristics, participants, and arm-level interventions. Add outcome fields from the analysis plan.",
        _CHARACTERISTICS + _ARMS,
    ),
    "nma": Template(
        "nma",
        "Network meta-analysis (arm-level)",
        "Arm-level treatments, doses, and sample sizes for every arm, as network meta-analysis needs.",
        _CHARACTERISTICS
        + _ARMS
        + (
            FieldSpec(
                "Treatment node (as coded for the network)", "Interventions", "text", per_arm=True, required=True
            ),
        ),
    ),
    "dta": Template(
        "dta",
        "Diagnostic test accuracy",
        "Index test, reference standard, threshold, and the 2×2 table.",
        (
            FieldSpec("Study design", "Methods", "categorical", required=True, options=STUDY_DESIGNS),
            FieldSpec("Participant selection (consecutive, random, convenience)", "Participants", "text"),
            FieldSpec("Target condition", "Participants", "text", required=True),
            FieldSpec("Index test", "Tests", "long_text", required=True),
            FieldSpec("Threshold for a positive index test", "Tests", "text"),
            FieldSpec("Reference standard", "Tests", "long_text", required=True),
            FieldSpec("Time between index test and reference standard", "Tests", "text"),
            FieldSpec("2×2 table", "Results", "dta_2x2", required=True),
            FieldSpec("Participants excluded from the analysis", "Results", "integer"),
        ),
    ),
    "prognostic": Template(
        "prognostic",
        "Prognostic factor",
        "Prognostic factor, outcome, adjusted and unadjusted estimates, and adjustment set.",
        (
            FieldSpec("Study design", "Methods", "categorical", required=True, options=STUDY_DESIGNS),
            FieldSpec("Source of data", "Methods", "text"),
            FieldSpec("Participants enrolled", "Participants", "integer", required=True),
            FieldSpec("Prognostic factor and how it was measured", "Factor", "long_text", required=True),
            FieldSpec("Outcome and how it was measured", "Outcome", "long_text", required=True),
            FieldSpec("Unadjusted estimate", "Results", "effect_estimate"),
            FieldSpec("Adjusted estimate", "Results", "effect_estimate", required=True),
            FieldSpec("Variables adjusted for", "Results", "long_text"),
            FieldSpec("Handling of missing data", "Analysis", "text"),
        ),
    ),
    "prediction_model": Template(
        "prediction_model",
        "Prediction model (CHARMS)",
        "The CHARMS checklist items for studies developing or validating prediction models.",
        (
            FieldSpec(
                "Source of data",
                "Source of data",
                "categorical",
                required=True,
                options=(
                    "Prospective cohort",
                    "Retrospective cohort",
                    "Randomized trial",
                    "Registry",
                    "Case-control",
                    "Other",
                ),
            ),
            FieldSpec("Participant eligibility and recruitment", "Participants", "long_text"),
            FieldSpec("Outcome to be predicted and prediction horizon", "Outcome", "long_text", required=True),
            FieldSpec("Candidate predictors", "Predictors", "long_text"),
            FieldSpec("Participants and outcome events", "Sample size", "dichotomous", required=True),
            FieldSpec("Handling of missing data", "Missing data", "text"),
            FieldSpec("Modelling method and predictor selection", "Model development", "long_text"),
            FieldSpec("Predictors in the final model", "Model development", "long_text"),
            FieldSpec("Discrimination (C statistic with 95% CI)", "Model performance", "effect_estimate"),
            FieldSpec("Calibration", "Model performance", "long_text"),
            FieldSpec(
                "Validation",
                "Model evaluation",
                "categorical",
                options=(
                    "Development only",
                    "Internal validation",
                    "External validation",
                    "Development and external validation",
                ),
            ),
        ),
    ),
    "qualitative": Template(
        "qualitative",
        "Qualitative evidence synthesis",
        "Methodology, context, and findings with supporting quotations.",
        (
            FieldSpec("Methodology", "Methods", "text", required=True),
            FieldSpec("Data collection", "Methods", "text"),
            FieldSpec("Analysis approach", "Methods", "text"),
            FieldSpec("Setting and context", "Context", "long_text"),
            FieldSpec("Participant characteristics", "Participants", "long_text"),
            FieldSpec("Findings (themes)", "Findings", "long_text", required=True),
            FieldSpec("Supporting quotations", "Findings", "long_text"),
            FieldSpec("Researcher reflexivity reported", "Methods", "boolean"),
        ),
    ),
}

# Analysis-plan effect measures and the field type their data are collected with.
_MEASURE_TYPES = {
    "risk ratio": ("dichotomous", True),
    "odds ratio": ("dichotomous", True),
    "risk difference": ("dichotomous", True),
    "mean difference": ("continuous", True),
    "standardized mean difference": ("continuous", True),
    "hazard ratio": ("effect_estimate", False),
}


def outcome_fields(analysis_plan: dict) -> list[FieldSpec]:
    """A data field for each pre-specified outcome, typed by its effect measure (arm-level where the measure allows)."""
    specs = []
    for outcome in analysis_plan.get("outcomes") or []:
        name = str(outcome.get("name") or "").strip()
        if not name:
            continue
        measure = str(outcome.get("measure") or "").strip().lower()
        field_type, per_arm = _MEASURE_TYPES.get(measure, ("effect_estimate", False))
        timepoint = str(outcome.get("timepoint") or "").strip()
        priority = str(outcome.get("priority") or "").capitalize()
        label = f"{name} ({timepoint})" if timepoint else name
        specs.append(
            FieldSpec(
                f"Outcome: {label}"[:200],
                "Outcomes",
                field_type,
                per_arm=per_arm,
                required=outcome.get("priority") == "primary",
                outcome=name[:200],
                timepoint=timepoint[:100],
                help_text=f"{priority} outcome; effect measure: {measure or 'not set'}",
            )
        )
    return specs


def catalog() -> list[dict]:
    return [
        {"key": t.key, "label": t.label, "description": t.description, "fields": [f.name for f in t.fields]}
        for t in TEMPLATES.values()
    ] + [
        {
            "key": "outcomes",
            "label": "Outcomes from the analysis plan",
            "description": "A typed data field for each pre-specified outcome.",
            "fields": [],
        }
    ]
