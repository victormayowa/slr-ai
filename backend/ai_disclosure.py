"""The AI-use disclosure, generated from what was recorded: every AI run (task, provider, model, prompt version) and the
human decisions and sign-offs around them. It follows the reporting expectations of PRISMA-trAIce and RAISE (which
tools, for what, with what human oversight and validation); check the published checklist wording before submission.
"""

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import models
from review_settings import load_settings

TASK_LABELS = {
    "screening": "title and abstract screening suggestions",
    "fulltext_screening": "full-text screening suggestions",
    "extraction": "data extraction suggestions",
    "appraisal": "risk of bias answer suggestions",
    "reporting": "reporting checklist suggestions",
    "entities": "entity recognition",
    "protocol": "protocol criteria and search string drafts",
    "question": "review question suggestions",
    "section_draft": "protocol section drafts",
    "consistency": "protocol consistency checks",
    "topic_questions": "topic question suggestions",
    "synthesis": "a narrative summary of extracted data",
    "plain_language": "a plain-language summary",
    "manuscript": "manuscript drafting and language editing",
    "embedding": "record similarity for duplicate detection",
    "surveillance": "screening suggestions for surveillance records",
    "publication": "journal guideline reading, cover letter, and response letter drafts",
}


def ai_use(db: Session, project_id: int) -> dict[str, Any]:
    rows = db.execute(
        select(
            models.AIRun.task,
            models.AIRun.provider,
            models.AIRun.model,
            models.AIRun.prompt_version,
            models.AIRun.status,
            func.count(),
            func.min(models.AIRun.created_at),
            func.max(models.AIRun.created_at),
        )
        .where(models.AIRun.project_id == project_id)
        .group_by(
            models.AIRun.task,
            models.AIRun.provider,
            models.AIRun.model,
            models.AIRun.prompt_version,
            models.AIRun.status,
        )
    ).all()
    tasks: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "succeeded": 0, "failed": 0, "models": set(), "prompts": set(), "first": None, "last": None}
    )
    for task, provider, model, prompt, status, count, first, last in rows:
        entry = tasks[task]
        entry["runs"] += count
        entry["succeeded" if status == "succeeded" else "failed"] += count
        entry["models"].add(f"{provider} {model}")
        entry["prompts"].add(prompt)
        entry["first"] = min(filter(None, [entry["first"], first]))
        entry["last"] = max(filter(None, [entry["last"], last]))
    task_rows = [
        {
            "task": task,
            "label": TASK_LABELS.get(task, task.replace("_", " ")),
            "runs": entry["runs"],
            "succeeded": entry["succeeded"],
            "failed": entry["failed"],
            "models": sorted(entry["models"]),
            "prompt_versions": sorted(entry["prompts"]),
            "first": entry["first"].date().isoformat() if entry["first"] else "",
            "last": entry["last"].date().isoformat() if entry["last"] else "",
        }
        for task, entry in sorted(tasks.items())
    ]

    settings = load_settings(db, project_id)
    human_decisions = (
        db.scalar(
            select(func.count())
            .select_from(models.ScreeningDecision)
            .join(models.Record, models.Record.id == models.ScreeningDecision.record_id)
            .where(models.Record.project_id == project_id)
        )
        or 0
    )
    values = db.execute(
        select(models.ExtractionValue.source, func.count())
        .join(models.Study, models.Study.id == models.ExtractionValue.study_id)
        .where(models.Study.project_id == project_id)
        .group_by(models.ExtractionValue.source)
    ).all()
    value_counts = {source: count for source, count in values}
    answers = db.execute(
        select(models.AppraisalAnswer.source, func.count())
        .join(models.AppraisalAssessment, models.AppraisalAssessment.id == models.AppraisalAnswer.assessment_id)
        .where(models.AppraisalAssessment.project_id == project_id)
        .group_by(models.AppraisalAnswer.source)
    ).all()
    answer_counts = {source: count for source, count in answers}
    sign_offs = (
        db.scalar(
            select(func.count()).select_from(models.StageSnapshot).where(models.StageSnapshot.project_id == project_id)
        )
        or 0
    )
    qa_samples = db.scalars(select(models.QASample).where(models.QASample.project_id == project_id)).all()
    stopping = db.scalar(
        select(models.StoppingEvaluation)
        .where(models.StoppingEvaluation.project_id == project_id, models.StoppingEvaluation.accepted_at.is_not(None))
        .order_by(models.StoppingEvaluation.id.desc())
        .limit(1)
    )
    oversight = {
        "screening_reviewers": {
            "title_abstract": settings.screening.title_abstract_reviewers,
            "full_text": settings.screening.full_text_reviewers,
            "blinded": settings.screening.blind_dual_screening,
        },
        "human_screening_decisions": human_decisions,
        "extraction_mode": settings.extraction.mode,
        "extraction_values": sum(value_counts.values()),
        "extraction_values_accepted_from_ai": value_counts.get("ai_accepted", 0),
        "appraisal_answers": sum(answer_counts.values()),
        "appraisal_answers_accepted_from_ai": answer_counts.get("ai_accepted", 0),
        "stage_sign_offs": sign_offs,
        "qa_samples": [len(sample.record_ids) for sample in qa_samples],
        "stopping_rule": stopping.result if stopping else None,
    }
    return {"tasks": task_rows, "oversight": oversight}


