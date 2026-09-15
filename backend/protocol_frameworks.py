"""Review question frameworks, FINER criteria, PRISMA-P protocol sections, and analysis plan options.

The frontend reads these from GET /api/protocol-frameworks, so this module is their single source of truth.
Section guidance paraphrases the PRISMA-P 2015 checklist (Moher et al., Systematic Reviews 2015;4:1).
"""

from dataclasses import asdict, dataclass

# Marks text an AI draft couldn't fill in; protocol checks warn while any remain.
PLACEHOLDER_MARKER = "[TO COMPLETE"


@dataclass(frozen=True)
class Element:
    key: str
    label: str
    hint: str


@dataclass(frozen=True)
class Framework:
    key: str
    label: str
    use_for: str
    elements: tuple[Element, ...]


_POPULATION = Element("population", "Population", "Who is studied, such as adults with type 2 diabetes")
_COMPARATOR = Element("comparator", "Comparator", "What it's compared with, such as placebo or usual care")
_OUTCOMES = Element("outcomes", "Outcomes", "What is measured, such as major cardiovascular events at 5 years")
_INTERVENTION = Element("intervention", "Intervention", "The treatment or action studied, such as low-dose aspirin")
_STUDY_DESIGN = Element("study_design", "Study design", "Designs to include, such as randomized controlled trials")

FRAMEWORKS: dict[str, Framework] = {
    framework.key: framework
    for framework in (
        Framework("PICO", "PICO", "Intervention reviews", (_POPULATION, _INTERVENTION, _COMPARATOR, _OUTCOMES)),
        Framework(
            "PICOS",
            "PICOS",
            "Intervention reviews that restrict study design",
            (_POPULATION, _INTERVENTION, _COMPARATOR, _OUTCOMES, _STUDY_DESIGN),
        ),
        Framework(
            "PECO",
            "PECO",
            "Exposure and risk factor reviews",
            (
                _POPULATION,
                Element("exposure", "Exposure", "The exposure or risk factor, such as long-term air pollution"),
                _COMPARATOR,
                _OUTCOMES,
            ),
        ),
        Framework(
            "SPIDER",
            "SPIDER",
            "Qualitative and mixed-methods reviews",
            (
                Element("sample", "Sample", "Who takes part, such as nurses in intensive care"),
                Element("phenomenon_of_interest", "Phenomenon of interest", "Such as experiences of moral distress"),
                Element("design", "Design", "How data is collected, such as interviews or focus groups"),
                Element("evaluation", "Evaluation", "What is examined, such as perceptions and coping strategies"),
                Element("research_type", "Research type", "Qualitative, quantitative, or mixed methods"),
            ),
        ),
        Framework(
            "PCC",
            "PCC",
            "Scoping reviews",
            (
                _POPULATION,
                Element("concept", "Concept", "The main idea mapped, such as digital health interventions"),
                Element("context", "Context", "Where it applies, such as primary care in low-income countries"),
            ),
        ),
    )
}

# Criteria can restrict these whatever the framework.
GENERAL_CRITERION_ELEMENTS = (
    _STUDY_DESIGN,
    Element("publication", "Publication", "Publication type, language, or date"),
    Element("setting", "Setting", "Such as hospital or community settings"),
    Element("other", "Other", "Anything else"),
)

FINER_CRITERIA = (
    Element("feasible", "Feasible", "Enough studies, time, skills, and resources to answer it"),
    Element("interesting", "Interesting", "Answers a question that reviewers and evidence users care about"),
    Element("novel", "Novel", "Not already answered by a recent, good-quality review"),
    Element("ethical", "Ethical", "Can be done without ethical concerns"),
    Element("relevant", "Relevant", "Could change practice, policy, or future research"),
)
FINER_RATINGS = ("yes", "partly", "no")


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    prisma_p_item: str
    guidance: str
    required: bool = False


