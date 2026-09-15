"""Risk of bias and quality appraisal: tool recommendations, assessments answered question by question with the tool's
algorithm suggesting judgments, domain and overall judgments signed off with rationales, AI suggestions with evidence,
reporting checklists, summaries, and robvis plots.
"""

from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import models
from appraisal_tools import ANSWER_SETS, JUDGMENT_SETS, TOOLS, Tool, catalog, evaluate, recommend_tools
from audit import record_event
from database import get_db
from documents import best_full_texts
from extraction_data import included_studies
from extraction_values import display
from jobs import job_out, start_job
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from reporting_checklists import CHECKLISTS, STATUSES, recommend_checklist
from reporting_checklists import catalog as checklist_catalog
from stats_engine import StatsEngineUnavailable, render_robvis
from workflow import require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["appraisal"])

MAX_AI_ASSESSMENTS = 200


def study_designs(db: Session, project_id: int, study_ids: list[int]) -> dict[int, str]:
    """Each study's final value of the extraction field named "Study design", when the form has one."""
    field = db.scalar(
        select(models.ExtractionField).where(
            models.ExtractionField.project_id == project_id, func.lower(models.ExtractionField.name) == "study design"
        )
    )
    if field is None:
        return {}
    finals = db.scalars(
        select(models.ExtractionFinal).where(
            models.ExtractionFinal.field_id == field.id, models.ExtractionFinal.study_id.in_(study_ids)
        )
    )
    return {final.study_id: display(field, final.value, final.not_reported) for final in finals}


def _tool(key: str) -> Tool:
    tool = TOOLS.get(key)
    if tool is None:
        raise HTTPException(status_code=422, detail=f"Unknown appraisal tool: {key}")
    return tool


def _assessment(db: Session, access: ProjectAccess, assessment_id: int) -> models.AppraisalAssessment:
    return get_in_project(db, models.AppraisalAssessment, assessment_id, access.project.id, "Assessment")


def _latest_suggestions(db: Session, assessment_id: int) -> list[models.AppraisalAISuggestion]:
    latest_run = db.scalar(
        select(func.max(models.AppraisalAISuggestion.ai_run_id)).where(
            models.AppraisalAISuggestion.assessment_id == assessment_id
        )
    )
    if latest_run is None:
        return []
    return list(
        db.scalars(
            select(models.AppraisalAISuggestion).where(
                models.AppraisalAISuggestion.assessment_id == assessment_id,
                models.AppraisalAISuggestion.ai_run_id == latest_run,
            )
        )
    )


def assessment_summary(assessment: models.AppraisalAssessment) -> dict:
    return {
        "id": assessment.id,
        "study_id": assessment.study_id,
        "tool": assessment.tool,
        "outcome": assessment.outcome,
        "status": assessment.status,
        "overall_judgment": assessment.overall_judgment,
        "domains": {d.domain: d.judgment for d in assessment.domains},
    }


def assessment_out(db: Session, assessment: models.AppraisalAssessment) -> dict:
    tool = _tool(assessment.tool)
    answers = {a.question_id: a for a in assessment.answers}
    judgments = {d.domain: d for d in assessment.domains}
    evaluation = evaluate(
        tool, {k: v.answer for k, v in answers.items()}, {k: v.judgment for k, v in judgments.items()}
    )
    study = db.get(models.Study, assessment.study_id)
    documents = best_full_texts(db, [report.record_id for report in study.reports]) if study else {}
    return {
        **assessment_summary(assessment),
        "tool_version": assessment.tool_version,
        "result_description": assessment.result_description,
        "selection_reason": assessment.selection_reason,
        "overall_rationale": assessment.overall_rationale,
        "signed_off_by": assessment.signed_off_by.full_name if assessment.signed_off_by else None,
        "signed_off_at": assessment.signed_off_at,
        "study": {
            "id": study.id,
            "label": study.label,
            "documents": [
                {"record_id": record_id, "document_id": doc.id, "file_name": doc.file_name}
                for record_id, doc in documents.items()
            ],
        }
        if study
        else None,
        "answers": {
            question_id: {"answer": a.answer, "note": a.note, "span_ids": a.span_ids, "source": a.source}
            for question_id, a in answers.items()
        },
        "domain_judgments": {
            key: {
                "judgment": d.judgment,
                "rationale": d.rationale,
                "algorithm_judgment": d.algorithm_judgment,
                "signed_off_by": d.signed_off_by.full_name if d.signed_off_by else None,
                "signed_off_at": d.signed_off_at,
            }
            for key, d in judgments.items()
        },
        "suggested": {"domains": evaluation.domain_suggestions, "overall": evaluation.overall_suggestion},
        "applicable_questions": evaluation.applicable,
        "unanswered": evaluation.unanswered,
        "ai_suggestions": [
            {
                "question_id": s.question_id,
                "domain": s.domain,
                "answer": s.answer,
                "rationale": s.rationale,
                "quote": s.quote,
                "span_ids": s.span_ids,
                "grounded": s.grounded,
            }
            for s in _latest_suggestions(db, assessment.id)
        ],
    }


