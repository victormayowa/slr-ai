"""Reporting checklists for the manuscript, filled in automatically from the manuscript's headings and the review's
records, with reviewers able to override each item's status and location.

Item topics are short labels, with the official checklist linked. PRISMA 2020, PRISMA-S, PRISMA-P, SWiM, and MOOSE items
follow the published checklists. The diagnostic accuracy and AI-use lists hold only the items specific to those reviews,
aligned to PRISMA-DTA and PRISMA-trAIce/RAISE; check them against the published wording before submission.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import models
from appraisal_tools import _AMSTAR_ITEMS
from claims import embeds, split_sentences
from manuscript_content import SECTION_SPECS
from protocol_design import project_sections
from protocol_frameworks import PROTOCOL_SECTIONS
from search_quality import press_status

STATUSES = ("reported", "partially_reported", "not_reported", "not_applicable")


@dataclass(frozen=True)
class Item:
    id: str
    topic: str
    section: str
    headings: tuple[str, ...] = ()
    rule: str = ""


@dataclass(frozen=True)
class ReportingChecklist:
    key: str
    label: str
    reference: str
    url: str
    applies: str
    items: tuple[Item, ...]
    note: str = ""


def _i(id: str, topic: str, section: str, *headings: str, rule: str = "") -> Item:
    return Item(id, topic, section, headings, rule)


PRISMA_2020 = ReportingChecklist(
    "prisma_2020",
    "PRISMA 2020",
    "Page MJ, et al. The PRISMA 2020 statement: an updated guideline for reporting systematic reviews. BMJ "
    "2021;372:n71.",
    "https://www.prisma-statement.org/prisma-2020-checklist",
    "always",
    (
        _i("1", "Identify the report as a systematic review", "title", rule="title_names_review"),
        _i("2", "Structured abstract (PRISMA 2020 for Abstracts)", "abstract"),
        _i("3", "Rationale in the context of existing knowledge", "introduction", "rationale"),
        _i("4", "Objectives or questions the review addresses", "introduction", "objective"),
        _i("5", "Eligibility criteria and how studies were grouped for syntheses", "methods", "eligibility"),
        _i(
            "6",
            "Information sources and the date each was last searched",
            "methods",
            "information source",
            rule="search_runs",
        ),
        _i(
            "7",
            "Full search strategies for all databases, registers, and websites",
            "methods",
            "search strategy",
            rule="search_runs",
        ),
        _i("8", "Selection process, including reviewers and automation tools", "methods", "selection"),
        _i("9", "Data collection process, including reviewers and automation tools", "methods", "data collection"),
        _i("10a", "Outcomes for which data were sought", "methods", "data items"),
        _i("10b", "Other variables for which data were sought", "methods", "data items"),
        _i("11", "Methods to assess risk of bias in included studies", "methods", "risk of bias", rule="appraisal"),
        _i("12", "Effect measures for each outcome", "methods", "effect measure", rule="analyses"),
        _i("13a", "Deciding which studies were eligible for each synthesis", "methods", "synthesis", rule="analyses"),
        _i("13b", "Preparing data for presentation or synthesis", "methods", "synthesis", rule="analyses"),
        _i("13c", "Tabulating or visually displaying results", "methods", "synthesis", rule="analyses"),
        _i("13d", "Methods to synthesize results and their rationale", "methods", "synthesis", rule="analyses"),
        _i("13e", "Methods to explore causes of heterogeneity", "methods", "synthesis", rule="analyses"),
        _i("13f", "Sensitivity analyses", "methods", "synthesis", rule="analyses"),
        _i("14", "Methods to assess risk of bias due to missing results", "methods", "reporting bias"),
        _i("15", "Methods to assess certainty in the body of evidence", "methods", "certainty", rule="grade"),
        _i(
            "16a",
            "Results of the search and selection process, with a flow diagram",
            "results",
            "study selection",
            rule="prisma_figure",
        ),
        _i("16b", "Studies that seemed eligible but were excluded, and why", "results", "study selection"),
        _i(
            "17",
            "Characteristics of included studies",
            "results",
            "study characteristics",
            rule="characteristics_table",
        ),
        _i("18", "Risk of bias in each included study", "results", "risk of bias", rule="appraisal"),
        _i(
            "19",
            "Results of individual studies with effect estimates",
            "results",
            "results of syntheses",
            rule="study_results_figure",
        ),
        _i(
            "20a",
            "Characteristics and risk of bias of studies in each synthesis",
            "results",
            "results of syntheses",
            rule="analyses",
        ),
        _i("20b", "Results of all statistical syntheses", "results", "results of syntheses", rule="analyses"),
        _i(
            "20c",
            "Investigations of possible causes of heterogeneity",
            "results",
            "results of syntheses",
            rule="analyses",
        ),
        _i("20d", "Results of sensitivity analyses", "results", "results of syntheses", rule="analyses"),
        _i(
            "21",
            "Assessments of risk of bias due to missing results",
            "results",
            "reporting bias",
            "results of syntheses",
        ),
        _i("22", "Certainty of evidence for each outcome", "results", "certainty", rule="grade"),
        _i("23a", "General interpretation of the results", "discussion", "interpretation"),
        _i("23b", "Limitations of the evidence", "discussion", "limitations of the evidence"),
        _i("23c", "Limitations of the review processes", "discussion", "limitations of the review"),
        _i("23d", "Implications for practice, policy, and future research", "discussion", "implications"),
        _i(
            "24a",
            "Registration information, or a statement that it wasn't registered",
            "other_information",
            "registration",
            rule="registration",
        ),
        _i("24b", "Where the protocol can be accessed", "other_information", "registration", "protocol"),
        _i("24c", "Amendments to the protocol", "other_information", "registration", "amendment"),
        _i(
            "25",
            "Sources of support and the role of funders",
            "other_information",
            "support",
            "funding",
            rule="statement_funding",
        ),
        _i(
            "26",
            "Competing interests of review authors",
            "other_information",
            "competing interest",
            rule="statement_competing_interests",
        ),
        _i(
            "27",
            "Availability of data, code, and other materials",
            "other_information",
            "availability",
            rule="statement_data_availability",
        ),
    ),
)

PRISMA_ABSTRACTS = ReportingChecklist(
    "prisma_abstracts",
    "PRISMA 2020 for Abstracts",
    "Page MJ, et al. BMJ 2021;372:n71 (abstract checklist).",
    "https://www.prisma-statement.org/prisma-2020-checklist",
    "always",
    (
        _i("1", "Title identifies the report as a systematic review", "title", rule="title_names_review"),
        _i("2", "Objectives", "abstract", "objective"),
        _i("3", "Eligibility criteria", "abstract", "method"),
        _i("4", "Information sources and dates last searched", "abstract", "method", rule="search_runs"),
        _i("5", "Risk of bias methods", "abstract", "method"),
        _i("6", "Synthesis methods", "abstract", "method"),
        _i("7", "Included studies and participants", "abstract", "result"),
        _i("8", "Synthesis of results", "abstract", "result"),
        _i("9", "Limitations of the evidence", "abstract", "conclusion", "discussion"),
        _i("10", "Interpretation", "abstract", "conclusion"),
        _i("11", "Funding", "abstract", "funding"),
        _i("12", "Registration", "abstract", "registration"),
    ),
)

PRISMA_S = ReportingChecklist(
    "prisma_s",
    "PRISMA-S",
    "Rethlefsen ML, et al. PRISMA-S: an extension to the PRISMA statement for reporting literature searches. Syst "
    "Rev 2021;10:39.",
    "https://www.prisma-statement.org/prisma-search",
    "always",
    (
        _i("1", "Database names and platforms", "methods", "information source", rule="database_runs"),
        _i("2", "Multi-database searching on one platform", "methods", "information source", rule="database_runs"),
        _i("3", "Study registries searched", "methods", "information source", rule="register_runs"),
        _i("4", "Online resources and browsing", "methods", "information source", rule="other_runs"),
        _i("5", "Citation searching", "methods", "information source", rule="citation_runs"),
        _i("6", "Contacts with authors or experts", "methods", "information source", "data collection"),
        _i("7", "Other methods", "methods", "information source"),
        _i("8", "Full search strategies", "methods", "search strategy", rule="search_runs"),
        _i("9", "Limits and restrictions", "methods", "search strategy"),
        _i("10", "Search filters", "methods", "search strategy"),
        _i("11", "Prior work used to develop the search", "methods", "search strategy"),
        _i("12", "Updates to the search", "methods", "search strategy", rule="surveillance"),
        _i("13", "Dates of searches", "methods", "information source", rule="search_runs"),
        _i("14", "Peer review of the search", "methods", "search strategy", rule="press"),
        _i("15", "Total records identified", "results", "study selection", rule="prisma_figure"),
        _i("16", "Deduplication process", "methods", "selection"),
    ),
)

SWIM = ReportingChecklist(
    "swim",
    "SWiM",
    "Campbell M, et al. Synthesis without meta-analysis (SWiM) in systematic reviews: reporting guideline. BMJ "
    "2020;368:l6890.",
    "https://www.bmj.com/content/368/bmj.l6890",
    "swim",
    (
        _i("1", "How studies were grouped for synthesis", "methods", "synthesis"),
        _i("2", "Standardized metric and transformation methods", "methods", "effect measure", "synthesis"),
        _i("3", "Synthesis methods", "methods", "synthesis"),
        _i("4", "Criteria used to prioritize results for summary and synthesis", "methods", "synthesis"),
        _i("5", "Investigation of heterogeneity in reported effects", "methods", "synthesis"),
        _i("6", "Certainty of evidence", "methods", "certainty", rule="grade"),
        _i("7", "Data presentation methods", "methods", "synthesis"),
        _i("8", "Reporting results", "results", "results of syntheses"),
        _i("9", "Limitations of the synthesis", "discussion", "limitation"),
    ),
)

PRISMA_NMA = ReportingChecklist(
    "prisma_nma",
    "PRISMA-NMA (network items)",
    "Hutton B, et al. The PRISMA extension statement for network meta-analyses. Ann Intern Med 2015;162:777-84.",
    "https://www.prisma-statement.org/nma",
    "nma",
    (
        _i("S1", "Geometry of the network: methods to explore it", "methods", "synthesis"),
        _i("S2", "Methods to assess inconsistency", "methods", "synthesis"),
        _i("S3", "Presentation of the network structure", "results", "results of syntheses", rule="network_figure"),
        _i("S4", "Summary of network geometry", "results", "results of syntheses"),
        _i("S5", "Exploration for inconsistency", "results", "results of syntheses"),
    ),
    "Items specific to network meta-analysis; the rest of PRISMA-NMA follows PRISMA with modified items.",
)

PRISMA_DTA = ReportingChecklist(
    "prisma_dta",
    "PRISMA-DTA (diagnostic accuracy items)",
    "McInnes MDF, et al. Preferred reporting items for a systematic review and meta-analysis of diagnostic test "
    "accuracy studies. JAMA 2018;319:388-96.",
    "https://www.prisma-statement.org/dta",
    "dta",
    (
        _i(
            "D1", "Title identifies a systematic review of diagnostic test accuracy", "title", rule="title_names_review"
        ),
        _i("D2", "Clinical role of the index test and the target condition", "introduction", "rationale"),
        _i(
            "D3",
            "Index tests, reference standards, and target conditions in eligibility criteria",
            "methods",
            "eligibility",
        ),
        _i("D4", "Definitions of extracted data, including 2×2 data", "methods", "data items"),
        _i("D5", "Risk of bias and applicability assessment", "methods", "risk of bias", rule="appraisal"),
        _i("D6", "Statistical methods for sensitivity and specificity", "methods", "synthesis", rule="analyses"),
        _i("D7", "Positivity thresholds and how they were handled", "methods", "synthesis"),
        _i(
            "D8",
            "Two-by-two data and sensitivity and specificity for each study",
            "results",
            "results of syntheses",
            rule="study_results_figure",
        ),
        _i("D9", "Summary estimates and SROC curve", "results", "results of syntheses", rule="analyses"),
        _i("D10", "Applicability concerns", "results", "risk of bias"),
    ),
    "Items specific to diagnostic accuracy reviews, which also report the PRISMA 2020 items. Check the published "
    "wording.",
)

MOOSE = ReportingChecklist(
    "moose",
    "MOOSE",
    "Stroup DF, et al. Meta-analysis of observational studies in epidemiology: a proposal for reporting. JAMA "
    "2000;283:2008-12.",
    "https://jamanetwork.com/journals/jama/fullarticle/192614",
    "observational",
    (
        _i("1", "Problem definition", "introduction", "rationale"),
        _i("2", "Hypothesis statement", "introduction", "objective"),
        _i("3", "Description of the study outcomes", "methods", "data items"),
        _i("4", "Type of exposure or intervention", "methods", "eligibility"),
        _i("5", "Type of study designs", "methods", "eligibility"),
        _i("6", "Study population", "methods", "eligibility"),
        _i("7", "Qualifications of searchers", "methods", "search strategy"),
        _i(
            "8", "Search strategy, including time period and keywords", "methods", "search strategy", rule="search_runs"
        ),
        _i(
            "9",
            "Effort to include all available studies, including contacting authors",
            "methods",
            "information source",
        ),
        _i("10", "Databases and registries searched", "methods", "information source", rule="search_runs"),
        _i("11", "Search software used", "methods", "information source"),
        _i("12", "Use of hand searching", "methods", "information source"),
        _i("13", "List of citations located and those excluded", "results", "study selection", rule="prisma_figure"),
        _i("14", "Handling of articles in languages other than English", "methods", "eligibility"),
        _i("15", "Handling of abstracts and unpublished studies", "methods", "eligibility"),
        _i("16", "Description of contact with authors", "methods", "data collection"),
        _i("17", "Relevance of the studies assembled to the hypothesis", "methods", "eligibility"),
        _i("18", "Rationale for the selection and coding of data", "methods", "data collection"),
        _i("19", "How data were classified and coded", "methods", "data items"),
        _i("20", "Assessment of confounding", "methods", "risk of bias"),
        _i("21", "Assessment of study quality", "methods", "risk of bias", rule="appraisal"),
        _i("22", "Assessment of heterogeneity", "methods", "synthesis"),
        _i("23", "Statistical methods in enough detail to replicate", "methods", "synthesis", rule="analyses"),
        _i("24", "Appropriate tables and graphics", "results", "study characteristics", rule="characteristics_table"),
        _i(
            "25",
            "Graph of individual study estimates and the overall estimate",
            "results",
            "results of syntheses",
            rule="study_results_figure",
        ),
        _i(
            "26",
            "Table of descriptive information for each study",
            "results",
            "study characteristics",
            rule="characteristics_table",
        ),
        _i("27", "Results of sensitivity testing", "results", "results of syntheses"),
        _i("28", "Statistical uncertainty of findings", "results", "results of syntheses", rule="analyses"),
        _i("29", "Quantitative assessment of bias", "discussion", "limitation"),
        _i("30", "Justification for exclusions", "results", "study selection"),
        _i("31", "Assessment of the quality of included studies", "discussion", "limitation"),
        _i("32", "Consideration of alternative explanations for the results", "discussion", "interpretation"),
        _i("33", "Generalization of the conclusions", "discussion", "implication"),
        _i("34", "Guidelines for future research", "discussion", "implication"),
        _i("35", "Disclosure of funding source", "other_information", "support", "funding", rule="statement_funding"),
    ),
)

AI_USE = ReportingChecklist(
    "ai_use",
    "AI use disclosure (PRISMA-trAIce and RAISE)",
    "Items aligned to PRISMA-trAIce and the RAISE guidance on AI in evidence synthesis.",
    "https://www.prisma-statement.org/",
    "ai",
    (
        _i("A1", "AI tools, providers, and model versions", "ai_use", rule="ai_runs"),
        _i("A2", "Review tasks the AI was used for", "ai_use", rule="ai_runs"),
        _i("A3", "Prompts or configuration (prompt versions)", "ai_use", rule="ai_runs"),
        _i("A4", "Human oversight of AI outputs", "ai_use"),
        _i("A5", "Validation or performance evaluation of the AI", "ai_use", "selection", rule="qa_or_stopping"),
        _i("A6", "Data handling and where data were processed", "ai_use"),
        _i("A7", "Limitations of the AI use", "discussion", "limitations of the review"),
    ),
    "Check these items against the published PRISMA-trAIce checklist before submission.",
)

AMSTAR_SECTIONS = (
    ("introduction", "objective"),
    ("other_information", "registration"),
    ("methods", "eligibility"),
    ("methods", "search strategy"),
    ("methods", "selection"),
    ("methods", "data collection"),
    ("results", "study selection"),
    ("results", "study characteristics"),
    ("methods", "risk of bias"),
    ("results", "study characteristics"),
    ("methods", "synthesis"),
    ("results", "results of syntheses"),
    ("discussion", "interpretation"),
    ("discussion", "interpretation"),
    ("methods", "reporting bias"),
    ("other_information", "competing interest"),
)
AMSTAR_2 = ReportingChecklist(
    "amstar2",
    "AMSTAR 2 self-check",
    "Shea BJ, et al. AMSTAR 2: a critical appraisal tool for systematic reviews. BMJ 2017;358:j4008.",
    "https://amstar.ca/Amstar-2.php",
    "always",
    tuple(
        _i(str(n), topic + (" (critical)" if critical else ""), section, heading)
        for n, ((topic, critical, _), (section, heading)) in enumerate(
            zip(_AMSTAR_ITEMS, AMSTAR_SECTIONS, strict=True), start=1
        )
    ),
)

PRISMA_P = ReportingChecklist(
    "prisma_p",
    "PRISMA-P (protocol)",
    "Moher D, et al. Preferred reporting items for systematic review and meta-analysis protocols (PRISMA-P) 2015. "
    "Syst Rev 2015;4:1.",
    "https://www.prisma-statement.org/protocols",
    "always",
    tuple(Item(section.prisma_p_item, section.label, f"protocol:{section.key}") for section in PROTOCOL_SECTIONS),
    "Assessed against the protocol document.",
)

CHECKLISTS = {
    c.key: c
    for c in (PRISMA_2020, PRISMA_ABSTRACTS, PRISMA_S, PRISMA_P, AI_USE, SWIM, PRISMA_NMA, PRISMA_DTA, MOOSE, AMSTAR_2)
}
OBSERVATIONAL_TOOLS = {
    "robins_i",
    "robins_e",
    "nos_cohort",
    "nos_case_control",
    "jbi_cohort",
    "jbi_case_control",
    "jbi_cross_sectional",
}


def checklist_context(db: Session, project: models.Project, manuscript: models.Manuscript) -> dict[str, Any]:
    project_id = project.id
    kinds = set(db.scalars(select(models.SearchRun.kind).where(models.SearchRun.project_id == project_id)))
    analyses = db.scalars(
        select(models.Analysis).where(models.Analysis.project_id == project_id, models.Analysis.status == "approved")
    ).all()
    tools = set(
        db.scalars(select(models.AppraisalAssessment.tool).where(models.AppraisalAssessment.project_id == project_id))
    )
    all_content = "\n".join(section.content for section in manuscript.sections)
    embedded = {f"{kind}:{key}" for kind, key in embeds(all_content)}

    def count(model: Any, *conditions: Any) -> int:
        return (
            db.scalar(select(func.count()).select_from(model).where(model.project_id == project_id, *conditions)) or 0
        )

    return {
        "title": manuscript.title,
        "kinds": kinds,
        "analysis_types": {a.analysis_type for a in analyses},
        "tools": tools,
        "grade": count(models.GradeAssessment) > 0,
        "registration": count(models.ProtocolRegistration) > 0,
        "surveillance": count(models.SurveillanceSchedule) > 0,
        "ai_runs": count(models.AIRun) > 0,
        "qa": count(models.QASample) > 0 or count(models.StoppingEvaluation) > 0,
        "press": bool(press_status(db, project_id).get("met")),
        "embedded": embedded,
        "statements": {k for k, v in manuscript.statements.items() if v},
        "protocol_sections": {k for k, row in project_sections(db, project_id).items() if row.content.strip()},
    }


def _yes_or_na(flag: bool) -> bool | None:
    return True if flag else None


RULES: dict[str, Callable[[Mapping[str, Any]], bool | None]] = {
    "title_names_review": lambda c: bool(re.search(r"systematic review|meta-analys", c["title"], re.I)),
    "search_runs": lambda c: bool(c["kinds"]),
    "database_runs": lambda c: bool(c["kinds"] & {"database", "import"}),
    "register_runs": lambda c: _yes_or_na("register" in c["kinds"]),
    "citation_runs": lambda c: _yes_or_na("citation" in c["kinds"]),
    "other_runs": lambda c: _yes_or_na(bool(c["kinds"] - {"database", "register", "import", "citation"})),
    "surveillance": lambda c: _yes_or_na(c["surveillance"]),
    "press": lambda c: c["press"],
    "appraisal": lambda c: bool(c["tools"]),
    "analyses": lambda c: bool(c["analysis_types"]),
    "grade": lambda c: c["grade"],
    "registration": lambda c: c["registration"],
    "prisma_figure": lambda c: "figure:prisma" in c["embedded"],
    "characteristics_table": lambda c: "table:study_characteristics" in c["embedded"],
    "study_results_figure": lambda c: any(e.startswith("figure:run:") for e in c["embedded"]),
    "network_figure": lambda c: any(e.endswith(":network") for e in c["embedded"]),
    "statement_funding": lambda c: "funding" in c["statements"],
    "statement_competing_interests": lambda c: "competing_interests" in c["statements"],
    "statement_data_availability": lambda c: "data_availability" in c["statements"],
    "ai_runs": lambda c: c["ai_runs"],
    "qa_or_stopping": lambda c: c["qa"],
}


def applicable(checklist: ReportingChecklist, context: Mapping[str, Any]) -> bool:
    return {
        "always": True,
        "swim": "swim" in context["analysis_types"],
        "nma": "nma" in context["analysis_types"],
        "dta": "dta" in context["analysis_types"],
        "observational": bool(context["tools"] & OBSERVATIONAL_TOOLS),
        "ai": context["ai_runs"],
    }[checklist.applies]


def _heading_blocks(content: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    heading = ""
    lines: list[str] = []
    for line in content.splitlines():
        if line.lstrip().startswith("#"):
            blocks.append((heading, "\n".join(lines)))
            heading = line.lstrip("#").strip()
            lines = []
        else:
            lines.append(line)
    blocks.append((heading, "\n".join(lines)))
    return blocks


def _has_content(text: str) -> bool:
    return bool(split_sentences(text) or embeds(text))


def evaluate(
    checklist: ReportingChecklist,
    manuscript: models.Manuscript,
    context: Mapping[str, Any],
    overrides: Mapping[str, models.ManuscriptChecklistItem],
    protocol_labels: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    sections = {s.key: s for s in manuscript.sections}
    out = []
    for item in checklist.items:
        rule = RULES[item.rule](context) if item.rule else True
        location = ""
        if item.section == "title":
            present = bool(manuscript.title.strip())
            location = "Title" if present else ""
        elif item.section.startswith("protocol:"):
            key = item.section.split(":", 1)[1]
            present = key in context["protocol_sections"]
            location = f"Protocol › {(protocol_labels or {}).get(key, key)}" if present else ""
        else:
            section = sections.get(item.section)
            title = SECTION_SPECS[item.section].title if item.section in SECTION_SPECS else item.section
            present = False
            if section is not None:
                if item.headings:
                    for heading, text in _heading_blocks(section.content):
                        if heading and any(h in heading.casefold() for h in item.headings) and _has_content(text):
                            present, location = True, f"{title} › {heading}"
                            break
                else:
                    present = _has_content(section.content)
                    location = title if present else ""
        if rule is None:
            auto = "not_applicable"
        elif present and rule:
            auto = "reported"
        elif present or rule and item.section == "title":
            auto = "partially_reported"
        else:
            auto = "not_reported"
        override = overrides.get(item.id)
        out.append(
            {
                "item_id": item.id,
                "topic": item.topic,
                "section": item.section,
                "auto_status": auto,
                "auto_location": location,
                "status": override.status if override and override.status else auto,
                "location": override.location if override and override.location else location,
                "note": override.note if override else "",
                "overridden": bool(override and override.status),
            }
        )
    return out


def complete(items: list[dict[str, Any]]) -> bool:
    return all(item["status"] in ("reported", "not_applicable") for item in items)
