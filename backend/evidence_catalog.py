"""The evidence a manuscript can cite: facts recorded in the review (protocol, searches, PRISMA counts, screening,
extraction, risk of bias, final analysis results, GRADE, AI use, and the authors' statements), each under a key such as
``analysis:3``. Sentences link to these keys, and their numbers are checked against the facts."""

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_disclosure import ai_use, disclosure_facts
from appraisal_tools import JUDGMENT_SETS, TOOLS
from certainty_routes import _assessments, effect_summary, sof_row
from claims import EvidenceItem
from prisma_flow import flow_counts
from review_settings import load_settings
from search_quality import press_status
from synthesis_data import extraction_source
from synthesis_routes import ANALYSIS_TYPE_LABELS, final_run

MODEL_LABELS = {
    "random": "random-effects",
    "fixed": "fixed-effect (inverse variance)",
    "mantel_haenszel": "Mantel-Haenszel",
    "peto": "Peto",
    "glmm": "generalized linear mixed",
}
COLUMN_LABELS = {
    "records_identified": "Records identified",
    "duplicates_removed": "Duplicate records removed",
    "records_screened": "Records screened",
    "records_excluded": "Records excluded",
    "excluded_by_automation": "Records not screened after the stopping rule",
    "reports_sought": "Reports sought for retrieval",
    "reports_not_retrieved": "Reports not retrieved",
    "reports_assessed": "Reports assessed for eligibility",
}


def number_text(value: Any, digits: int = 4) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def latest_snapshot(db: Session, project_id: int, stage: str) -> models.StageSnapshot | None:
    return db.scalar(
        select(models.StageSnapshot)
        .where(models.StageSnapshot.project_id == project_id, models.StageSnapshot.stage == stage)
        .order_by(models.StageSnapshot.version.desc())
        .limit(1)
    )


def run_date(run: models.SearchRun) -> str:
    return run.searched_on or run.executed_at.date().isoformat()


def approved_analyses(db: Session, project_id: int) -> list[tuple[models.Analysis, models.AnalysisRun]]:
    analyses = db.scalars(
        select(models.Analysis).where(models.Analysis.project_id == project_id).order_by(models.Analysis.id)
    )
    return [(a, run) for a in analyses if a.status == "approved" and (run := final_run(a)) is not None]


def prisma_totals(flow: dict[str, Any]) -> dict[str, Any]:
    columns = flow["columns"].values()
    totals: dict[str, Any] = {key: sum(int(c.get(key) or 0) for c in columns) for key in COLUMN_LABELS}
    reasons: Counter[str] = Counter()
    for column in columns:
        reasons.update(column.get("reports_excluded") or {})
    totals["reports_excluded"] = dict(reasons)
    totals["reports_excluded_total"] = sum(reasons.values())
    totals["studies_included"] = flow["included"]["studies"]
    totals["reports_included"] = flow["included"]["reports"]
    return totals


def analysis_facts(analysis: models.Analysis, run: models.AnalysisRun) -> list[str]:
    spec = run.spec
    results = run.results or {}
    summary = effect_summary(run)
    facts = [
        f"{analysis.title}: {ANALYSIS_TYPE_LABELS.get(analysis.analysis_type, analysis.analysis_type)} of "
        f"{analysis.outcome}",
        f"Effect measure: {spec.get('measure', '')}",
        f"Model: {MODEL_LABELS.get(spec.get('model', ''), spec.get('model', ''))}, estimator "
        f"{spec.get('tau_method', '')}, "
        f"Hartung-Knapp adjustment {'yes' if spec.get('hksj') else 'no'}",
        f"Studies: {summary['studies']}",
    ]
    if summary.get("participants"):
        facts.append(f"Participants: {summary['participants']}")
    for key, value in summary.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            facts.append(f"Pooled {key}: {number_text(value)}")
    for section in ("summary", "two_stage", "one_stage", "vote_counting", "heterogeneity", "model"):
        values = results.get(section)
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                facts.append(f"{section} {key}: {number_text(value)}")
            elif isinstance(value, list) and value and all(isinstance(v, int | float) for v in value):
                facts.append(f"{section} {key}: {', '.join(number_text(v) for v in value)}")
    bias = results.get("publication_bias") or {}
    for test in ("egger", "begg"):
        if isinstance(bias.get(test), dict) and bias[test].get("p_value") is not None:
            facts.append(f"{test.capitalize()} test p value: {number_text(bias[test]['p_value'])}")
    if isinstance(bias.get("trim_and_fill"), dict) and bias["trim_and_fill"].get("imputed_studies") is not None:
        facts.append(f"Trim and fill imputed studies: {bias['trim_and_fill']['imputed_studies']}")
    subgroup = (results.get("subgroups") or {}).get("test_for_differences") or {}
    if subgroup.get("p_value") is not None:
        facts.append(f"Test for subgroup differences p value: {number_text(subgroup['p_value'])}")
    excluded = run.dataset.get("excluded", [])
    if excluded:
        facts.append(f"Studies without usable data: {len(excluded)}")
    facts.append(f"Software: {run.r_version}; " + ", ".join(f"{k} {v}" for k, v in sorted(run.packages.items())))
    return facts