PROTOCOL_SECTIONS = (
    Section(
        "title",
        "Title",
        "1a–1b",
        "Identify the report as a protocol of a systematic review, and say whether it updates a previous review.",
    ),
    Section(
        "registration",
        "Registration",
        "2",
        "If registered, give the name of the registry (such as PROSPERO) and the registration number.",
    ),
    Section(
        "authors",
        "Authors and contributions",
        "3a–3b",
        "Names, affiliations, and email addresses of all protocol authors, the corresponding author's contact "
        "details, each author's contribution, and the guarantor of the review.",
    ),
    Section(
        "amendments",
        "Amendments",
        "4",
        "If the protocol amends an earlier one, identify it and list the changes; otherwise, state how important "
        "amendments will be documented.",
    ),
    Section(
        "support",
        "Support and funding",
        "5a–5c",
        "Sources of financial or other support, the name of the funder or sponsor, and the roles of funders, "
        "sponsors, and institutions in developing the protocol.",
    ),
    Section(
        "competing_interests",
        "Competing interests",
        "—",
        "Financial and non-financial interests of each author that could influence the review, or a statement that "
        "there are none.",
    ),
    Section(
        "rationale",
        "Rationale",
        "6",
        "The rationale for the review in the context of what is already known.",
        required=True,
    ),
    Section(
        "objectives",
        "Objectives",
        "7",
        "An explicit statement of the questions the review will address, with reference to the elements of the "
        "question framework.",
        required=True,
    ),
    Section(
        "eligibility",
        "Eligibility criteria",
        "8",
        "The study and report characteristics used as eligibility criteria, such as design, setting, time frame, "
        "language, and publication status, with a rationale.",
    ),
    Section(
        "information_sources",
        "Information sources",
        "9",
        "All intended information sources (such as databases, trial registers, contact with study authors, and "
        "other grey literature) with planned dates of coverage.",
    ),
    Section(
        "search_strategy",
        "Search strategy",
        "10",
        "A draft search strategy for at least one database, including planned limits, so that it could be repeated.",
    ),
    Section(
        "data_management",
        "Data management",
        "11a",
        "How records and data will be managed throughout the review.",
    ),
    Section(
        "selection_process",
        "Selection process",
        "11b",
        "How studies will be selected through each phase of the review (such as two independent reviewers), "
        "including how disagreements are resolved.",
    ),
    Section(
        "data_collection",
        "Data collection process",
        "11c",
        "How data will be extracted from reports (such as piloted forms, independently, in duplicate) and how data "
        "will be obtained or confirmed with investigators.",
    ),
    Section(
        "data_items",
        "Data items",
        "12",
        "All variables for which data will be sought, and any planned data assumptions and simplifications.",
    ),
    Section(
        "outcomes",
        "Outcomes and prioritization",
        "13",
        "All outcomes for which data will be sought, including which are main and additional outcomes, with a "
        "rationale.",
    ),
    Section(
        "risk_of_bias",
        "Risk of bias in individual studies",
        "14",
        "Planned methods for assessing risk of bias in individual studies, whether at outcome or study level, and "
        "how the assessments will be used in the synthesis.",
    ),
    Section(
        "synthesis",
        "Data synthesis",
        "15a–15d",
        "When data will be synthesized quantitatively; planned summary measures, methods of combining data, and "
        "exploration of heterogeneity; additional analyses such as subgroup or sensitivity analyses; and the "
        "approach if a quantitative synthesis isn't appropriate.",
        required=True,
    ),
    Section(
        "meta_bias",
        "Meta-bias",
        "16",
        "Any planned assessment of meta-bias, such as publication bias across studies or selective reporting "
        "within studies.",
    ),
    Section(
        "confidence",
        "Confidence in cumulative evidence",
        "17",
        "How the certainty of the body of evidence will be assessed, such as with GRADE.",
    ),
    Section(
        "ai_use",
        "Use of AI tools",
        "—",
        "Which AI tools and models will be used, for which tasks, and how reviewers will check their output "
        "(following PRISMA-trAIce and RAISE guidance).",
    ),
    Section(
        "ethics_and_dissemination",
        "Ethics and dissemination",
        "—",
        "Whether ethics approval is needed, and how the results will be disseminated.",
    ),
)
SECTIONS_BY_KEY = {section.key: section for section in PROTOCOL_SECTIONS}
REQUIRED_SECTIONS = tuple(section for section in PROTOCOL_SECTIONS if section.required)

SYNTHESIS_APPROACHES = {
    "meta_analysis": "Meta-analysis",
    "swim": "Synthesis without meta-analysis (SWiM)",
    "narrative": "Narrative synthesis",
    "undecided": "Not decided yet",
}
OUTCOME_PRIORITIES = {"primary": "Primary", "secondary": "Secondary", "adverse": "Adverse event"}


def criterion_element_keys(framework_key: str) -> list[str]:
    """What a criterion can restrict: the framework's elements, then the general ones it doesn't already have."""
    framework = FRAMEWORKS.get(framework_key)
    keys = [element.key for element in framework.elements] if framework else []
    return keys + [element.key for element in GENERAL_CRITERION_ELEMENTS if element.key not in keys]


def describe_frameworks() -> str:
    """The frameworks as prompt text."""
    lines = []
    for framework in FRAMEWORKS.values():
        elements = "; ".join(f"{element.key} ({element.label}: {element.hint})" for element in framework.elements)
        lines.append(f"- {framework.key}, for {framework.use_for.lower()}: {elements}")
    return "\n".join(lines)


def catalog() -> dict:
    return {
        "frameworks": [
            {**asdict(framework), "elements": [asdict(element) for element in framework.elements]}
            for framework in FRAMEWORKS.values()
        ],
        "general_criterion_elements": [asdict(element) for element in GENERAL_CRITERION_ELEMENTS],
        "finer_criteria": [asdict(criterion) for criterion in FINER_CRITERIA],
        "finer_ratings": list(FINER_RATINGS),
        "sections": [asdict(section) for section in PROTOCOL_SECTIONS],
        "synthesis_approaches": [{"key": key, "label": label} for key, label in SYNTHESIS_APPROACHES.items()],
        "outcome_priorities": [{"key": key, "label": label} for key, label in OUTCOME_PRIORITIES.items()],
    }