def disclosure_facts(usage: dict[str, Any]) -> list[str]:
    facts = [
        f"{t['label']}: {t['runs']} AI runs ({t['succeeded']} succeeded, {t['failed']} failed) with "
        f"{', '.join(t['models'])}, prompt versions {', '.join(t['prompt_versions'])}, from {t['first']} to {t['last']}"
        for t in usage["tasks"]
    ]
    o = usage["oversight"]
    facts += [
        f"Reviewers per record: {o['screening_reviewers']['title_abstract']} at title and abstract, "
        f"{o['screening_reviewers']['full_text']} at full text",
        f"Human screening decisions recorded: {o['human_screening_decisions']}",
        f"Extraction values: {o['extraction_values']}, of which {o['extraction_values_accepted_from_ai']} were "
        "accepted "
        "from AI suggestions after review",
        f"Risk of bias answers: {o['appraisal_answers']}, of which {o['appraisal_answers_accepted_from_ai']} were "
        "accepted from AI suggestions after review",
        f"Workflow stage sign-offs by reviewers: {o['stage_sign_offs']}",
    ]
    if o["qa_samples"]:
        facts.append(
            f"Quality-assurance samples screened by humans: {', '.join(str(n) for n in o['qa_samples'])} records"
        )
    if o["stopping_rule"]:
        facts.append(f"Accepted stopping rule result: {o['stopping_rule']}")
    return facts


def disclosure_text(usage: dict[str, Any]) -> str:
    """Disclosure paragraphs with evidence markers, ready to verify."""
    if not usage["tasks"]:
        return "No artificial intelligence tools were used in this review. [#ai_use]\n"
    uses = "; ".join(
        f"{t['label']} ({', '.join(t['models'])}; {t['runs']} runs)"
        for t in usage["tasks"]
        if t["task"] != "manuscript"
    )
    o = usage["oversight"]
    lines = [
        f"Artificial intelligence tools were used for {uses or 'no review tasks'}. [#ai_use]",
        "Every AI output was stored as a suggestion together with its provider, model version, prompt version, and the "
        "person who requested it, and quotes offered as evidence were checked against the source text. [#ai_use]",
        "Reviewers made every screening decision, and extracted values, risk of bias judgments, statistical model "
        "choices, and certainty ratings were entered or approved by reviewers before each stage was signed off. "
        "[#ai_use]",
        f"Of {o['extraction_values']} extracted values, {o['extraction_values_accepted_from_ai']} were accepted from "
        f"AI suggestions after review. [#ai_use]",
    ]
    if any(t["task"] == "manuscript" for t in usage["tasks"]):
        lines.append(
            "AI drafts and language edits of the manuscript were accepted section by section by the authors, and "
            "every number was checked against the recorded results. [#ai_use]"
        )
    return " ".join(lines) + "\n"