def _audit(db: Session, access: ProjectAccess, action: str, entity_type: str, entity_id: int, details: dict) -> None:
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )


@router.get("/appraisal/tools")
def appraisal_tools(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    return {"tools": catalog(), "checklists": checklist_catalog()}


@router.get("/appraisal/overview")
def appraisal_overview(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """Included studies with their design, recommended tools and checklist, and assessments."""
    protocol = access.project.protocol
    review_type, framework = (protocol.review_type, protocol.framework) if protocol else ("", "")
    studies = included_studies(db, access.project.id)
    designs = study_designs(db, access.project.id, [s.id for s in studies])
    assessments: dict[int, list[models.AppraisalAssessment]] = defaultdict(list)
    for assessment in db.scalars(
        select(models.AppraisalAssessment)
        .where(models.AppraisalAssessment.project_id == access.project.id)
        .options(selectinload(models.AppraisalAssessment.domains))
        .order_by(models.AppraisalAssessment.id)
    ):
        assessments[assessment.study_id].append(assessment)
    reporting: dict[int, list[models.ReportingAssessment]] = defaultdict(list)
    for item in db.scalars(
        select(models.ReportingAssessment).where(models.ReportingAssessment.project_id == access.project.id)
    ):
        reporting[item.study_id].append(item)
    return [
        {
            "study_id": study.id,
            "label": study.label,
            "design": designs.get(study.id, ""),
            "recommended_tools": [
                {"tool": r.tool, "reason": r.reason}
                for r in recommend_tools(designs.get(study.id, ""), review_type, framework)
            ],
            "recommended_checklist": recommend_checklist(designs.get(study.id, ""), review_type),
            "assessments": [assessment_summary(a) for a in assessments[study.id]],
            "reporting": [{"id": r.id, "checklist": r.checklist, "status": r.status} for r in reporting[study.id]],
        }
        for study in studies
    ]


class AssessmentCreate(BaseModel):
    study_id: int
    tool: str = Field(max_length=40)
    outcome: str = Field("", max_length=300)
    result_description: str = Field("", max_length=5000)
    selection_reason: str = Field("", max_length=2000)


@router.post("/appraisal/assessments", status_code=201)
def create_assessment(
    body: AssessmentCreate,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "appraisal")
    tool = _tool(body.tool)
    if body.study_id not in {s.id for s in included_studies(db, access.project.id)}:
        raise HTTPException(status_code=404, detail="Only studies included in the review are appraised")
    if tool.per_outcome and not body.outcome.strip():
        raise HTTPException(status_code=422, detail=f"{tool.label} is assessed per result: name the outcome")
    exists = db.scalar(
        select(models.AppraisalAssessment.id).where(
            models.AppraisalAssessment.study_id == body.study_id,
            models.AppraisalAssessment.tool == tool.key,
            models.AppraisalAssessment.outcome == body.outcome.strip(),
        )
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail="This study already has that assessment")
    protocol = access.project.protocol
    designs = study_designs(db, access.project.id, [body.study_id])
    recommended = recommend_tools(
        designs.get(body.study_id, ""), protocol.review_type if protocol else "", protocol.framework if protocol else ""
    )
    reason = body.selection_reason.strip() or next((r.reason for r in recommended if r.tool == tool.key), "")
    if not reason:
        raise HTTPException(
            status_code=422,
            detail="This tool isn't the one recommended for the study's design: explain why it was chosen",
        )
    assessment = models.AppraisalAssessment(
        project_id=access.project.id,
        study_id=body.study_id,
        tool=tool.key,
        tool_version=tool.version[:120],
        outcome=body.outcome.strip(),
        result_description=body.result_description.strip(),
        selection_reason=reason,
        status="in_progress",
        created_by_id=access.user.id,
    )
    db.add(assessment)
    db.flush()
    _audit(
        db,
        access,
        "appraisal.assessment_created",
        "appraisal_assessment",
        assessment.id,
        {"study_id": body.study_id, "tool": tool.key, "outcome": assessment.outcome, "selection_reason": reason},
    )
    db.commit()
    return assessment_out(db, assessment)


@router.get("/appraisal/assessments/{assessment_id}")
def get_assessment(
    assessment_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    return assessment_out(db, _assessment(db, access, assessment_id))


class AnswerIn(BaseModel):
    question_id: str = Field(max_length=20)
    # An empty answer clears it.
    answer: str = Field("", max_length=30)
    note: str = Field("", max_length=5000)
    span_ids: list[int] = Field(default_factory=list, max_length=50)
    source: Literal["manual", "ai_accepted"] = "manual"


class AnswersUpdate(BaseModel):
    answers: list[AnswerIn] = Field(min_length=1, max_length=200)


def _reopen(assessment: models.AppraisalAssessment) -> None:
    assessment.status = "in_progress"
    assessment.overall_judgment, assessment.overall_rationale = None, ""
    assessment.signed_off_by_id, assessment.signed_off_at = None, None


@router.put("/appraisal/assessments/{assessment_id}/answers")
def save_answers(
    assessment_id: int,
    body: AnswersUpdate,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    """Save answers to signalling questions. Changing an answer withdraws the sign-off of its domain and the overall."""
    require_stage_open(db, access.project.id, "appraisal")
    assessment = _assessment(db, access, assessment_id)
    tool = _tool(assessment.tool)
    domain_of = {q.id: d.key for d in tool.domains for q in d.questions}
    existing = {a.question_id: a for a in assessment.answers}
    changed_domains: set[str] = set()
    for item in body.answers:
        question = tool.question(item.question_id)
        if question is None:
            raise HTTPException(status_code=422, detail=f"{tool.label} has no question {item.question_id}")
        current = existing.get(item.question_id)
        if not item.answer:
            if current is not None:
                assessment.answers.remove(current)
                changed_domains.add(domain_of[item.question_id])
            continue
        if item.answer not in dict(ANSWER_SETS[question.answers]):
            raise HTTPException(status_code=422, detail=f"{item.answer} isn't an answer to question {item.question_id}")
        if current is None:
            current = models.AppraisalAnswer(question_id=item.question_id, answer=item.answer)
            assessment.answers.append(current)
            changed_domains.add(domain_of[item.question_id])
        elif current.answer != item.answer:
            changed_domains.add(domain_of[item.question_id])
        current.answer, current.note, current.span_ids, current.source = (
            item.answer,
            item.note,
            item.span_ids,
            item.source,
        )
        current.answered_by_id = access.user.id
    withdrawn = [d.domain for d in assessment.domains if d.domain in changed_domains]
    assessment.domains = [d for d in assessment.domains if d.domain not in changed_domains]
    if changed_domains and assessment.status == "signed_off":
        _reopen(assessment)
    db.flush()
    _audit(
        db,
        access,
        "appraisal.answers_saved",
        "appraisal_assessment",
        assessment.id,
        {"answers": {a.question_id: a.answer for a in body.answers}, "sign_offs_withdrawn": withdrawn},
    )
    db.commit()
    db.refresh(assessment)
    return assessment_out(db, assessment)


class JudgmentIn(BaseModel):
    judgment: str = Field(max_length=30)
    rationale: str = Field(min_length=10, max_length=5000)


@router.put("/appraisal/assessments/{assessment_id}/domains/{domain_key}")
def sign_off_domain(
    assessment_id: int,
    domain_key: str,
    body: JudgmentIn,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "appraisal")
    assessment = _assessment(db, access, assessment_id)
    tool = _tool(assessment.tool)
    domain = tool.domain(domain_key)
    if domain is None:
        raise HTTPException(status_code=404, detail=f"{tool.label} has no domain {domain_key}")
    if body.judgment not in dict(JUDGMENT_SETS[domain.judgments]):
        raise HTTPException(status_code=422, detail=f"{body.judgment} isn't a judgment for {domain.label}")
    answers = {a.question_id: a.answer for a in assessment.answers}
    unanswered = [q.id for q in domain.applicable_questions(answers) if not answers.get(q.id)]
    if unanswered:
        raise HTTPException(
            status_code=409, detail=f"Answer questions {', '.join(unanswered)} before judging {domain.label}"
        )
    suggestion = domain.algorithm(answers) if domain.algorithm else None
    judgment = next((d for d in assessment.domains if d.domain == domain_key), None)
    if judgment is None:
        judgment = models.AppraisalDomainJudgment(domain=domain_key, judgment=body.judgment, rationale=body.rationale)
        assessment.domains.append(judgment)
    judgment.judgment, judgment.rationale, judgment.algorithm_judgment = (
        body.judgment,
        body.rationale.strip(),
        suggestion,
    )
    judgment.signed_off_by_id, judgment.signed_off_at = access.user.id, models.utcnow()
    if assessment.status == "signed_off":
        _reopen(assessment)
    db.flush()
    _audit(
        db,
        access,
        "appraisal.domain_signed_off",
        "appraisal_assessment",
        assessment.id,
        {
            "domain": domain_key,
            "judgment": body.judgment,
            "algorithm_judgment": suggestion,
            "rationale": judgment.rationale,
        },
    )
    db.commit()
    db.refresh(assessment)
    return assessment_out(db, assessment)


@router.put("/appraisal/assessments/{assessment_id}/overall")
def sign_off_overall(
    assessment_id: int,
    body: JudgmentIn,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    """Sign off the overall judgment, once every domain the tool needs is signed off."""
    require_stage_open(db, access.project.id, "appraisal")
    assessment = _assessment(db, access, assessment_id)
    tool = _tool(assessment.tool)
    if body.judgment not in dict(JUDGMENT_SETS[tool.overall_judgments]):
        raise HTTPException(status_code=422, detail=f"{body.judgment} isn't an overall judgment for {tool.label}")
    signed = {d.domain for d in assessment.domains}
    if tool.key == "mmat":
        missing = (
            []
            if "screening" in signed and any(k.startswith("category_") for k in signed)
            else ["screening and the study's category"]
        )
    else:
        missing = [d.label for d in tool.domains if d.kind != "screening" and d.key not in signed]
    if missing:
        raise HTTPException(status_code=409, detail=f"Sign off these domains first: {'; '.join(missing)}")
    answers = {a.question_id: a.answer for a in assessment.answers}
    suggestion = evaluate(tool, answers, {d.domain: d.judgment for d in assessment.domains}).overall_suggestion
    assessment.overall_judgment, assessment.overall_rationale = body.judgment, body.rationale.strip()
    assessment.status = "signed_off"
    assessment.signed_off_by_id, assessment.signed_off_at = access.user.id, models.utcnow()
    _audit(
        db,
        access,
        "appraisal.signed_off",
        "appraisal_assessment",
        assessment.id,
        {"overall": body.judgment, "algorithm_overall": suggestion, "rationale": assessment.overall_rationale},
    )
    db.commit()
    db.refresh(assessment)
    return assessment_out(db, assessment)


@router.delete("/appraisal/assessments/{assessment_id}", status_code=204)
def delete_assessment(
    assessment_id: int,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "appraisal")
    assessment = _assessment(db, access, assessment_id)
    if assessment.status == "signed_off":
        raise HTTPException(
            status_code=409, detail="A signed-off assessment can't be deleted; change an answer to reopen it"
        )
    _audit(
        db,
        access,
        "appraisal.assessment_deleted",
        "appraisal_assessment",
        assessment.id,
        {"tool": assessment.tool, "study_id": assessment.study_id},
    )
    db.delete(assessment)
    db.commit()
    return Response(status_code=204)


class AIRequest(BaseModel):
    assessment_ids: list[int] = Field(min_length=1, max_length=MAX_AI_ASSESSMENTS)


def _checked_ids(
    db: Session,
    model: type[models.AppraisalAssessment] | type[models.ReportingAssessment],
    project_id: int,
    ids: list[int],
) -> list[int]:
    unique = list(dict.fromkeys(ids))
    found = set(db.scalars(select(model.id).where(model.project_id == project_id, model.id.in_(unique))))
    if len(found) != len(unique):
        raise HTTPException(status_code=404, detail="Some assessments weren't found in this project")
    return unique


@router.post("/appraisal/ai", status_code=202, dependencies=[Depends(ai_rate_limit)])
async def suggest_appraisals(
    body: AIRequest,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    """Queue AI answers to each assessment's signalling questions, read from the study's full texts."""
    require_stage_open(db, access.project.id, "appraisal")
    ids = _checked_ids(db, models.AppraisalAssessment, access.project.id, body.assessment_ids)
    return job_out(await start_job(db, access, "appraisal", ids))


@router.get("/appraisal/summary")
def appraisal_summary(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """Every assessment's domain and overall judgments, grouped by tool, for traffic-light and summary displays."""
    labels = {s.id: s.label for s in included_studies(db, access.project.id)}
    tools: dict[str, dict] = {}
    for assessment in db.scalars(
        select(models.AppraisalAssessment)
        .where(models.AppraisalAssessment.project_id == access.project.id)
        .options(selectinload(models.AppraisalAssessment.domains))
        .order_by(models.AppraisalAssessment.tool, models.AppraisalAssessment.id)
    ):
        tool = _tool(assessment.tool)
        entry = tools.setdefault(
            tool.key,
            {
                "tool": tool.key,
                "label": tool.label,
                "domains": [
                    {"key": d.key, "label": d.label, "kind": d.kind} for d in tool.domains if d.kind != "screening"
                ],
                "judgments": {
                    k: v
                    for set_key in {tool.overall_judgments, *(d.judgments for d in tool.domains)}
                    for k, v in JUDGMENT_SETS[set_key]
                },
                "rows": [],
            },
        )
        entry["rows"].append(
            {
                "assessment_id": assessment.id,
                "study_id": assessment.study_id,
                "study": labels.get(assessment.study_id, f"Study {assessment.study_id}"),
                "outcome": assessment.outcome,
                "status": assessment.status,
                "domains": {d.domain: d.judgment for d in assessment.domains},
                "overall": assessment.overall_judgment,
            }
        )
    return list(tools.values())


@router.get("/appraisal/plots/{tool_key}")
async def appraisal_plot(
    tool_key: str,
    kind: Literal["traffic_light", "summary"] = "traffic_light",
    format: Literal["svg", "png", "pdf"] = "svg",
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    """Publication-ready risk of bias plots drawn with robvis in R, from signed-off assessments."""
    tool = _tool(tool_key)
    summary = next((entry for entry in appraisal_summary(access, db) if entry["tool"] == tool.key), None)
    rows = [row for row in (summary or {}).get("rows", []) if row["status"] == "signed_off"]
    if not rows:
        raise HTTPException(status_code=404, detail=f"No signed-off {tool.label} assessments to plot")
    try:
        content, media_type = await render_robvis(tool, summary["domains"] if summary else [], rows, kind, format)
    except StatsEngineUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{tool.key}-{kind}.{format}"'},
    )


# --- Reporting checklists ---


class ReportingCreate(BaseModel):
    study_id: int
    checklist: str = Field(max_length=30)


class ItemIn(BaseModel):
    item_id: str = Field(max_length=10)
    status: str = Field("", max_length=20)
    location: str = Field("", max_length=2000)
    note: str = Field("", max_length=5000)
    span_ids: list[int] = Field(default_factory=list, max_length=50)


class ItemsUpdate(BaseModel):
    items: list[ItemIn] = Field(min_length=1, max_length=100)


def reporting_out(assessment: models.ReportingAssessment) -> dict:
    checklist = CHECKLISTS[assessment.checklist]
    items = {item.item_id: item for item in assessment.items}
    return {
        "id": assessment.id,
        "study_id": assessment.study_id,
        "checklist": assessment.checklist,
        "status": assessment.status,
        "signed_off_at": assessment.signed_off_at,
        "items": [
            {
                "item_id": item_id,
                "section": section,
                "topic": topic,
                "status": items[item_id].status if item_id in items else "",
                "location": items[item_id].location if item_id in items else "",
                "note": items[item_id].note if item_id in items else "",
                "span_ids": items[item_id].span_ids if item_id in items else [],
                "ai_status": items[item_id].ai_status if item_id in items else "",
                "ai_rationale": items[item_id].ai_rationale if item_id in items else "",
                "ai_quote": items[item_id].ai_quote if item_id in items else None,
                "ai_grounded": items[item_id].ai_grounded if item_id in items else None,
            }
            for item_id, section, topic in checklist.items
        ],
    }


@router.post("/reporting/assessments", status_code=201)
def create_reporting(
    body: ReportingCreate,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "appraisal")
    if body.checklist not in CHECKLISTS:
        raise HTTPException(status_code=422, detail=f"Unknown checklist: {body.checklist}")
    if body.study_id not in {s.id for s in included_studies(db, access.project.id)}:
        raise HTTPException(status_code=404, detail="Only studies included in the review are appraised")
    exists = db.scalar(
        select(models.ReportingAssessment.id).where(
            models.ReportingAssessment.study_id == body.study_id, models.ReportingAssessment.checklist == body.checklist
        )
    )
    if exists is not None:
        raise HTTPException(status_code=409, detail="This study already has that checklist")
    assessment = models.ReportingAssessment(
        project_id=access.project.id,
        study_id=body.study_id,
        checklist=body.checklist,
        status="in_progress",
        created_by_id=access.user.id,
    )
    db.add(assessment)
    db.flush()
    _audit(
        db,
        access,
        "reporting.assessment_created",
        "reporting_assessment",
        assessment.id,
        {"study_id": body.study_id, "checklist": body.checklist},
    )
    db.commit()
    db.refresh(assessment)
    return reporting_out(assessment)


@router.get("/reporting/assessments/{assessment_id}")
def get_reporting(
    assessment_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    return reporting_out(get_in_project(db, models.ReportingAssessment, assessment_id, access.project.id, "Checklist"))


@router.put("/reporting/assessments/{assessment_id}/items")
def save_reporting_items(
    assessment_id: int,
    body: ItemsUpdate,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "appraisal")
    assessment = get_in_project(db, models.ReportingAssessment, assessment_id, access.project.id, "Checklist")
    valid_items = {item_id for item_id, _, _ in CHECKLISTS[assessment.checklist].items}
    existing = {item.item_id: item for item in assessment.items}
    for entry in body.items:
        if entry.item_id not in valid_items:
            raise HTTPException(status_code=422, detail=f"{assessment.checklist} has no item {entry.item_id}")
        if entry.status and entry.status not in STATUSES:
            raise HTTPException(status_code=422, detail=f"Unknown status: {entry.status}")
        item = existing.get(entry.item_id)
        if item is None:
            item = models.ReportingItem(item_id=entry.item_id)
            assessment.items.append(item)
        item.status, item.location, item.note, item.span_ids = entry.status, entry.location, entry.note, entry.span_ids
        item.updated_by_id = access.user.id
    assessment.status = "in_progress"
    assessment.signed_off_by_id, assessment.signed_off_at = None, None
    _audit(
        db,
        access,
        "reporting.items_saved",
        "reporting_assessment",
        assessment.id,
        {"items": {e.item_id: e.status for e in body.items}},
    )
    db.commit()
    db.refresh(assessment)
    return reporting_out(assessment)


@router.post("/reporting/assessments/{assessment_id}/sign-off")
def sign_off_reporting(
    assessment_id: int,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "appraisal")
    assessment = get_in_project(db, models.ReportingAssessment, assessment_id, access.project.id, "Checklist")
    assessed = {item.item_id for item in assessment.items if item.status}
    missing = [item_id for item_id, _, _ in CHECKLISTS[assessment.checklist].items if item_id not in assessed]
    if missing:
        raise HTTPException(status_code=409, detail=f"Assess every item first; missing: {', '.join(missing[:15])}")
    assessment.status = "signed_off"
    assessment.signed_off_by_id, assessment.signed_off_at = access.user.id, models.utcnow()
    _audit(
        db, access, "reporting.signed_off", "reporting_assessment", assessment.id, {"checklist": assessment.checklist}
    )
    db.commit()
    db.refresh(assessment)
    return reporting_out(assessment)


@router.post("/reporting/ai", status_code=202, dependencies=[Depends(ai_rate_limit)])
async def suggest_reporting_items(
    body: AIRequest,
    access: ProjectAccess = Depends(project_access(Permission.APPRAISE)),
    db: Session = Depends(get_db),
):
    """Queue AI flags for how completely each study reports the checklist's items."""
    require_stage_open(db, access.project.id, "appraisal")
    ids = _checked_ids(db, models.ReportingAssessment, access.project.id, body.assessment_ids)
    return job_out(await start_job(db, access, "reporting", ids))
