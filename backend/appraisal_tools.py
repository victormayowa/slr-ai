"""Risk of bias and quality appraisal tools: domains, signalling questions, and each tool's published algorithm from
answers to domain judgments and from domain judgments to an overall judgment.

Question labels here are short descriptions written for OmniReview, with each item's number in the official tool, so
reviewers can follow the official guidance document linked from each tool. Several tools' full texts are published
under licences that restrict commercial reuse (for example RoB 2 and ROBINS-I under CC BY-NC-ND), so their verbatim
question wording and guidance are not embedded.

Where a tool defines an algorithm (RoB 2, QUADAS-2's convention, PROBAST, AMSTAR 2, the Newcastle-Ottawa AHRQ
conversion), the suggested judgment is computed from the answers; reviewers can depart from it with a rationale. Where
a tool relies on judgment (ROBINS-I, ROBINS-E, QUIPS, JBI, CASP, MMAT), no judgment is computed.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

Answers = Mapping[str, str]

YES = frozenset({"Y", "PY"})
NO = frozenset({"N", "PN"})

ANSWER_SETS: dict[str, tuple[tuple[str, str], ...]] = {
    "signalling": (("Y", "Yes"), ("PY", "Probably yes"), ("PN", "Probably no"), ("N", "No"), ("NI", "No information")),
    "yes_no_unclear": (("yes", "Yes"), ("no", "No"), ("unclear", "Unclear")),
    "yes_no_unclear_na": (("yes", "Yes"), ("no", "No"), ("unclear", "Unclear"), ("not_applicable", "Not applicable")),
    "yes_no_cant_tell": (("yes", "Yes"), ("no", "No"), ("cant_tell", "Can't tell")),
    "amstar": (("yes", "Yes"), ("partial_yes", "Partial yes"), ("no", "No")),
    "amstar_meta": (
        ("yes", "Yes"),
        ("partial_yes", "Partial yes"),
        ("no", "No"),
        ("no_meta_analysis", "No meta-analysis"),
    ),
    "star": (("1", "Star awarded"), ("0", "No star")),
    "two_stars": (("2", "Two stars"), ("1", "One star"), ("0", "No star")),
    "quips": (("yes", "Yes"), ("partial", "Partial"), ("no", "No"), ("unclear", "Unclear")),
}

JUDGMENT_SETS: dict[str, tuple[tuple[str, str], ...]] = {
    "rob2": (("low", "Low risk"), ("some_concerns", "Some concerns"), ("high", "High risk")),
    "robins_i": (
        ("low", "Low risk"),
        ("moderate", "Moderate risk"),
        ("serious", "Serious risk"),
        ("critical", "Critical risk"),
        ("no_information", "No information"),
    ),
    "robins_e": (
        ("low", "Low risk"),
        ("some_concerns", "Some concerns"),
        ("high", "High risk"),
        ("very_high", "Very high risk"),
    ),
    "low_high_unclear": (("low", "Low"), ("high", "High"), ("unclear", "Unclear")),
    "low_moderate_high": (("low", "Low"), ("moderate", "Moderate"), ("high", "High")),
    "nos_quality": (("good", "Good"), ("fair", "Fair"), ("poor", "Poor")),
    "stars": (("0", "0 stars"), ("1", "1 star"), ("2", "2 stars"), ("3", "3 stars"), ("4", "4 stars")),
    "jbi": (("include", "Include"), ("exclude", "Exclude"), ("seek_information", "Seek further information")),
    "amstar": (("high", "High"), ("moderate", "Moderate"), ("low", "Low"), ("critically_low", "Critically low")),
    "quality": (("high", "High quality"), ("moderate", "Moderate quality"), ("low", "Low quality")),
}

# The order from least to most concerning, for "worst domain" overall judgments.
SEVERITY = {
    "low": 0,
    "moderate": 1,
    "some_concerns": 1,
    "unclear": 1,
    "no_information": 1,
    "serious": 2,
    "high": 2,
    "critical": 3,
    "very_high": 3,
}


@dataclass(frozen=True)
class Condition:
    """A question is asked only when any (or all) of the listed questions have one of the listed answers."""

    questions: tuple[str, ...]
    answers: frozenset[str]
    mode: Literal["any", "all"] = "any"

    def met(self, answers: Answers) -> bool:
        checks = [answers.get(question) in self.answers for question in self.questions]
        return any(checks) if self.mode == "any" else all(checks)


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    answers: str = "signalling"
    asked_if: Condition | None = None
    critical: bool = False


@dataclass(frozen=True)
class Domain:
    key: str
    label: str
    questions: tuple[Question, ...]
    judgments: str
    algorithm: Callable[[Answers], str | None] | None = None
    # "bias" domains feed the overall judgment; "applicability" domains are reported separately.
    kind: Literal["bias", "applicability", "screening"] = "bias"

    def applicable_questions(self, answers: Answers) -> list[Question]:
        return [q for q in self.questions if q.asked_if is None or q.asked_if.met(answers)]


@dataclass(frozen=True)
class Tool:
    key: str
    label: str
    version: str
    study_types: str
    reference: str
    guidance_url: str
    domains: tuple[Domain, ...]
    overall_judgments: str
    overall: Callable[[Answers, Mapping[str, str]], str | None] | None = None
    # RoB 2 is assessed for a specific result (outcome); most tools assess the study.
    per_outcome: bool = False
    robvis_tool: str = "Generic"
    notes: tuple[str, ...] = field(default_factory=tuple)

    def domain(self, key: str) -> Domain | None:
        return next((d for d in self.domains if d.key == key), None)

    def question(self, question_id: str) -> Question | None:
        return next((q for d in self.domains for q in d.questions if q.id == question_id), None)


def _all_answered(answers: Answers, *ids: str) -> bool:
    return all(answers.get(i) for i in ids)


def _worst(judgments: list[str | None]) -> str | None:
    if any(j is None for j in judgments):
        return None
    return max(judgments, key=lambda j: SEVERITY.get(j or "", 0))  # type: ignore[arg-type]


# --- RoB 2 (Sterne et al. BMJ 2019;366:l4898), individually randomized parallel-group trials, effect of assignment ---


def _rob2_d1(a: Answers) -> str | None:
    if not _all_answered(a, "1.1", "1.2", "1.3"):
        return None
    if a["1.2"] in NO:
        return "high"
    if a["1.2"] == "NI":
        return "high" if a["1.3"] in YES else "some_concerns"
    if a["1.3"] in YES or a["1.1"] in NO:
        return "some_concerns"
    return "low"


def _rob2_d2(a: Answers) -> str | None:
    if not _all_answered(a, "2.1", "2.2", "2.6"):
        return None
    if a["2.1"] in NO and a["2.2"] in NO:
        part1 = "low"
    elif not a.get("2.3"):
        return None
    elif a["2.3"] in NO:
        part1 = "low"
    elif a["2.3"] == "NI":
        part1 = "some_concerns"
    elif not a.get("2.4"):
        return None
    elif a["2.4"] in NO:
        part1 = "some_concerns"
    elif not a.get("2.5"):
        return None
    else:
        part1 = "some_concerns" if a["2.5"] in YES else "high"
    if a["2.6"] in YES:
        part2 = "low"
    elif not a.get("2.7"):
        return None
    else:
        part2 = "some_concerns" if a["2.7"] in NO else "high"
    return _worst([part1, part2])


def _rob2_d3(a: Answers) -> str | None:
    if not a.get("3.1"):
        return None
    if a["3.1"] in YES:
        return "low"
    if not a.get("3.2"):
        return None
    if a["3.2"] in YES:
        return "low"
    if not a.get("3.3"):
        return None
    if a["3.3"] in NO:
        return "low"
    if not a.get("3.4"):
        return None
    return "some_concerns" if a["3.4"] in NO else "high"


def _rob2_d4(a: Answers) -> str | None:
    if not _all_answered(a, "4.1", "4.2"):
        return None
    if a["4.1"] in YES or a["4.2"] in YES:
        return "high"
    lowest = "low" if a["4.1"] in NO and a["4.2"] in NO else "some_concerns"
    if not a.get("4.3"):
        return None
    if a["4.3"] in NO:
        return lowest
    if not a.get("4.4"):
        return None
    if a["4.4"] in NO:
        return lowest
    if not a.get("4.5"):
        return None
    return "some_concerns" if a["4.5"] in NO else "high"


def _rob2_d5(a: Answers) -> str | None:
    if not _all_answered(a, "5.1", "5.2", "5.3"):
        return None
    if a["5.2"] in YES or a["5.3"] in YES:
        return "high"
    if a["5.2"] == "NI" or a["5.3"] == "NI":
        return "some_concerns"
    return "low" if a["5.1"] in YES else "some_concerns"


def _rob2_overall(_: Answers, domains: Mapping[str, str]) -> str | None:
    judgments = [domains.get(key) for key in ("d1", "d2", "d3", "d4", "d5")]
    if any(j is None for j in judgments):
        return None
    if "high" in judgments:
        return "high"
    return "low" if all(j == "low" for j in judgments) else "some_concerns"


_YPY_NI = frozenset({"Y", "PY", "NI"})
_NPN_NI = frozenset({"N", "PN", "NI"})

ROB2 = Tool(
    key="rob2",
    label="RoB 2 (randomized trials)",
    version="2019-08-22, individually randomized parallel-group trials, effect of assignment",
    study_types="Randomized controlled trials",
    reference="Sterne JAC, et al. RoB 2: a revised tool for assessing risk of bias in randomised trials. BMJ "
    "2019;366:l4898.",
    guidance_url="https://www.riskofbias.info/welcome/rob-2-0-tool",
    per_outcome=True,
    robvis_tool="ROB2",
    domains=(
        Domain(
            "d1",
            "Bias arising from the randomization process",
            (
                Question("1.1", "Random allocation sequence"),
                Question("1.2", "Allocation concealed until assignment"),
                Question("1.3", "Baseline differences suggesting a problem with randomization"),
            ),
            "rob2",
            _rob2_d1,
        ),
        Domain(
            "d2",
            "Bias due to deviations from intended interventions",
            (
                Question("2.1", "Participants aware of their assigned intervention"),
                Question("2.2", "Carers and people delivering interventions aware of assignment"),
                Question(
                    "2.3",
                    "Deviations from the intended intervention that arose because of the trial context",
                    asked_if=Condition(("2.1", "2.2"), _YPY_NI),
                ),
                Question(
                    "2.4", "Those deviations likely to have affected the outcome", asked_if=Condition(("2.3",), YES)
                ),
                Question("2.5", "Those deviations balanced between groups", asked_if=Condition(("2.4",), _YPY_NI)),
                Question("2.6", "Analysis appropriate to estimate the effect of assignment"),
                Question(
                    "2.7",
                    "Potential for substantial impact of analysing participants outside their assigned groups",
                    asked_if=Condition(("2.6",), _NPN_NI),
                ),
            ),
            "rob2",
            _rob2_d2,
        ),
        Domain(
            "d3",
            "Bias due to missing outcome data",
            (
                Question("3.1", "Outcome data available for all, or nearly all, participants randomized"),
                Question(
                    "3.2",
                    "Evidence that the result was not biased by missing outcome data",
                    asked_if=Condition(("3.1",), _NPN_NI),
                ),
                Question(
                    "3.3", "Missingness could depend on the true value of the outcome", asked_if=Condition(("3.2",), NO)
                ),
                Question(
                    "3.4",
                    "Missingness likely depended on the true value of the outcome",
                    asked_if=Condition(("3.3",), _YPY_NI),
                ),
            ),
            "rob2",
            _rob2_d3,
        ),
        Domain(
            "d4",
            "Bias in measurement of the outcome",
            (
                Question("4.1", "Method of measuring the outcome inappropriate"),
                Question("4.2", "Measurement or ascertainment could have differed between groups"),
                Question(
                    "4.3",
                    "Outcome assessors aware of the intervention received",
                    asked_if=Condition(("4.1", "4.2"), _NPN_NI, "all"),
                ),
                Question(
                    "4.4",
                    "Assessment could have been influenced by knowing the intervention",
                    asked_if=Condition(("4.3",), _YPY_NI),
                ),
                Question(
                    "4.5",
                    "Assessment likely to have been influenced by knowing the intervention",
                    asked_if=Condition(("4.4",), _YPY_NI),
                ),
            ),
            "rob2",
            _rob2_d4,
        ),
        Domain(
            "d5",
            "Bias in selection of the reported result",
            (
                Question(
                    "5.1", "Analysed according to a pre-specified plan finalized before unblinded data were available"
                ),
                Question("5.2", "Result selected from multiple eligible outcome measurements"),
                Question("5.3", "Result selected from multiple eligible analyses of the data"),
            ),
            "rob2",
            _rob2_d5,
        ),
    ),
    overall_judgments="rob2",
    overall=_rob2_overall,
    notes=(
        "Assess each result (outcome) separately.",
        "Overall: high if any domain is high; low if every domain is low; otherwise some concerns. Reviewers may judge "
        "high when some concerns in several domains substantially lower confidence in the result.",
    ),
)


# --- ROBINS-I (Sterne et al. BMJ 2016;355:i4919) ---


def _robins_i_overall(_: Answers, domains: Mapping[str, str]) -> str | None:
    judgments = [domains.get(f"d{i}") for i in range(1, 8)]
    if any(j is None for j in judgments):
        return None
    for level in ("critical", "serious"):
        if level in judgments:
            return level
    if "no_information" in judgments:
        return "no_information"
    return "moderate" if "moderate" in judgments else "low"


def _signalling(prefix: str, labels: list[str]) -> tuple[Question, ...]:
    return tuple(Question(f"{prefix}.{i}", label) for i, label in enumerate(labels, start=1))


ROBINS_I = Tool(
    key="robins_i",
    label="ROBINS-I (non-randomized studies of interventions)",
    version="2016",
    study_types="Non-randomized studies of the effects of interventions",
    reference="Sterne JAC, et al. ROBINS-I: a tool for assessing risk of bias in non-randomised studies of "
    "interventions. BMJ 2016;355:i4919.",
    guidance_url="https://www.riskofbias.info/welcome/home/current-version-of-robins-i",
    per_outcome=True,
    robvis_tool="ROBINS-I",
    domains=(
        Domain(
            "d1",
            "Bias due to confounding",
            _signalling(
                "1",
                [
                    "Potential for confounding of the effect of intervention",
                    "Analysis based on splitting follow-up time by intervention received",
                    "Intervention discontinuations or switches likely related to prognostic factors",
                    "Appropriate analysis controlling for all important confounding domains",
                    "Confounding domains measured validly and reliably",
                    "Adjustment for post-intervention variables that could be affected by the intervention",
                    "Appropriate methods for time-varying confounding",
                    "Time-varying confounders measured validly and reliably",
                ],
            ),
            "robins_i",
        ),
        Domain(
            "d2",
            "Bias in selection of participants into the study",
            _signalling(
                "2",
                [
                    "Selection based on characteristics observed after the start of intervention",
                    "Post-intervention variables influencing selection associated with the intervention",
                    "Post-intervention variables influencing selection associated with the outcome",
                    "Start of follow-up and start of intervention coincide for most participants",
                    "Adjustment techniques used to correct for selection biases",
                ],
            ),
            "robins_i",
        ),
        Domain(
            "d3",
            "Bias in classification of interventions",
            _signalling(
                "3",
                [
                    "Intervention groups clearly defined",
                    "Information defining intervention groups recorded at the start of the intervention",
                    "Classification affected by knowledge of the outcome or risk of the outcome",
                ],
            ),
            "robins_i",
        ),
        Domain(
            "d4",
            "Bias due to deviations from intended interventions",
            _signalling(
                "4",
                [
                    "Deviations from intended intervention beyond usual practice",
                    "Deviations unbalanced between groups and likely to have affected the outcome",
                    "Important co-interventions balanced across groups",
                    "Intervention implemented successfully for most participants",
                    "Participants adhered to the assigned intervention regimen",
                    "Appropriate analysis to estimate the effect of starting and adhering to the intervention",
                ],
            ),
            "robins_i",
        ),
        Domain(
            "d5",
            "Bias due to missing data",
            _signalling(
                "5",
                [
                    "Outcome data available for all, or nearly all, participants",
                    "Participants excluded due to missing data on intervention status",
                    "Participants excluded due to missing data on other variables needed for the analysis",
                    "Proportion and reasons for missing data similar across interventions",
                    "Evidence that results were robust to the presence of missing data",
                ],
            ),
            "robins_i",
        ),
        Domain(
            "d6",
            "Bias in measurement of outcomes",
            _signalling(
                "6",
                [
                    "Outcome measure could have been influenced by knowledge of the intervention received",
                    "Outcome assessors aware of the intervention received",
                    "Methods of outcome assessment comparable across intervention groups",
                    "Systematic errors in measuring the outcome related to the intervention received",
                ],
            ),
            "robins_i",
        ),
        Domain(
            "d7",
            "Bias in selection of the reported result",
            _signalling(
                "7",
                [
                    "Result selected from multiple outcome measurements",
                    "Result selected from multiple analyses of the intervention-outcome relationship",
                    "Result selected from different subgroups",
                ],
            ),
            "robins_i",
        ),
    ),
    overall_judgments="robins_i",
    overall=_robins_i_overall,
    notes=(
        "Domain judgments are reviewers' judgments informed by the signalling questions; the tool has no algorithm.",
    ),
)


# --- ROBINS-E (Higgins et al. Environ Int 2024;186:108602) ---

ROBINS_E = Tool(
    key="robins_e",
    label="ROBINS-E (non-randomized studies of exposures)",
    version="2023-06",
    study_types="Observational studies of the effect of an exposure",
    reference="Higgins JPT, et al. A tool to assess risk of bias in non-randomized follow-up studies of exposure "
    "effects (ROBINS-E). Environ Int 2024;186:108602.",
    guidance_url="https://www.riskofbias.info/welcome/robins-e-tool",
    per_outcome=True,
    robvis_tool="ROBINS-E",
    domains=tuple(
        Domain(f"d{i}", label, (), "robins_e")
        for i, label in enumerate(
            [
                "Bias due to confounding",
                "Bias arising from measurement of the exposure",
                "Bias in selection of participants into the study (or into the analysis)",
                "Bias due to post-exposure interventions",
                "Bias due to missing data",
                "Bias arising from measurement of the outcome",
                "Bias in selection of the reported result",
            ],
            start=1,
        )
    ),
    overall_judgments="robins_e",
    overall=lambda _, domains: _worst([domains.get(f"d{i}") for i in range(1, 8)]),
    notes=(
        "Use the official ROBINS-E signalling questions and algorithms, then record each domain's judgment here.",
        "Overall: the most severe domain judgment.",
    ),
)


# --- QUADAS-2 (Whiting et al. Ann Intern Med 2011;155:529-36) ---


def _all_yes(*ids: str) -> Callable[[Answers], str | None]:
    def algorithm(a: Answers) -> str | None:
        values = [a.get(i) for i in ids]
        if any(v is None for v in values):
            return None
        if all(v == "yes" for v in values):
            return "low"
        return "high" if "no" in values else "unclear"

    return algorithm


def _bias_summary(tool_domains: tuple[str, ...]) -> Callable[[Answers, Mapping[str, str]], str | None]:
    def overall(_: Answers, domains: Mapping[str, str]) -> str | None:
        judgments = [domains.get(key) for key in tool_domains]
        if any(j is None for j in judgments):
            return None
        if "high" in judgments:
            return "high"
        return "low" if all(j == "low" for j in judgments) else "unclear"

    return overall


QUADAS2 = Tool(
    key="quadas2",
    label="QUADAS-2 (diagnostic accuracy studies)",
    version="2011",
    study_types="Diagnostic test accuracy studies",
    reference="Whiting PF, et al. QUADAS-2: a revised tool for the quality assessment of diagnostic accuracy "
    "studies. Ann Intern Med 2011;155:529-36.",
    guidance_url="https://www.bristol.ac.uk/population-health-sciences/projects/quadas/quadas-2/",
    robvis_tool="QUADAS-2",
    domains=(
        Domain(
            "d1",
            "Patient selection",
            (
                Question("1.1", "Consecutive or random sample of patients enrolled", "yes_no_unclear"),
                Question("1.2", "Case-control design avoided", "yes_no_unclear"),
                Question("1.3", "Inappropriate exclusions avoided", "yes_no_unclear"),
            ),
            "low_high_unclear",
            _all_yes("1.1", "1.2", "1.3"),
        ),
        Domain(
            "d2",
            "Index test",
            (
                Question("2.1", "Index test interpreted without knowledge of the reference standard", "yes_no_unclear"),
                Question("2.2", "Threshold pre-specified, if one was used", "yes_no_unclear"),
            ),
            "low_high_unclear",
            _all_yes("2.1", "2.2"),
        ),
        Domain(
            "d3",
            "Reference standard",
            (
                Question(
                    "3.1", "Reference standard likely to classify the target condition correctly", "yes_no_unclear"
                ),
                Question("3.2", "Reference standard interpreted without knowledge of the index test", "yes_no_unclear"),
            ),
            "low_high_unclear",
            _all_yes("3.1", "3.2"),
        ),
        Domain(
            "d4",
            "Flow and timing",
            (
                Question("4.1", "Appropriate interval between index test and reference standard", "yes_no_unclear"),
                Question("4.2", "All patients received a reference standard", "yes_no_unclear"),
                Question("4.3", "Patients received the same reference standard", "yes_no_unclear"),
                Question("4.4", "All patients included in the analysis", "yes_no_unclear"),
            ),
            "low_high_unclear",
            _all_yes("4.1", "4.2", "4.3", "4.4"),
        ),
        Domain("a1", "Applicability: patient selection", (), "low_high_unclear", kind="applicability"),
        Domain("a2", "Applicability: index test", (), "low_high_unclear", kind="applicability"),
        Domain("a3", "Applicability: reference standard", (), "low_high_unclear", kind="applicability"),
    ),
    overall_judgments="low_high_unclear",
    overall=_bias_summary(("d1", "d2", "d3", "d4")),
    notes=(
        "Suggested domain judgments follow the QUADAS-2 convention: low if every signalling question is yes; a no "
        "signals potential bias, which reviewers judge.",
        "QUADAS-2 defines no overall judgment; the summary is low if every domain is low and high if any is high.",
    ),
)


# --- PROBAST (Wolff et al. Ann Intern Med 2019;170:51-58) ---


def _probast_domain(*ids: str) -> Callable[[Answers], str | None]:
    def algorithm(a: Answers) -> str | None:
        values = [a.get(i) for i in ids]
        if any(v is None for v in values):
            return None
        if any(v in NO for v in values):
            return "high"
        return "low" if all(v in YES for v in values) else "unclear"

    return algorithm


PROBAST = Tool(
    key="probast",
    label="PROBAST (prediction model studies)",
    version="2019",
    study_types="Studies developing or validating prediction models",
    reference="Wolff RF, et al. PROBAST: a tool to assess the risk of bias and applicability of prediction model "
    "studies. Ann Intern Med 2019;170:51-58.",
    guidance_url="https://www.probast.org/",
    domains=(
        Domain(
            "d1",
            "Participants",
            _signalling("1", ["Appropriate data sources", "Appropriate inclusions and exclusions"]),
            "low_high_unclear",
            _probast_domain("1.1", "1.2"),
        ),
        Domain(
            "d2",
            "Predictors",
            _signalling(
                "2",
                [
                    "Predictors defined and assessed in a similar way for all participants",
                    "Predictor assessments made without knowledge of outcome data",
                    "All predictors available at the time the model is intended to be used",
                ],
            ),
            "low_high_unclear",
            _probast_domain("2.1", "2.2", "2.3"),
        ),
        Domain(
            "d3",
            "Outcome",
            _signalling(
                "3",
                [
                    "Outcome determined appropriately",
                    "Pre-specified or standard outcome definition used",
                    "Predictors excluded from the outcome definition",
                    "Outcome defined and determined in a similar way for all participants",
                    "Outcome determined without knowledge of predictor information",
                    "Appropriate time interval between predictor assessment and outcome determination",
                ],
            ),
            "low_high_unclear",
            _probast_domain("3.1", "3.2", "3.3", "3.4", "3.5", "3.6"),
        ),
        Domain(
            "d4",
            "Analysis",
            _signalling(
                "4",
                [
                    "Reasonable number of participants with the outcome",
                    "Continuous and categorical predictors handled appropriately",
                    "All enrolled participants included in the analysis",
                    "Participants with missing data handled appropriately",
                    "Selection of predictors based on univariable analysis avoided",
                    "Complexities in the data (censoring, competing risks) accounted for",
                    "Relevant model performance measures evaluated appropriately",
                    "Overfitting, underfitting, and optimism accounted for",
                    "Predictors and weights in the final model correspond to the reported multivariable analysis",
                ],
            ),
            "low_high_unclear",
            _probast_domain(*(f"4.{i}" for i in range(1, 10))),
        ),
        Domain("a1", "Applicability: participants", (), "low_high_unclear", kind="applicability"),
        Domain("a2", "Applicability: predictors", (), "low_high_unclear", kind="applicability"),
        Domain("a3", "Applicability: outcome", (), "low_high_unclear", kind="applicability"),
    ),
    overall_judgments="low_high_unclear",
    overall=_bias_summary(("d1", "d2", "d3", "d4")),
    notes=(
        "A model developed without external validation is rated high risk of bias overall unless it was based on a "
        "very "
        "large data set with internal validation.",
    ),
)


# --- Newcastle-Ottawa Scale, with the AHRQ conversion to good, fair, or poor ---


def _stars(*ids: str) -> Callable[[Answers], str | None]:
    def algorithm(a: Answers) -> str | None:
        if not _all_answered(a, *ids):
            return None
        return str(sum(int(a[i]) for i in ids))

    return algorithm


def _nos_overall(_: Answers, domains: Mapping[str, str]) -> str | None:
    if not all(domains.get(key) for key in ("selection", "comparability", "outcome")):
        return None
    selection, comparability, outcome = (int(domains[key]) for key in ("selection", "comparability", "outcome"))
    if selection >= 3 and comparability >= 1 and outcome >= 2:
        return "good"
    if selection == 2 and comparability >= 1 and outcome >= 2:
        return "fair"
    return "poor"


NOS_COHORT = Tool(
    key="nos_cohort",
    label="Newcastle-Ottawa Scale (cohort studies)",
    version="2000s, with AHRQ thresholds",
    study_types="Cohort studies",
    reference="Wells GA, et al. The Newcastle-Ottawa Scale (NOS) for assessing the quality of nonrandomised studies "
    "in meta-analyses.",
    guidance_url="https://www.ohri.ca/programs/clinical_epidemiology/oxford.asp",
    domains=(
        Domain(
            "selection",
            "Selection",
            (
                Question("S1", "Representativeness of the exposed cohort", "star"),
                Question("S2", "Selection of the non-exposed cohort", "star"),
                Question("S3", "Ascertainment of exposure", "star"),
                Question("S4", "Outcome of interest not present at the start of the study", "star"),
            ),
            "stars",
            _stars("S1", "S2", "S3", "S4"),
        ),
        Domain(
            "comparability",
            "Comparability",
            (Question("C1", "Comparability of cohorts in design or analysis", "two_stars"),),
            "stars",
            _stars("C1"),
        ),
        Domain(
            "outcome",
            "Outcome",
            (
                Question("O1", "Assessment of outcome", "star"),
                Question("O2", "Follow-up long enough for outcomes to occur", "star"),
                Question("O3", "Adequacy of follow-up of cohorts", "star"),
            ),
            "stars",
            _stars("O1", "O2", "O3"),
        ),
    ),
    overall_judgments="nos_quality",
    overall=_nos_overall,
    notes=(
        "Good: 3-4 selection stars, 1-2 comparability stars, and 2-3 outcome stars. Fair: 2 selection stars with the "
        "same "
        "comparability and outcome stars. Poor: otherwise.",
    ),
)

NOS_CASE_CONTROL = Tool(
    key="nos_case_control",
    label="Newcastle-Ottawa Scale (case-control studies)",
    version="2000s, with AHRQ thresholds",
    study_types="Case-control studies",
    reference=NOS_COHORT.reference,
    guidance_url=NOS_COHORT.guidance_url,
    domains=(
        Domain(
            "selection",
            "Selection",
            (
                Question("S1", "Case definition adequate", "star"),
                Question("S2", "Representativeness of the cases", "star"),
                Question("S3", "Selection of controls", "star"),
                Question("S4", "Definition of controls", "star"),
            ),
            "stars",
            _stars("S1", "S2", "S3", "S4"),
        ),
        Domain(
            "comparability",
            "Comparability",
            (Question("C1", "Comparability of cases and controls in design or analysis", "two_stars"),),
            "stars",
            _stars("C1"),
        ),
        Domain(
            "outcome",
            "Exposure",
            (
                Question("E1", "Ascertainment of exposure", "star"),
                Question("E2", "Same method of ascertainment for cases and controls", "star"),
                Question("E3", "Non-response rate", "star"),
            ),
            "stars",
            _stars("E1", "E2", "E3"),
        ),
    ),
    overall_judgments="nos_quality",
    overall=_nos_overall,
    notes=NOS_COHORT.notes,
)


# --- JBI critical appraisal checklists (judgment-based, no score) ---


def _checklist(key: str, label: str, study_types: str, items: list[str], answers: str = "yes_no_unclear_na") -> Tool:
    return Tool(
        key=key,
        label=label,
        version="JBI checklist",
        study_types=study_types,
        reference="JBI Manual for Evidence Synthesis. JBI critical appraisal tools.",
        guidance_url="https://jbi.global/critical-appraisal-tools",
        domains=(
            Domain(
                "items",
                "Checklist",
                tuple(Question(str(i), text, answers) for i, text in enumerate(items, start=1)),
                "quality",
            ),
        ),
        overall_judgments="jbi",
        notes=("JBI checklists have no score; reviewers decide to include, exclude, or seek further information.",),
    )


JBI_TOOLS = [
    _checklist(
        "jbi_cohort",
        "JBI checklist for cohort studies",
        "Cohort studies",
        [
            "Groups similar and recruited from the same population",
            "Exposures measured similarly to assign people to groups",
            "Exposure measured in a valid and reliable way",
            "Confounding factors identified",
            "Strategies to deal with confounding stated",
            "Participants free of the outcome at the start",
            "Outcomes measured in a valid and reliable way",
            "Follow-up time reported and sufficient",
            "Follow-up complete, or reasons for loss described and explored",
            "Strategies to address incomplete follow-up used",
            "Appropriate statistical analysis",
        ],
    ),
    _checklist(
        "jbi_case_control",
        "JBI checklist for case-control studies",
        "Case-control studies",
        [
            "Groups comparable other than the presence of disease",
            "Cases and controls matched appropriately",
            "Same criteria used to identify cases and controls",
            "Exposure measured in a standard, valid, and reliable way",
            "Exposure measured in the same way for cases and controls",
            "Confounding factors identified",
            "Strategies to deal with confounding stated",
            "Outcomes assessed in a standard, valid, and reliable way",
            "Exposure period of interest long enough to be meaningful",
            "Appropriate statistical analysis",
        ],
    ),
    _checklist(
        "jbi_cross_sectional",
        "JBI checklist for analytical cross-sectional studies",
        "Analytical cross-sectional studies",
        [
            "Inclusion criteria clearly defined",
            "Study subjects and setting described in detail",
            "Exposure measured in a valid and reliable way",
            "Objective, standard criteria used to measure the condition",
            "Confounding factors identified",
            "Strategies to deal with confounding stated",
            "Outcomes measured in a valid and reliable way",
            "Appropriate statistical analysis",
        ],
    ),
    _checklist(
        "jbi_prevalence",
        "JBI checklist for prevalence studies",
        "Prevalence studies",
        [
            "Sample frame appropriate to address the target population",
            "Participants sampled appropriately",
            "Sample size adequate",
            "Subjects and setting described in detail",
            "Data analysis conducted with sufficient coverage of the sample",
            "Valid methods used to identify the condition",
            "Condition measured in a standard, reliable way for all participants",
            "Appropriate statistical analysis",
            "Response rate adequate, or low response managed appropriately",
        ],
    ),
    _checklist(
        "jbi_case_series",
        "JBI checklist for case series",
        "Case series",
        [
            "Clear criteria for inclusion",
            "Condition measured in a standard, reliable way for all participants",
            "Valid methods used to identify the condition",
            "Consecutive inclusion of participants",
            "Complete inclusion of participants",
            "Clear reporting of participants' demographics",
            "Clear reporting of participants' clinical information",
            "Outcomes or follow-up results clearly reported",
            "Clear reporting of the presenting sites' or clinics' demographic information",
            "Appropriate statistical analysis",
        ],
    ),
    _checklist(
        "jbi_qualitative",
        "JBI checklist for qualitative research",
        "Qualitative studies",
        [
            "Philosophical perspective congruent with the research methodology",
            "Methodology congruent with the research question or objectives",
            "Methodology congruent with the data collection methods",
            "Methodology congruent with the representation and analysis of data",
            "Methodology congruent with the interpretation of results",
            "Researcher located culturally or theoretically",
            "Influence of the researcher on the research addressed",
            "Participants and their voices adequately represented",
            "Ethical approval and conduct according to current criteria",
            "Conclusions flow from the analysis or interpretation of the data",
        ],
    ),
]

CASP_QUALITATIVE = Tool(
    key="casp_qualitative",
    label="CASP qualitative studies checklist",
    version="2018",
    study_types="Qualitative studies",
    reference="Critical Appraisal Skills Programme. CASP Qualitative Studies Checklist, 2018.",
    guidance_url="https://casp-uk.net/casp-tools-checklists/",
    domains=(
        Domain(
            "items",
            "Checklist",
            tuple(
                Question(str(i), text, "yes_no_cant_tell")
                for i, text in enumerate(
                    [
                        "Clear statement of the aims of the research",
                        "Qualitative methodology appropriate",
                        "Research design appropriate to address the aims",
                        "Recruitment strategy appropriate to the aims",
                        "Data collected in a way that addressed the research issue",
                        "Relationship between researcher and participants adequately considered",
                        "Ethical issues taken into consideration",
                        "Data analysis sufficiently rigorous",
                        "Clear statement of findings",
                        "Value of the research discussed",
                    ],
                    start=1,
                )
            ),
            "quality",
        ),
    ),
    overall_judgments="quality",
    notes=("CASP checklists have no score; reviewers summarize the overall quality with a rationale.",),
)

_MMAT_CATEGORIES = {
    "1": (
        "Qualitative",
        [
            "Qualitative approach appropriate to answer the research question",
            "Qualitative data collection methods adequate",
            "Findings adequately derived from the data",
            "Interpretation of results sufficiently substantiated by data",
            "Coherence between data sources, collection, analysis, and interpretation",
        ],
    ),
    "2": (
        "Quantitative randomized controlled trials",
        [
            "Randomization appropriately performed",
            "Groups comparable at baseline",
            "Complete outcome data",
            "Outcome assessors blinded to the intervention provided",
            "Participants adhered to the assigned intervention",
        ],
    ),
    "3": (
        "Quantitative non-randomized",
        [
            "Participants representative of the target population",
            "Measurements appropriate regarding both the outcome and intervention or exposure",
            "Complete outcome data",
            "Confounders accounted for in the design and analysis",
            "Intervention administered, or exposure occurred, as intended",
        ],
    ),
    "4": (
        "Quantitative descriptive",
        [
            "Sampling strategy relevant to address the research question",
            "Sample representative of the target population",
            "Measurements appropriate",
            "Risk of nonresponse bias low",
            "Statistical analysis appropriate to answer the research question",
        ],
    ),
    "5": (
        "Mixed methods",
        [
            "Adequate rationale for using a mixed methods design",
            "Different components of the study effectively integrated",
            "Outputs of the integration of components adequately interpreted",
            "Divergences and inconsistencies between quantitative and qualitative results adequately addressed",
            "Different components adhere to the quality criteria of each tradition",
        ],
    ),
}

MMAT = Tool(
    key="mmat",
    label="Mixed Methods Appraisal Tool (MMAT)",
    version="2018",
    study_types="Qualitative, quantitative, and mixed methods studies",
    reference="Hong QN, et al. The Mixed Methods Appraisal Tool (MMAT) version 2018. Education for Information "
    "2018;34:285-291.",
    guidance_url="http://mixedmethodsappraisaltoolpublic.pbworks.com/",
    domains=(
        Domain(
            "screening",
            "Screening questions",
            (
                Question("S1", "Clear research questions", "yes_no_cant_tell"),
                Question("S2", "Collected data allow the research questions to be addressed", "yes_no_cant_tell"),
            ),
            "quality",
            kind="screening",
        ),
        *(
            Domain(
                f"category_{number}",
                name,
                tuple(Question(f"{number}.{i}", text, "yes_no_cant_tell") for i, text in enumerate(items, start=1)),
                "quality",
            )
            for number, (name, items) in _MMAT_CATEGORIES.items()
        ),
    ),
    overall_judgments="quality",
    notes=(
        "Answer the screening questions and the criteria of the study's category only; MMAT discourages an overall "
        "score.",
    ),
)

QUIPS = Tool(
    key="quips",
    label="QUIPS (prognostic factor studies)",
    version="2013",
    study_types="Prognostic factor studies",
    reference="Hayden JA, et al. Assessing bias in studies of prognostic factors. Ann Intern Med 2013;158:280-6.",
    guidance_url="https://methods.cochrane.org/prognosis/tools",
    robvis_tool="QUIPS",
    domains=tuple(
        Domain(
            f"d{number}",
            label,
            tuple(Question(f"{number}.{i}", text, "quips") for i, text in enumerate(prompts, start=1)),
            "low_moderate_high",
        )
        for number, (label, prompts) in enumerate(
            [
                (
                    "Study participation",
                    [
                        "Source population and recruitment described",
                        "Inclusion and exclusion criteria described",
                        "Adequate participation of eligible people",
                    ],
                ),
                (
                    "Study attrition",
                    [
                        "Proportion of participants with follow-up reported and adequate",
                        "Reasons for loss to follow-up described",
                        "Participants lost to follow-up similar to those completing",
                    ],
                ),
                (
                    "Prognostic factor measurement",
                    [
                        "Clear definition of the prognostic factor",
                        "Valid and reliable measurement",
                        "Same method and setting of measurement for all participants",
                    ],
                ),
                (
                    "Outcome measurement",
                    [
                        "Clear definition of the outcome",
                        "Valid and reliable outcome measurement",
                        "Same method and setting of measurement for all participants",
                    ],
                ),
                (
                    "Study confounding",
                    [
                        "Important confounders measured",
                        "Valid and reliable measurement of confounders",
                        "Confounders accounted for in the design or analysis",
                    ],
                ),
                (
                    "Statistical analysis and reporting",
                    [
                        "Sufficient presentation of data",
                        "Analysis appropriate for the design",
                        "No selective reporting of results",
                    ],
                ),
            ],
            start=1,
        )
    ),
    overall_judgments="low_moderate_high",
    notes=("QUIPS has no algorithm or overall score; judge each domain from its prompting items.",),
)


# --- AMSTAR 2 (Shea et al. BMJ 2017;358:j4008) ---

_AMSTAR_ITEMS = [
    ("Research questions and inclusion criteria include the components of PICO", False, "amstar"),
    ("Methods established before the review, with deviations justified", True, "amstar"),
    ("Selection of study designs for inclusion explained", False, "amstar"),
    ("Comprehensive literature search strategy", True, "amstar"),
    ("Study selection performed in duplicate", False, "amstar"),
    ("Data extraction performed in duplicate", False, "amstar"),
    ("List of excluded studies with justifications", True, "amstar"),
    ("Included studies described in adequate detail", False, "amstar"),
    ("Satisfactory technique for assessing risk of bias in included studies", True, "amstar"),
    ("Sources of funding for included studies reported", False, "amstar"),
    ("Appropriate methods for statistical combination of results", True, "amstar_meta"),
    ("Potential impact of risk of bias on meta-analysis results assessed", False, "amstar_meta"),
    ("Risk of bias accounted for when interpreting results", True, "amstar"),
    ("Heterogeneity explained and discussed", False, "amstar"),
    ("Publication bias investigated and its likely impact discussed", True, "amstar_meta"),
    ("Conflicts of interest, including review funding, reported", False, "amstar"),
]


def _amstar_overall(a: Answers, _: Mapping[str, str]) -> str | None:
    if not all(a.get(str(i)) for i in range(1, 17)):
        return None
    critical_flaws = sum(
        1 for i, (_, critical, _) in enumerate(_AMSTAR_ITEMS, start=1) if critical and a[str(i)] == "no"
    )
    weaknesses = sum(
        1 for i, (_, critical, _) in enumerate(_AMSTAR_ITEMS, start=1) if not critical and a[str(i)] == "no"
    )
    if critical_flaws > 1:
        return "critically_low"
    if critical_flaws == 1:
        return "low"
    return "moderate" if weaknesses > 1 else "high"


AMSTAR2 = Tool(
    key="amstar2",
    label="AMSTAR 2 (systematic reviews)",
    version="2017",
    study_types="Systematic reviews of healthcare interventions",
    reference="Shea BJ, et al. AMSTAR 2: a critical appraisal tool for systematic reviews. BMJ 2017;358:j4008.",
    guidance_url="https://amstar.ca/Amstar-2.php",
    domains=(
        Domain(
            "items",
            "Items",
            tuple(
                Question(str(i), text, answers, critical=critical)
                for i, (text, critical, answers) in enumerate(_AMSTAR_ITEMS, start=1)
            ),
            "amstar",
        ),
    ),
    overall_judgments="amstar",
    overall=_amstar_overall,
    notes=(
        "Critical items: 2, 4, 7, 9, 11, 13, 15. High: no or one non-critical weakness. Moderate: more than one "
        "non-critical weakness. Low: one critical flaw. Critically low: more than one critical flaw.",
    ),
)

TOOLS: dict[str, Tool] = {
    tool.key: tool
    for tool in [
        ROB2,
        ROBINS_I,
        ROBINS_E,
        QUADAS2,
        PROBAST,
        NOS_COHORT,
        NOS_CASE_CONTROL,
        *JBI_TOOLS,
        CASP_QUALITATIVE,
        MMAT,
        QUIPS,
        AMSTAR2,
    ]
}
# Protocol risk of bias tool names from earlier versions.
LEGACY_TOOL_KEYS = {
    "ROB-2": "rob2",
    "ROBINS-I": "robins_i",
    "Newcastle-Ottawa": "nos_cohort",
    "QUADAS-2": "quadas2",
    "PROBAST": "probast",
    "PROBAST+AI": "probast",
}


@dataclass
class Evaluation:
    domain_suggestions: dict[str, str | None]
    overall_suggestion: str | None
    # Questions that must be answered given the answers so far.
    applicable: dict[str, list[str]]
    unanswered: list[str]


def evaluate(tool: Tool, answers: Answers, domain_judgments: Mapping[str, str] | None = None) -> Evaluation:
    """Suggested judgments from the answers, and which questions still need answers.

    The overall suggestion uses reviewers' domain judgments where given, and the algorithm's suggestions otherwise.
    """
    suggestions: dict[str, str | None] = {}
    applicable: dict[str, list[str]] = {}
    unanswered: list[str] = []
    for domain in tool.domains:
        questions = domain.applicable_questions(answers)
        applicable[domain.key] = [q.id for q in questions]
        unanswered += [q.id for q in questions if not answers.get(q.id)]
        suggestions[domain.key] = domain.algorithm(answers) if domain.algorithm else None
    judged = {key: (domain_judgments or {}).get(key) or suggestion for key, suggestion in suggestions.items()}
    overall = tool.overall(answers, {k: v for k, v in judged.items() if v}) if tool.overall else None
    return Evaluation(suggestions, overall, applicable, unanswered)


def catalog() -> list[dict]:
    return [
        {
            "key": tool.key,
            "label": tool.label,
            "version": tool.version,
            "study_types": tool.study_types,
            "reference": tool.reference,
            "guidance_url": tool.guidance_url,
            "per_outcome": tool.per_outcome,
            "notes": list(tool.notes),
            "overall_judgments": [{"key": k, "label": v} for k, v in JUDGMENT_SETS[tool.overall_judgments]],
            "has_overall_algorithm": tool.overall is not None,
            "domains": [
                {
                    "key": domain.key,
                    "label": domain.label,
                    "kind": domain.kind,
                    "has_algorithm": domain.algorithm is not None,
                    "judgments": [{"key": k, "label": v} for k, v in JUDGMENT_SETS[domain.judgments]],
                    "questions": [
                        {
                            "id": q.id,
                            "text": q.text,
                            "critical": q.critical,
                            "answers": [{"key": k, "label": v} for k, v in ANSWER_SETS[q.answers]],
                            "asked_if": {
                                "questions": list(q.asked_if.questions),
                                "answers": sorted(q.asked_if.answers),
                                "mode": q.asked_if.mode,
                            }
                            if q.asked_if
                            else None,
                        }
                        for q in domain.questions
                    ],
                }
                for domain in tool.domains
            ],
        }
        for tool in TOOLS.values()
    ]


@dataclass(frozen=True)
class Recommendation:
    tool: str
    reason: str


def recommend_tools(study_design: str, review_type: str, framework: str) -> list[Recommendation]:
    """Tools suited to a study, most suitable first, from its design and the review's type and question framework."""
    design = study_design.casefold()
    review = f"{review_type} {framework}".casefold()
    if "systematic review" in design or "meta-analysis" in design:
        return [
            Recommendation(
                "amstar2", "The study is itself a systematic review; AMSTAR 2 appraises reviews of interventions."
            )
        ]
    if "diagnos" in review or "dta" in review or "diagnos" in design:
        return [Recommendation("quadas2", "Diagnostic test accuracy studies are assessed with QUADAS-2.")]
    if "predict" in review or "predict" in design:
        return [Recommendation("probast", "Prediction model studies are assessed with PROBAST.")]
    if "prognos" in review:
        return [Recommendation("quips", "Prognostic factor studies are assessed with QUIPS.")]
    if "mixed" in design:
        return [Recommendation("mmat", "Mixed methods studies are appraised with MMAT, which covers each component.")]
    if "qualitative" in design or "spider" in review:
        return [
            Recommendation("jbi_qualitative", "Qualitative studies are appraised with the JBI qualitative checklist."),
            Recommendation("casp_qualitative", "The CASP qualitative checklist is a common alternative."),
        ]
    if "random" in design or "crossover" in design:
        return [
            Recommendation("rob2", "Randomized trials are assessed with RoB 2, the Cochrane tool, for each result.")
        ]
    exposure = "peco" in review or "exposure" in review
    if "non-randomized" in design or "controlled before" in design:
        return [Recommendation("robins_i", "Non-randomized studies of interventions are assessed with ROBINS-I.")]
    if "cohort" in design or "case-control" in design:
        if exposure:
            return [Recommendation("robins_e", "Observational studies of exposures are assessed with ROBINS-E.")]
        primary = "nos_case_control" if "case-control" in design else "nos_cohort"
        return [
            Recommendation(
                "robins_i", "For effects of interventions, Cochrane recommends ROBINS-I for observational studies."
            ),
            Recommendation(primary, "The Newcastle-Ottawa Scale is widely used for observational studies."),
        ]
    if "cross-sectional" in design:
        return [
            Recommendation(
                "jbi_cross_sectional", "Analytical cross-sectional studies are appraised with the JBI checklist."
            )
        ]
    if "prevalence" in design or "prevalence" in review:
        return [Recommendation("jbi_prevalence", "Prevalence studies are appraised with the JBI prevalence checklist.")]
    if "case series" in design:
        return [Recommendation("jbi_case_series", "Case series are appraised with the JBI case series checklist.")]
    return [
        Recommendation("rob2", "No study design is recorded yet; RoB 2 applies if the study is a randomized trial.")
    ]
