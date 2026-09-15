"""Reporting guideline checklists for appraising how completely included studies report their methods and results.

Item labels are short topic descriptions written for OmniReview, numbered as in each guideline; reviewers follow the
official checklist and explanation papers linked from each checklist.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Checklist:
    key: str
    label: str
    study_types: str
    reference: str
    url: str
    items: tuple[tuple[str, str, str], ...]  # (item id, section, topic)


def _items(rows: list[tuple[str, str, str]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(rows)


CONSORT = Checklist(
    "consort",
    "CONSORT 2010",
    "Randomized trials",
    "Schulz KF, Altman DG, Moher D. CONSORT 2010 Statement. BMJ 2010;340:c332.",
    "https://www.consort-spirit.org/",
    _items(
        [
            ("1a", "Title and abstract", "Identified as a randomized trial in the title"),
            ("1b", "Title and abstract", "Structured summary of design, methods, results, and conclusions"),
            ("2a", "Introduction", "Scientific background and rationale"),
            ("2b", "Introduction", "Specific objectives or hypotheses"),
            ("3a", "Methods", "Trial design and allocation ratio"),
            ("3b", "Methods", "Important changes to methods after trial start, with reasons"),
            ("4a", "Methods", "Eligibility criteria for participants"),
            ("4b", "Methods", "Settings and locations of data collection"),
            ("5", "Methods", "Interventions for each group, with enough detail to replicate"),
            ("6a", "Methods", "Pre-specified primary and secondary outcomes"),
            ("6b", "Methods", "Changes to trial outcomes after the trial started, with reasons"),
            ("7a", "Methods", "How the sample size was determined"),
            ("7b", "Methods", "Interim analyses and stopping guidelines"),
            ("8a", "Methods", "Method used to generate the random allocation sequence"),
            ("8b", "Methods", "Type of randomization and any restriction"),
            ("9", "Methods", "Mechanism used to conceal the allocation sequence"),
            ("10", "Methods", "Who generated the sequence, enrolled participants, and assigned interventions"),
            ("11a", "Methods", "Who was blinded after assignment, and how"),
            ("11b", "Methods", "Similarity of interventions"),
            ("12a", "Methods", "Statistical methods for primary and secondary outcomes"),
            ("12b", "Methods", "Methods for additional analyses"),
            ("13a", "Results", "Participants randomized, receiving treatment, and analysed, per group"),
            ("13b", "Results", "Losses and exclusions after randomization, with reasons"),
            ("14a", "Results", "Dates of recruitment and follow-up"),
            ("14b", "Results", "Why the trial ended or was stopped"),
            ("15", "Results", "Baseline demographic and clinical characteristics per group"),
            ("16", "Results", "Number analysed in each group, and by original assigned groups"),
            ("17a", "Results", "Results per group with effect size and precision for each outcome"),
            ("17b", "Results", "Absolute and relative effect sizes for binary outcomes"),
            ("18", "Results", "Results of other analyses, distinguishing pre-specified from exploratory"),
            ("19", "Results", "All important harms or unintended effects per group"),
            ("20", "Discussion", "Limitations, sources of bias, imprecision, and multiplicity"),
            ("21", "Discussion", "Generalisability of the findings"),
            ("22", "Discussion", "Interpretation consistent with results, balancing benefits and harms"),
            ("23", "Other information", "Registration number and name of the trial registry"),
            ("24", "Other information", "Where the full trial protocol can be accessed"),
            ("25", "Other information", "Sources of funding and role of funders"),
        ]
    ),
)

STROBE = Checklist(
    "strobe",
    "STROBE",
    "Observational studies (cohort, case-control, cross-sectional)",
    "von Elm E, et al. The STROBE Statement. Lancet 2007;370:1453-7.",
    "https://www.strobe-statement.org/",
    _items(
        [
            ("1", "Title and abstract", "Design indicated, and informative, balanced abstract"),
            ("2", "Introduction", "Scientific background and rationale"),
            ("3", "Introduction", "Specific objectives, including pre-specified hypotheses"),
            ("4", "Methods", "Key elements of study design"),
            ("5", "Methods", "Setting, locations, and relevant dates"),
            ("6", "Methods", "Eligibility criteria, sources, and selection of participants"),
            ("7", "Methods", "Outcomes, exposures, predictors, confounders, and effect modifiers defined"),
            ("8", "Methods", "Data sources and measurement methods for each variable"),
            ("9", "Methods", "Efforts to address potential sources of bias"),
            ("10", "Methods", "How the study size was arrived at"),
            ("11", "Methods", "How quantitative variables were handled in the analyses"),
            (
                "12",
                "Methods",
                "Statistical methods, including confounding, subgroups, missing data, and sensitivity analyses",
            ),
            ("13", "Results", "Numbers of individuals at each stage of the study"),
            ("14", "Results", "Characteristics of participants and missing data per variable"),
            ("15", "Results", "Numbers of outcome events or summary measures"),
            ("16", "Results", "Unadjusted and adjusted estimates with precision"),
            ("17", "Results", "Other analyses, such as subgroups, interactions, and sensitivity analyses"),
            ("18", "Discussion", "Key results with reference to study objectives"),
            ("19", "Discussion", "Limitations, including sources of bias or imprecision"),
            ("20", "Discussion", "Cautious overall interpretation"),
            ("21", "Discussion", "Generalisability of the results"),
            ("22", "Other information", "Funding source and role of funders"),
        ]
    ),
)

STARD = Checklist(
    "stard",
    "STARD 2015",
    "Diagnostic accuracy studies",
    "Bossuyt PM, et al. STARD 2015: an updated list of essential items for reporting diagnostic accuracy studies. "
    "BMJ 2015;351:h5527.",
    "https://www.equator-network.org/reporting-guidelines/stard/",
    _items(
        [
            ("1", "Title or abstract", "Identified as a diagnostic accuracy study with at least one accuracy measure"),
            ("2", "Abstract", "Structured summary of design, methods, results, and conclusions"),
            ("3", "Introduction", "Scientific and clinical background, including intended use of the index test"),
            ("4", "Introduction", "Study objectives and hypotheses"),
            ("5", "Methods", "Prospective or retrospective data collection"),
            ("6", "Methods", "Eligibility criteria"),
            ("7", "Methods", "Basis on which potentially eligible participants were identified"),
            ("8", "Methods", "Where and when potentially eligible participants were identified"),
            ("9", "Methods", "Consecutive, random, or convenience series"),
            ("10a", "Methods", "Index test, in enough detail to replicate"),
            ("10b", "Methods", "Reference standard, in enough detail to replicate"),
            ("11", "Methods", "Rationale for choosing the reference standard"),
            ("12a", "Methods", "Definition and rationale for positivity cut-offs of the index test"),
            ("12b", "Methods", "Definition and rationale for positivity cut-offs of the reference standard"),
            ("13a", "Methods", "Clinical information and reference standard results available to index test assessors"),
            ("13b", "Methods", "Clinical information and index test results available to reference standard assessors"),
            ("14", "Methods", "Methods for estimating or comparing measures of diagnostic accuracy"),
            ("15", "Methods", "Handling of indeterminate results"),
            ("16", "Methods", "Handling of missing data on the index test and reference standard"),
            ("17", "Methods", "Analyses of variability in diagnostic accuracy"),
            ("18", "Methods", "Intended sample size and how it was determined"),
            ("19", "Results", "Flow of participants, using a diagram"),
            ("20", "Results", "Baseline demographic and clinical characteristics"),
            ("21a", "Results", "Distribution of severity of disease in those with the target condition"),
            ("21b", "Results", "Distribution of alternative diagnoses in those without the target condition"),
            ("22", "Results", "Time interval and interventions between index test and reference standard"),
            ("23", "Results", "Cross tabulation of index test results by reference standard results"),
            ("24", "Results", "Estimates of diagnostic accuracy and their precision"),
            ("25", "Results", "Adverse events from performing the index test or reference standard"),
            ("26", "Discussion", "Study limitations, including sources of bias and imprecision"),
            ("27", "Discussion", "Implications for practice, including intended use and clinical role"),
            ("28", "Other information", "Registration number and name of registry"),
            ("29", "Other information", "Where the full study protocol can be accessed"),
            ("30", "Other information", "Sources of funding and role of funders"),
        ]
    ),
)

TRIPOD = Checklist(
    "tripod",
    "TRIPOD",
    "Prediction model development and validation studies",
    "Collins GS, et al. Transparent reporting of a multivariable prediction model for individual prognosis or "
    "diagnosis (TRIPOD). BMJ 2015;350:g7594.",
    "https://www.tripod-statement.org/",
    _items(
        [
            ("1", "Title and abstract", "Identifies development or validation, target population, and outcome"),
            (
                "2",
                "Title and abstract",
                "Summary of objectives, design, setting, participants, sample size, predictors, outcome, analysis, "
                "results, and conclusions",
            ),
            ("3a", "Introduction", "Medical context and rationale, with references to existing models"),
            ("3b", "Introduction", "Objectives, including development and/or validation"),
            ("4a", "Methods", "Study design or source of data"),
            ("4b", "Methods", "Key study dates, including start of accrual, end of accrual, and end of follow-up"),
            ("5a", "Methods", "Key elements of the study setting"),
            ("5b", "Methods", "Eligibility criteria for participants"),
            ("5c", "Methods", "Details of treatments received, if relevant"),
            ("6a", "Methods", "Outcome predicted, including how and when assessed"),
            ("6b", "Methods", "Actions to blind assessment of the outcome"),
            ("7a", "Methods", "All predictors used, including how and when measured"),
            ("7b", "Methods", "Actions to blind assessment of predictors"),
            ("8", "Methods", "How the study size was arrived at"),
            ("9", "Methods", "How missing data were handled"),
            ("10a", "Methods", "How predictors were handled in the analyses"),
            ("10b", "Methods", "Type of model, model-building procedures, and internal validation"),
            ("10c", "Methods", "For validation, how predictions were calculated"),
            ("10d", "Methods", "Measures used to assess model performance"),
            ("10e", "Methods", "Any model updating arising from validation"),
            ("11", "Methods", "How risk groups were created, if done"),
            (
                "12",
                "Methods",
                "For validation, differences from the development data in setting, eligibility, outcome, and "
                "predictors",
            ),
            ("13a", "Results", "Flow of participants, with numbers with and without the outcome"),
            ("13b", "Results", "Characteristics of participants, including missing data"),
            ("13c", "Results", "For validation, comparison with the development data"),
            ("14a", "Results", "Number of participants and outcome events in each analysis"),
            ("14b", "Results", "Unadjusted association between each candidate predictor and outcome, if done"),
            (
                "15a",
                "Results",
                "The full prediction model, including all coefficients and intercept or baseline survival",
            ),
            ("15b", "Results", "How to use the prediction model"),
            ("16", "Results", "Performance measures with confidence intervals"),
            ("17", "Results", "Results of model updating, if done"),
            ("18", "Discussion", "Limitations"),
            ("19a", "Discussion", "For validation, results discussed with reference to development data"),
            ("19b", "Discussion", "Overall interpretation of the results"),
            ("20", "Discussion", "Potential clinical use of the model and implications for future research"),
            (
                "21",
                "Other information",
                "Availability of supplementary resources such as the protocol, calculator, and data sets",
            ),
            ("22", "Other information", "Source of funding and role of funders"),
        ]
    ),
)

CHECKLISTS: dict[str, Checklist] = {c.key: c for c in (CONSORT, STROBE, STARD, TRIPOD)}
STATUSES = ("reported", "partially_reported", "not_reported", "not_applicable")


def catalog() -> list[dict]:
    return [
        {
            "key": c.key,
            "label": c.label,
            "study_types": c.study_types,
            "reference": c.reference,
            "url": c.url,
            "items": [{"id": item_id, "section": section, "topic": topic} for item_id, section, topic in c.items],
        }
        for c in CHECKLISTS.values()
    ]


def recommend_checklist(study_design: str, review_type: str) -> str:
    design = study_design.casefold()
    review = review_type.casefold()
    if "diagnos" in design or "diagnos" in review:
        return "stard"
    if "predict" in design or "predict" in review:
        return "tripod"
    if "random" in design or "crossover" in design:
        return "consort"
    return "strobe"