def build_catalog(
    db: Session, project: models.Project, manuscript: models.Manuscript | None = None
) -> dict[str, EvidenceItem]:
    items: dict[str, EvidenceItem] = {}

    def add(key: str, label: str, facts: list[str], group: str) -> None:
        items[key] = EvidenceItem(key, label, [fact for fact in facts if fact], group)

    project_id = project.id
    protocol = project.protocol
    snapshot = latest_snapshot(db, project_id, "protocol")
    protocol_versions = db.scalars(
        select(models.StageSnapshot.version).where(
            models.StageSnapshot.project_id == project_id, models.StageSnapshot.stage == "protocol"
        )
    ).all()
    criteria = db.scalars(
        select(models.Criterion)
        .where(models.Criterion.project_id == project_id, models.Criterion.status == "accepted")
        .order_by(models.Criterion.id)
    ).all()
    registrations = db.scalars(
        select(models.ProtocolRegistration).where(models.ProtocolRegistration.project_id == project_id)
    ).all()
    plan = (protocol.analysis_plan if protocol else None) or {}
    protocol_facts = [
        f"Review question: {protocol.question}" if protocol else "",
        f"Question framework: {protocol.framework}" if protocol else "",
        f"Protocol version {snapshot.version}, locked on {snapshot.created_at.date().isoformat()}" if snapshot else "",
        f"Protocol amendments: {max(len(protocol_versions) - 1, 0)}",
        f"Inclusion criteria: {sum(1 for c in criteria if c.kind == 'inclusion')}",
        f"Exclusion criteria: {sum(1 for c in criteria if c.kind == 'exclusion')}",
        *[f"{c.kind.capitalize()} criterion: {c.text}" for c in criteria],
        *[f"{o.get('priority', '').capitalize()} outcome: {o.get('name', '')}" for o in plan.get("outcomes", [])],
    ]
    for registration in registrations:
        if registration.status == "waived":
            protocol_facts.append(f"Registration waived: {registration.waiver_reason}")
        else:
            protocol_facts.append(
                f"Registered with {registration.registry_name} as {registration.registration_id} "
                "(protocol version "
                f"{registration.protocol_version}){f', {registration.url}' if registration.url else ''}"
            )
    add("protocol", "Protocol and registration", protocol_facts, "Methods")

    runs = db.scalars(
        select(models.SearchRun).where(models.SearchRun.project_id == project_id).order_by(models.SearchRun.id)
    ).all()
    strategies = db.scalars(select(models.SearchStrategy).where(models.SearchStrategy.project_id == project_id)).all()
    press = press_status(db, project_id)
    search_facts = [
        f"{run.source_label} ({run.interface or run.database}): {run.result_count} records, searched on {run_date(run)}"
        for run in runs
    ]
    search_facts += [f"Search strategies: {len(strategies)}", f"Search runs and imports: {len(runs)}"]
    search_facts += [f"Search string for {s.database} (version {s.version}): {s.query}" for s in strategies]
    search_facts.append(f"PRESS 2015 peer review requirement met: {'yes' if press.get('met') else 'no'}")
    add("search", "Searches", search_facts, "Methods")

    flow = flow_counts(db, project_id)
    totals = prisma_totals(flow)
    prisma_facts = [f"{label}: {totals[key]}" for key, label in COLUMN_LABELS.items()]
    prisma_facts += [
        f"Records identified from {key.replace('_', ' ')}: {value}"
        for key, value in flow["identification"].items()
        if key != "by_source"
    ]
    prisma_facts += [f"Reports excluded, {reason}: {count}" for reason, count in totals["reports_excluded"].items()]
    prisma_facts += [
        f"Reports excluded in total: {totals['reports_excluded_total']}",
        f"Studies included: {totals['studies_included']}",
        f"Reports of included studies: {totals['reports_included']}",
    ]
    add("prisma", "PRISMA flow", prisma_facts, "Results")

    settings = load_settings(db, project_id)
    stopping = db.scalar(
        select(models.StoppingEvaluation)
        .where(models.StoppingEvaluation.project_id == project_id, models.StoppingEvaluation.accepted_at.is_not(None))
        .order_by(models.StoppingEvaluation.id.desc())
        .limit(1)
    )
    samples = db.scalars(select(models.QASample).where(models.QASample.project_id == project_id)).all()
    screening_facts = [
        f"Reviewers per record at title and abstract: {settings.screening.title_abstract_reviewers}",
        f"Reviewers per record at full text: {settings.screening.full_text_reviewers}",
        f"Blinded dual screening: {'yes' if settings.screening.blind_dual_screening else 'no'}",
        f"Recall target: {settings.screening.recall_target}",
    ]
    if stopping:
        screening_facts.append(f"Stopping rule accepted ({stopping.method}) after {stopping.decisions_count} decisions")
        screening_facts += [
            f"Stopping rule {k}: {number_text(v)}" for k, v in stopping.result.items() if isinstance(v, int | float)
        ]
    screening_facts += [f"Quality-assurance sample: {len(s.record_ids)} records" for s in samples]
    add("screening", "Screening", screening_facts, "Methods")

    content, source, _ = extraction_source(db, project_id)
    add(
        "extraction",
        "Data extraction",
        [
            f"Extraction mode: {settings.extraction.mode}",
            f"Included studies: {len(content['studies'])}",
            f"Extraction form fields: {len(content['fields'])}",
            *[f"Field: {f['name']}" for f in content["fields"]],
            f"Data set: {'locked' if source == 'locked' else 'current values (not locked)'}",
        ],
        "Methods",
    )
    for study in content["studies"]:
        facts = [f"Study: {study['label']}", *[f"Arm: {arm['label']}" for arm in study["arms"]]]
        facts += [
            f"{v['field']}{f' [{v["arm"]}]' if v['arm'] else ''}: {v['display']}"
            for v in study["values"]
            if v["display"]
        ]
        add(f"study:{study['study_id']}", study["label"], facts, "Studies")

    assessments = db.scalars(
        select(models.AppraisalAssessment).where(
            models.AppraisalAssessment.project_id == project_id, models.AppraisalAssessment.status == "signed_off"
        )
    ).all()
    by_tool: dict[str, list[models.AppraisalAssessment]] = defaultdict(list)
    for assessment in assessments:
        by_tool[assessment.tool].append(assessment)
    overall_facts = [f"Signed-off assessments: {len(assessments)}"]
    for tool_key, tool_assessments in by_tool.items():
        tool = TOOLS[tool_key]
        labels = dict(JUDGMENT_SETS[tool.overall_judgments])
        counts = Counter(labels.get(a.overall_judgment or "", a.overall_judgment or "") for a in tool_assessments)
        facts = [f"Tool: {tool.label}", f"{tool.label} assessments: {len(tool_assessments)}"]
        facts += [f"{tool.label} overall {label}: {count}" for label, count in counts.items()]
        add(f"appraisal:{tool_key}", tool.label, facts, "Risk of bias")
        overall_facts += facts
    add("appraisal", "Risk of bias", overall_facts, "Risk of bias")

    for analysis, run in approved_analyses(db, project_id):
        add(f"analysis:{analysis.id}", analysis.title, analysis_facts(analysis, run), "Results")

    grades = _assessments(db, project_id)
    certainty_facts = [
        f"GRADE assessments: {len(grades)}",
        *[f"Outcome assessed with GRADE: {g.outcome}" for g in grades],
    ]
    for grade in grades:
        row = sof_row(db, project, grade)
        facts = [
            f"Outcome: {grade.outcome}",
            f"Certainty: {row['certainty_label']}",
            f"Relative effect: {row['relative_effect']}" if row["relative_effect"] else "",
            f"Studies: {row['studies']}" if row["studies"] is not None else "",
            f"Participants: {row['participants']}" if row["participants"] else "",
            f"Informative statement: {row['informative_statement']}",
            *row["footnotes"],
        ]
        for effect in row["absolute_effects"]:
            low, high = effect["difference_per_1000_ci"]
            facts.append(
                f"{effect['label']}: {effect['baseline_per_1000']} per 1000 with the comparator, "
                f"{effect['intervention_per_1000']} per 1000 with the intervention, difference "
                f"{effect['difference_per_1000']} per 1000 ({low} to {high})"
            )
            if effect.get("nnt"):
                facts.append(f"{effect['nnt']['type']} {effect['nnt']['value']} ({effect['nnt']['interval']})")
        add(f"grade:{grade.id}", f"GRADE: {grade.outcome}", facts, "Certainty")
        certainty_facts += [f for f in facts if f]
    texts = db.scalars(
        select(models.InterpretationText).where(
            models.InterpretationText.project_id == project_id, models.InterpretationText.status == "approved"
        )
    ).all()
    certainty_facts += [f"Approved {t.kind.replace('_', ' ')}: {t.content}" for t in texts]
    add("certainty", "Certainty and interpretation", certainty_facts, "Certainty")

    add("ai_use", "Use of AI", disclosure_facts(ai_use(db, project_id)), "Methods")
    if manuscript is not None:
        add(
            "statements",
            "Authors' statements",
            [f"{key.replace('_', ' ').capitalize()}: {value}" for key, value in manuscript.statements.items() if value],
            "Other information",
        )
    return items
