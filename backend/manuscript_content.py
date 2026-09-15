"""Manuscript sections (PRISMA 2020 structure) and text generated from the pipeline's own records.

Methods, the study selection and synthesis results, other information, and the AI-use disclosure are written from what
was actually done, with evidence markers on every sentence so they verify. Other sections start as PRISMA headings for
authors to write, or AI drafts they accept.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_disclosure import ai_use, disclosure_text
from appraisal_tools import JUDGMENT_SETS, TOOLS
from certainty_routes import _assessments, effect_summary, sof_row
from evidence_catalog import approved_analyses, latest_snapshot, prisma_totals, run_date
from prisma_flow import flow_counts
from review_settings import load_settings
from search_quality import press_status
from synthesis_data import extraction_source
from synthesis_routes import ANALYSIS_TYPE_LABELS


@dataclass(frozen=True)
class SectionSpec:
    key: str
    title: str
    guidance: str
    skeleton: str = ""
    generated: bool = False


SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        "abstract",
        "Abstract",
        "PRISMA 2020 for Abstracts: background, objectives, eligibility, information sources, risk of bias, synthesis "
        "methods, included studies, synthesis results, limitations, interpretation, funding, and registration.",
        "## Background\n\n## Objectives\n\n## Methods\n\n## Results\n\n## Conclusions\n\n## Funding\n\n## "
        "Registration\n",
    ),
    SectionSpec(
        "introduction",
        "Introduction",
        "PRISMA 2020 items 3-4: the rationale in the context of existing knowledge, and the review's objectives.",
        "## Rationale\n\n## Objectives\n",
    ),
    SectionSpec("methods", "Methods", "PRISMA 2020 items 5-15.", generated=True),
    SectionSpec("results", "Results", "PRISMA 2020 items 16-22.", generated=True),
    SectionSpec(
        "discussion",
        "Discussion",
        "PRISMA 2020 item 23: interpretation, limitations of the evidence and of the review processes, implications.",
        "## Interpretation\n\n## Limitations of the evidence\n\n## Limitations of the review processes\n\n"
        "## Implications for practice, policy, and research\n",
        generated=True,
    ),
    SectionSpec("conclusions", "Conclusions", "The main conclusions, consistent with the certainty of the evidence."),
    SectionSpec(
        "other_information",
        "Other information",
        "PRISMA 2020 items 24-27: registration, support, competing interests, "
        "and availability of data, code, and other materials.",
        generated=True,
    ),
    SectionSpec(
        "ai_use",
        "Use of artificial intelligence",
        "Which AI tools and versions were used, for which tasks, with what human oversight and validation.",
        generated=True,
    ),
)
SECTION_SPECS = {spec.key: spec for spec in SECTIONS}
MEASURE_NAMES = {
    "RR": "risk ratios",
    "OR": "odds ratios",
    "RD": "risk differences",
    "HR": "hazard ratios",
    "MD": "mean differences",
    "SMD": "standardized mean differences",
    "ROM": "ratios of means",
}
REVIEWER_WORDS = {1: "one reviewer", 2: "two reviewers independently"}


def join_words(items: list[str]) -> str:
    items = [item for item in items if item]
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _f(value: float | None, digits: int = 2) -> str:
    return "–" if value is None else f"{value:.{digits}f}"


def methods_text(db: Session, project: models.Project) -> str:
    project_id = project.id
    protocol = project.protocol
    parts: list[str] = ["## Eligibility criteria", ""]
    snapshot = latest_snapshot(db, project_id, "protocol")
    criteria = db.scalars(
        select(models.Criterion)
        .where(models.Criterion.project_id == project_id, models.Criterion.status == "accepted")
        .order_by(models.Criterion.kind, models.Criterion.id)
    ).all()
    lead = "Eligibility criteria were specified in advance in the protocol"
    if snapshot:
        lead += f" (version {snapshot.version})"
    if protocol:
        lead += f", structured with the {protocol.framework} framework"
    parts.append(f"{lead}. [#protocol]")
    parts.append("")
    parts += [f"- {c.kind.capitalize()} criterion: {c.text} [#protocol]" for c in criteria]

    runs = db.scalars(
        select(models.SearchRun).where(models.SearchRun.project_id == project_id).order_by(models.SearchRun.id)
    ).all()
    database_runs = [r for r in runs if r.kind in ("database", "register", "import")]
    other_runs = [r for r in runs if r not in database_runs]
    parts += ["", "## Information sources", ""]
    if database_runs:
        parts.append(f"We searched {join_words(sorted({r.database for r in database_runs}))}. [#search]")
        parts.append("")
        parts += [
            f"- {r.source_label} ({r.interface or r.database}): {r.result_count} records, searched on "
            f"{run_date(r)}. [#search]"
            for r in database_runs
        ]
    if other_runs:
        parts += ["", "Other methods included citation searching and grey literature sources:", ""]
        parts += [
            f"- {r.source_label} ({r.interface or r.database}): {r.result_count} records, searched on "
            f"{run_date(r)}. [#search]"
            for r in other_runs
        ]

    parts += ["", "## Search strategy", ""]
    parts.append("The full search strategy for every source is reported in the supplementary material. [#search]")
    if press_status(db, project_id).get("met"):
        parts.append(
            "Search strategies were peer reviewed with the PRESS 2015 guideline, or peer review was waived with a "
            "recorded reason. [#search]"
        )

    settings = load_settings(db, project_id)
    parts += ["", "## Selection process", ""]
    title_reviewers = REVIEWER_WORDS[settings.screening.title_abstract_reviewers]
    full_reviewers = REVIEWER_WORDS[settings.screening.full_text_reviewers]
    blinded = (
        " without seeing each other's decisions"
        if settings.screening.blind_dual_screening
        and 2
        in (
            settings.screening.title_abstract_reviewers,
            settings.screening.full_text_reviewers,
        )
        else ""
    )
    parts.append(
        "Duplicate records were identified by identifiers and title matching, and possible duplicates were "
        "confirmed by a reviewer. [#screening]"
    )
    parts.append(
        f"Titles and abstracts were screened by {title_reviewers}{blinded}, and full texts by {full_reviewers}; "
        "disagreements were resolved by adjudication. [#screening]"
    )
    stopping = db.scalar(
        select(models.StoppingEvaluation.id).where(
            models.StoppingEvaluation.project_id == project_id, models.StoppingEvaluation.accepted_at.is_not(None)
        )
    )
    if stopping:
        parts.append(
            "Title and abstract screening stopped once a statistical stopping rule, accepted by a reviewer, "
            "indicated the recall target had been reached. [#screening]"
        )
    ai_tasks = {t["task"] for t in ai_use(db, project_id)["tasks"]}
    if ai_tasks & {"screening", "fulltext_screening"}:
        parts.append("AI screening suggestions supported, but never replaced, reviewers' decisions. [#ai_use]")

    content, _, _ = extraction_source(db, project_id)
    parts += ["", "## Data collection process", ""]
    mode = settings.extraction.mode
    collection = {
        "single": "One reviewer extracted data into a structured form",
        "dual": "Two reviewers extracted data independently into a structured form and reconciled discrepancies",
        "human_and_ai": (
            "One reviewer extracted data, an AI model served as a second extractor, and a reviewer reconciled "
            "every discrepancy"
        ),
    }[mode]
    parts.append(f"{collection}; the data set was locked before analysis. [#extraction]")
    parts += ["", "## Data items", ""]
    if content["fields"]:
        parts.append(
            f"The extraction form collected {join_words([f['name'] for f in content['fields']])}. [#extraction]"
        )

    assessments = db.scalars(
        select(models.AppraisalAssessment.tool).where(models.AppraisalAssessment.project_id == project_id)
    ).all()
    parts += ["", "## Study risk of bias assessment", ""]
    if assessments:
        labels = join_words(sorted({TOOLS[t].label for t in assessments if t in TOOLS}))
        parts.append(
            f"Risk of bias was assessed with {labels}, answering each tool's signalling questions; every domain "
            "judgment was signed off with a written rationale. [#appraisal]"
        )

    analyses = approved_analyses(db, project_id)
    parts += ["", "## Effect measures", ""]
    for analysis, run in analyses:
        measure = run.spec.get("measure", "")
        if analysis.analysis_type in ("pairwise", "bayesian", "rve", "ipd", "nma") and measure in MEASURE_NAMES:
            parts.append(
                f"For {analysis.outcome.lower()}, effects were expressed as {MEASURE_NAMES[measure]}. "
                f"[#analysis:{analysis.id}]"
            )
    parts += ["", "## Synthesis methods", ""]
    for analysis, run in analyses:
        spec = run.spec
        label = ANALYSIS_TYPE_LABELS.get(analysis.analysis_type, analysis.analysis_type).lower()
        sentence = f"{analysis.title}: {label}"
        if analysis.analysis_type == "pairwise":
            model = spec.get("model", "random")
            if model == "random":
                sentence += (
                    f" with a random-effects model ({spec.get('tau_method', 'REML')} estimator of "
                    "between-study variance"
                )
                sentence += ", with the Hartung-Knapp-Sidik-Jonkman adjustment)" if spec.get("hksj") else ")"
                if spec.get("prediction_interval"):
                    sentence += " and prediction intervals"
            else:
                sentence += {
                    "fixed": " with a fixed-effect inverse-variance model",
                    "mantel_haenszel": " with the Mantel-Haenszel method",
                    "peto": " with the Peto method",
                    "glmm": " with a generalized linear mixed model",
                }.get(model, "")
        post_hoc = "" if analysis.prespecified else f" (not pre-specified: {analysis.justification.rstrip('.')})"
        sentence += f"{post_hoc}. [#analysis:{analysis.id}]"
        parts.append(sentence)
        if analysis.analysis_type == "pairwise":
            parts.append(f"Heterogeneity was quantified with I² and τ². [#analysis:{analysis.id}]")
            sensitivity = [
                name
                for key, name in (
                    ("leave_one_out", "leave-one-out analysis"),
                    ("influence", "influence diagnostics"),
                    ("exclude_high_risk_of_bias", "excluding studies at high risk of bias"),
                )
                if (spec.get("sensitivity") or {}).get(key)
            ]
            if sensitivity:
                parts.append(f"Sensitivity analyses included {join_words(sensitivity)}. [#analysis:{analysis.id}]")
        packages = ", ".join(f"{k} {v}" for k, v in sorted(run.packages.items()))
        parts.append(f"Analyses were run in {run.r_version} with {packages}. [#analysis:{analysis.id}]")
    parts += ["", "## Reporting bias assessment", ""]
    for analysis, run in analyses:
        bias = run.spec.get("publication_bias") or {}
        tests = [
            name
            for key, name in (
                ("funnel", "funnel plots"),
                ("egger", "Egger's test"),
                ("begg", "Begg's test"),
                ("trim_fill", "trim and fill"),
                ("pet_peese", "PET-PEESE"),
                ("selection_model", "a selection model"),
            )
            if bias.get(key)
        ]
        if tests:
            parts.append(
                f"For {analysis.outcome.lower()}, small-study effects were assessed with {join_words(tests)}. "
                f"[#analysis:{analysis.id}]"
            )
    parts += ["", "## Certainty assessment", ""]
    if _assessments(db, project_id):
        parts.append(
            "Certainty of evidence was assessed with GRADE for each outcome, and every rating was justified and "
            "signed off by a methodologist. [#certainty]"
        )
    return "\n".join(parts).strip() + "\n"


def results_text(db: Session, project: models.Project, study_references: dict[int, list[int]] | None = None) -> str:
    project_id = project.id
    totals = prisma_totals(flow_counts(db, project_id))
    parts = ["## Study selection", ""]
    parts.append(
        f"The searches identified {totals['records_identified']} records, of which {totals['duplicates_removed']} "
        "were duplicates. [#prisma]"
    )
    parts.append(
        f"We screened {totals['records_screened']} records and excluded {totals['records_excluded']}. [#prisma]"
    )
    if totals["excluded_by_automation"]:
        parts.append(
            f"{totals['excluded_by_automation']} records were not screened after the stopping rule was accepted. "
            "[#prisma]"
        )
    parts.append(
        f"We sought {totals['reports_sought']} reports; {totals['reports_not_retrieved']} could not be retrieved "
        f"and {totals['reports_assessed']} were assessed for eligibility. [#prisma]"
    )
    if totals["reports_excluded_total"]:
        reasons = "; ".join(f"{reason}, {count}" for reason, count in totals["reports_excluded"].items())
        parts.append(f"We excluded {totals['reports_excluded_total']} reports ({reasons}). [#prisma]")
    parts.append(
        f"In total, {totals['studies_included']} studies reported in {totals['reports_included']} reports were "
        "included. [#prisma]"
    )
    parts += ["", "[[figure:prisma]]", "", "## Study characteristics", ""]
    content, _, _ = extraction_source(db, project_id)
    parts.append(
        f"The characteristics of the {len(content['studies'])} included studies are summarized in the table. "
        "[#extraction]"
    )
    if study_references:
        studies = [s for s in content["studies"] if study_references.get(s["study_id"])]
        if studies:
            evidence = " ".join(f"[#study:{s['study_id']}]" for s in studies)
            cites = "; ".join(f"@{ref}" for s in studies for ref in study_references[s["study_id"]])
            parts.append(f"The included studies were {join_words([s['label'] for s in studies])} {evidence} [{cites}].")
    parts += ["", "[[table:study_characteristics]]", "", "## Risk of bias in studies", ""]
    assessments = db.scalars(
        select(models.AppraisalAssessment).where(
            models.AppraisalAssessment.project_id == project_id, models.AppraisalAssessment.status == "signed_off"
        )
    ).all()
    by_tool: dict[str, list[models.AppraisalAssessment]] = {}
    for assessment in assessments:
        by_tool.setdefault(assessment.tool, []).append(assessment)
    for tool_key, items in by_tool.items():
        tool = TOOLS[tool_key]
        labels = dict(JUDGMENT_SETS[tool.overall_judgments])
        counts: dict[str, int] = {}
        for item in items:
            label = labels.get(item.overall_judgment or "", item.overall_judgment or "")
            counts[label] = counts.get(label, 0) + 1
        judged = join_words([f"{count} {label.lower()}" for label, count in counts.items()])
        parts.append(
            f"Of {len(items)} {tool.label} assessments, the overall judgments were {judged}. [#appraisal:{tool_key}]"
        )
    if by_tool:
        parts += ["", "[[table:risk_of_bias]]"]
    parts += ["", "## Results of syntheses", ""]
    for analysis, run in approved_analyses(db, project_id):
        summary = effect_summary(run)
        results = run.results or {}
        marker = f"[#analysis:{analysis.id}]"
        measure = run.spec.get("measure", "")
        ratio = summary.get("exp_estimate") is not None
        estimate = summary.get("exp_estimate") if ratio else summary.get("estimate")
        low = summary.get("exp_ci_lower") if ratio else summary.get("ci_lower")
        high = summary.get("exp_ci_upper") if ratio else summary.get("ci_upper")
        people = f" ({summary['participants']} participants)" if summary.get("participants") else ""
        if analysis.analysis_type in ("pairwise", "bayesian", "rve", "ipd") and estimate is not None:
            sentence = (
                f"For {analysis.outcome.lower()}, {summary['studies']} studies{people} contributed; the "
                f"pooled {measure} was {_f(estimate)} (95% CI {_f(low)} to {_f(high)})"
            )
            if isinstance(summary.get("I2"), int | float):
                sentence += f", with I² = {summary['I2']:.0f}%"
            parts.append(f"{sentence}. {marker}")
        elif analysis.analysis_type == "dta" and results.get("summary"):
            s = results["summary"]
            parts.append(
                f"Across {s['k']} studies, summary sensitivity was {_f(s['sensitivity'])} "
                f"(95% CI {_f(s['sensitivity_ci'][0])} to {_f(s['sensitivity_ci'][1])}) and specificity "
                f"{_f(s['specificity'])} "
                f"(95% CI {_f(s['specificity_ci'][0])} to {_f(s['specificity_ci'][1])}). {marker}"
            )
        elif analysis.analysis_type == "nma" and results.get("model"):
            m = results["model"]
            parts.append(
                f"The network for {analysis.outcome.lower()} included {m['treatments']} treatments compared in "
                f"{m['studies']} studies. {marker}"
            )
        elif analysis.analysis_type == "swim" and results.get("vote_counting"):
            v = results["vote_counting"]
            parts.append(
                f"Of {v['studies']} studies, {v['benefit']} showed benefit and {v['harm']} showed harm. {marker}"
            )
        names = {p.get("name") for p in run.plots}
        for plot in ("forest", "network", "sroc", "effect_direction", "forest_two_stage"):
            if plot in names:
                parts += ["", f"[[figure:run:{run.id}:{plot}]]", ""]
                break
        if "funnel" in names:
            parts += ["", f"[[figure:run:{run.id}:funnel]]", ""]
    grades = _assessments(db, project_id)
    if grades:
        parts += ["", "## Certainty of evidence", ""]
        for grade in grades:
            row = sof_row(db, project, grade)
            parts.append(
                f"{row['informative_statement'].rstrip('.')} ({row['certainty_label'].lower()} certainty "
                f"evidence). [#grade:{grade.id}]"
            )
        parts += ["", "[[table:sof]]"]
    return "\n".join(parts).strip() + "\n"


def discussion_text(db: Session, project: models.Project) -> str:
    spec = SECTION_SPECS["discussion"]
    limitations = db.scalars(
        select(models.InterpretationText).where(
            models.InterpretationText.project_id == project.id,
            models.InterpretationText.status == "approved",
            models.InterpretationText.kind == "limitations",
        )
    ).all()
    if not limitations:
        return spec.skeleton
    items = "\n".join(
        f"{line.strip()} [#certainty]" if line.strip() else ""
        for text in limitations
        for line in text.content.splitlines()
    )
    return spec.skeleton.replace("## Limitations of the evidence\n", f"## Limitations of the evidence\n\n{items}\n")


def other_information_text(db: Session, project: models.Project, manuscript: models.Manuscript) -> str:
    registrations = db.scalars(
        select(models.ProtocolRegistration).where(models.ProtocolRegistration.project_id == project.id)
    ).all()
    parts = ["## Registration and protocol", ""]
    for registration in registrations:
        if registration.status == "waived":
            parts.append(
                "The review was not registered "
                f"(registration waived: {(registration.waiver_reason or '').rstrip('.')}). [#protocol]"
            )
        else:
            parts.append(
                f"The review was registered with {registration.registry_name} as {registration.registration_id} "
                f"(protocol version {registration.protocol_version}). [#protocol]"
            )
    versions = db.scalars(
        select(models.StageSnapshot.version).where(
            models.StageSnapshot.project_id == project.id, models.StageSnapshot.stage == "protocol"
        )
    ).all()
    if len(versions) > 1:
        parts.append(
            f"The protocol was amended {len(versions) - 1} times; amendments and their rationales are listed in "
            "the supplementary material. [#protocol]"
        )
    statements = manuscript.statements
    for heading, key, placeholder in (
        ("Support", "funding", "State the sources of financial or non-financial support and the role of funders."),
        ("Competing interests", "competing_interests", "Declare any competing interests of the review authors."),
        (
            "Availability of data, code, and other materials",
            "data_availability",
            "State where the extraction data set, analysis code, and other materials are available.",
        ),
    ):
        parts += ["", f"## {heading}", ""]
        parts.append(f"{statements[key]} [#statements]" if statements.get(key) else placeholder)
    return "\n".join(parts).strip() + "\n"


def generate(
    db: Session,
    project: models.Project,
    manuscript: models.Manuscript,
    key: str,
    study_references: dict[int, list[int]] | None = None,
) -> str:
    if key == "methods":
        return methods_text(db, project)
    if key == "results":
        return results_text(db, project, study_references)
    if key == "discussion":
        return discussion_text(db, project)
    if key == "other_information":
        return other_information_text(db, project, manuscript)
    if key == "ai_use":
        return disclosure_text(ai_use(db, project.id))
    return SECTION_SPECS[key].skeleton
