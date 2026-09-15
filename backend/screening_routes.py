"""Screening: reviewers' decisions at title and abstract and at full text, adjudication of disagreements, agreement
statistics, prioritized screening, stopping rules, quality-assurance sampling, AI suggestion accuracy, review settings,
and the AI narrative synthesis.

Batch AI suggestions run as background jobs; see jobs_routes.py. AI output is stored as suggestions with provenance;
only reviewers' decisions include or exclude a record.
"""

import asyncio
import logging
import random
from collections import defaultdict
from itertools import combinations
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import models
from active_learning import ALGORITHM, NotEnoughDecisions, train
from agreement import cohens_kappa, wilson_interval
from ai_access import new_ai_run, project_ai, record_usage
from audit import record_event
from database import get_db
from extraction_data import dataset_content, included_records
from llm.prompts import SYNTHESIS_PROMPT
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from records_routes import record_brief, record_out, with_record_details
from review_data import FINAL_DECISIONS, FULL_TEXT, TITLE_ABSTRACT, WORKFLOW_STAGE, ReviewPolicy, latest_run
from review_settings import ReviewSettingsData, exclusion_reasons, load_settings
from services.ai_screening import generate_narrative_synthesis
from services.errors import LLMError
from stopping import hypergeometric_test
from workflow import WorkflowError, require_stage_open

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["screening"])

Stage = Literal["title_abstract", "full_text"]


class DecisionRequest(BaseModel):
    decision: Literal["include", "exclude", "undecided", "not_retrieved"]
    stage: Stage = "title_abstract"
    reason_code: str | None = Field(None, max_length=60)
    note: str | None = Field(None, max_length=5000)


class AdjudicationRequest(BaseModel):
    stage: Stage = "title_abstract"
    decision: Literal["include", "exclude", "not_retrieved"]
    reason_code: str | None = Field(None, max_length=60)
    rationale: str = Field(min_length=10, max_length=5000)


class StoppingRequest(BaseModel):
    recall_target: float | None = Field(None, ge=0.5, le=0.999)
    alpha: float | None = Field(None, gt=0, lt=0.5)


class AcceptStoppingRequest(BaseModel):
    rationale: str = Field(min_length=10, max_length=5000)


class QASampleRequest(BaseModel):
    size: int = Field(ge=1, le=5000)


def _settings(db: Session, project_id: int) -> tuple[ReviewSettingsData, ReviewPolicy]:
    settings = load_settings(db, project_id)
    return settings, ReviewPolicy(settings.screening.title_abstract_reviewers, settings.screening.full_text_reviewers)


def _unique_records(db: Session, project_id: int) -> list[models.Record]:
    return list(
        db.scalars(
            with_record_details(
                select(models.Record)
                .where(models.Record.project_id == project_id, models.Record.duplicate_of_id.is_(None))
                .order_by(models.Record.id)
            )
        )
    )


def _check_reason(
    stage: str, decision: str, reason_code: str | None, note: str | None, settings: ReviewSettingsData
) -> str | None:
    if decision not in FINAL_DECISIONS[stage] and decision != "undecided":
        raise HTTPException(status_code=422, detail=f"{decision} isn't a decision at this stage")
    if stage != FULL_TEXT or decision != "exclude":
        return None
    reasons = exclusion_reasons(settings)
    if reason_code not in reasons:
        raise HTTPException(status_code=422, detail="Choose why the report is excluded")
    if reason_code == "other" and not (note or "").strip():
        raise HTTPException(status_code=422, detail="Explain the exclusion in the note when the reason is Other")
    return reason_code


@router.put("/records/{record_id}/decision")
def set_screening_decision(
    record_id: int,
    body: DecisionRequest,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, WORKFLOW_STAGE[body.stage])
    record = get_in_project(db, models.Record, record_id, access.project.id, "Record")
    if record.duplicate_of_id is not None:
        raise HTTPException(status_code=409, detail="This record is marked as a duplicate")
    settings, policy = _settings(db, access.project.id)
    reason = _check_reason(body.stage, body.decision, body.reason_code, body.note, settings)
    if body.stage == FULL_TEXT:
        if not policy.sought(record):
            raise HTTPException(
                status_code=409, detail="Only records included at title and abstract are screened at full text"
            )
        if body.decision == "not_retrieved":
            has_full_text = db.scalar(
                select(models.Document.id)
                .where(models.Document.record_id == record.id, models.Document.role == "full_text")
                .limit(1)
            )
            if has_full_text is not None:
                raise HTTPException(status_code=409, detail="This report has a stored full text; assess it instead")

    existing = next((d for d in record.decisions if d.stage == body.stage and d.reviewer_id == access.user.id), None)
    previous = existing.decision if existing else None
    if existing is not None:
        # Replace rather than update, so the newest decision always has the highest id.
        record.decisions.remove(existing)
        db.flush()
    record.decisions.append(
        models.ScreeningDecision(
            stage=body.stage,
            reviewer_id=access.user.id,
            decision=body.decision,
            reason_code=reason,
            note=(body.note or "").strip() or None,
        )
    )
    db.flush()
    details = {"stage": body.stage, "decision": body.decision, "previous": previous}
    if reason:
        details["reason_code"] = reason
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="screening.decided",
        entity_type="record",
        entity_id=record.id,
        details=details,
    )
    db.commit()
    db.refresh(record)
    return record_out(record, access.user.id, policy, settings.screening.blind_dual_screening)


@router.put("/records/{record_id}/adjudication")
def adjudicate(
    record_id: int,
    body: AdjudicationRequest,
    access: ProjectAccess = Depends(project_access(Permission.ADJUDICATE)),
    db: Session = Depends(get_db),
):
    """Settle a disagreement between reviewers with a final decision and a rationale."""
    require_stage_open(db, access.project.id, WORKFLOW_STAGE[body.stage])
    record = get_in_project(db, models.Record, record_id, access.project.id, "Record")
    settings, policy = _settings(db, access.project.id)
    status = policy.status(record, body.stage)
    if status.state not in ("conflict", "adjudicated"):
        raise HTTPException(status_code=409, detail="Only records whose reviewers disagree are adjudicated")
    reason = _check_reason(body.stage, body.decision, body.reason_code, body.rationale, settings)
    for existing in [a for a in record.adjudications if a.stage == body.stage]:
        record.adjudications.remove(existing)
    db.flush()
    record.adjudications.append(
        models.ScreeningAdjudication(
            project_id=access.project.id,
            stage=body.stage,
            decision=body.decision,
            reason_code=reason,
            rationale=body.rationale.strip(),
            adjudicator_id=access.user.id,
        )
    )
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="screening.adjudicated",
        entity_type="record",
        entity_id=record.id,
        details={"stage": body.stage, "decision": body.decision, "reason_code": reason, "rationale": body.rationale},
    )
    db.commit()
    db.refresh(record)
    return record_out(record, access.user.id, policy, settings.screening.blind_dual_screening)


@router.get("/screening/conflicts")
def list_conflicts(
    stage: Stage = "title_abstract",
    access: ProjectAccess = Depends(project_access(Permission.ADJUDICATE)),
    db: Session = Depends(get_db),
):
    """Records whose reviewers disagree (and those already adjudicated), with every reviewer's decision."""
    settings, policy = _settings(db, access.project.id)
    reasons = exclusion_reasons(settings)
    items = []
    for record in _unique_records(db, access.project.id):
        status = policy.status(record, stage)
        if status.state not in ("conflict", "adjudicated"):
            continue
        adjudication = next((a for a in record.adjudications if a.stage == stage), None)
        items.append(
            {
                "record": {**record_brief(record), "abstract": record.abstract},
                "state": status.state,
                "decisions": [
                    {
                        "reviewer": d.reviewer.full_name,
                        "decision": d.decision,
                        "reason_code": d.reason_code,
                        "reason": reasons.get(d.reason_code or "", None),
                        "note": d.note,
                    }
                    for d in record.decisions
                    if d.stage == stage
                ],
                "adjudication": {
                    "decision": adjudication.decision,
                    "reason_code": adjudication.reason_code,
                    "rationale": adjudication.rationale,
                    "adjudicator": adjudication.adjudicator.full_name if adjudication.adjudicator else None,
                }
                if adjudication
                else None,
            }
        )
    return items


def _agreement_out(result) -> dict:
    return {
        "n": result.n,
        "observed_agreement": result.observed,
        "kappa": result.kappa,
        "kappa_ci": result.kappa_ci,
        "pabak": result.pabak,
    }


@router.get("/screening/agreement")
def screening_agreement(
    stage: Stage = "title_abstract",
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    """Cohen's kappa and PABAK between reviewers: overall (first two reviewers of each record) and per reviewer pair."""
    categories = FINAL_DECISIONS[stage]
    by_record: dict[int, dict[int, str]] = defaultdict(dict)
    names: dict[int, str] = {}
    for record in _unique_records(db, access.project.id):
        for decision in sorted(record.decisions, key=lambda d: d.id):
            if decision.stage == stage and decision.decision in categories:
                by_record[record.id][decision.reviewer_id] = decision.decision
                names[decision.reviewer_id] = decision.reviewer.full_name
    overall_pairs = []
    pair_labels: dict[tuple[int, int], list[tuple[str, str]]] = defaultdict(list)
    for decisions in by_record.values():
        reviewers = list(decisions)
        if len(reviewers) >= 2:
            overall_pairs.append((decisions[reviewers[0]], decisions[reviewers[1]]))
        for a, b in combinations(sorted(reviewers), 2):
            pair_labels[(a, b)].append((decisions[a], decisions[b]))
    return {
        "stage": stage,
        "categories": list(categories),
        "overall": _agreement_out(cohens_kappa(overall_pairs, categories)),
        "pairs": [
            {"reviewers": [names[a], names[b]], **_agreement_out(cohens_kappa(labels, categories))}
            for (a, b), labels in sorted(pair_labels.items())
        ],
    }


@router.get("/screening/ai-performance")
def ai_performance(
    stage: Stage = "title_abstract",
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    """How AI suggestions compare with reviewers' final decisions. "Maybe" counts as include, as it keeps a record in
    screening. An alert is raised when sensitivity is below the project's recall target."""
    settings, policy = _settings(db, access.project.id)
    task = "screening" if stage == TITLE_ABSTRACT else "fulltext_screening"
    tp = fp = tn = fn = 0
    for record in _unique_records(db, access.project.id):
        final = policy.final(record, stage)
        run = latest_run(record, task)
        if final not in ("include", "exclude") or run is None or run.screening is None:
            continue
        suggested_include = run.screening.decision in ("Include", "Maybe")
        if final == "include":
            tp, fn = tp + suggested_include, fn + (not suggested_include)
        else:
            fp, tn = fp + suggested_include, tn + (not suggested_include)
    sensitivity = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    target = settings.screening.recall_target
    return {
        "stage": stage,
        "compared": tp + fp + tn + fn,
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "sensitivity": sensitivity,
        "sensitivity_ci": wilson_interval(tp, tp + fn),
        "specificity": specificity,
        "specificity_ci": wilson_interval(tn, tn + fp),
        "recall_target": target,
        "below_target": sensitivity is not None and sensitivity < target,
    }


# --- Prioritization ---


def _decision_count(db: Session, project_id: int, stage: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(models.ScreeningDecision)
            .join(models.Record)
            .where(models.Record.project_id == project_id, models.ScreeningDecision.stage == stage)
        )
        or 0
    )


def _latest_model(db: Session, project_id: int) -> models.ScreeningModelRun | None:
    return db.scalar(
        select(models.ScreeningModelRun)
        .where(models.ScreeningModelRun.project_id == project_id, models.ScreeningModelRun.stage == TITLE_ABSTRACT)
        .order_by(models.ScreeningModelRun.id.desc())
        .limit(1)
    )


def _model_out(run: models.ScreeningModelRun | None, decisions: int, retrain_every: int) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "algorithm": run.algorithm,
        "includes": run.includes,
        "excludes": run.excludes,
        "ranked": run.ranked,
        "top_terms": run.top_terms,
        "created_at": run.created_at,
        "decisions_since": decisions - run.decisions_count,
        "retrain_due": decisions - run.decisions_count >= retrain_every,
    }


@router.post("/screening/rank")
async def rank_records(
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)), db: Session = Depends(get_db)
):
    """Train the prioritization model on reviewers' decisions and rank the records nobody has decided yet."""
    require_stage_open(db, access.project.id, "screening")
    settings, policy = _settings(db, access.project.id)
    records = _unique_records(db, access.project.id)
    texts = {record.id: f"{record.title}\n{record.abstract}" for record in records}
    decided = [(record, policy.final(record)) for record in records]
    training = [(texts[record.id], final == "include") for record, final in decided if final in ("include", "exclude")]
    pending = [record for record, final in decided if final not in ("include", "exclude")]
    try:
        model = await asyncio.to_thread(
            train, [text for text, _ in training], [label for _, label in training], list(texts.values())
        )
    except NotEnoughDecisions as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    scores = await asyncio.to_thread(lambda: [(record.id, model.score(texts[record.id])) for record in pending])
    run = models.ScreeningModelRun(
        project_id=access.project.id,
        stage=TITLE_ABSTRACT,
        algorithm=ALGORITHM,
        includes=model.includes,
        excludes=model.excludes,
        decisions_count=_decision_count(db, access.project.id, TITLE_ABSTRACT),
        ranked=len(scores),
        top_terms=model.top_terms(),
        created_by_id=access.user.id,
    )
    db.add(run)
    db.flush()
    ordered = sorted(scores, key=lambda item: (-item[1], item[0]))
    db.add_all(
        models.ScreeningRank(model_run_id=run.id, record_id=record_id, score=score, rank=rank)
        for rank, (record_id, score) in enumerate(ordered, start=1)
    )
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="screening.ranked",
        entity_type="screening_model_run",
        entity_id=run.id,
        details={"algorithm": ALGORITHM, "includes": model.includes, "excludes": model.excludes, "ranked": len(scores)},
    )
    db.commit()
    return _model_out(run, run.decisions_count, settings.screening.retrain_every)


@router.get("/screening/queue")
def screening_queue(
    stage: Stage = "title_abstract",
    limit: int = 25,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    """Records still needing this reviewer's decision, most likely includes first when a ranking exists."""
    settings, policy = _settings(db, access.project.id)
    limit = max(1, min(limit, 200))
    records = _unique_records(db, access.project.id)
    ranks: dict[int, tuple[int, float]] = {}
    model = _latest_model(db, access.project.id) if stage == TITLE_ABSTRACT else None
    if model is not None:
        for row in db.scalars(select(models.ScreeningRank).where(models.ScreeningRank.model_run_id == model.id)):
            ranks[row.record_id] = (row.rank, row.score)

    def needs_me(record: models.Record) -> bool:
        if stage == FULL_TEXT and not policy.sought(record):
            return False
        status = policy.status(record, stage)
        if status.final in FINAL_DECISIONS[stage] or status.state == "conflict":
            return False
        mine = next((d for d in record.decisions if d.stage == stage and d.reviewer_id == access.user.id), None)
        return mine is None or mine.decision == "undecided"

    waiting = [record for record in records if needs_me(record)]
    waiting.sort(key=lambda record: (ranks.get(record.id, (10**9, 0.0))[0], record.id))
    blind = settings.screening.blind_dual_screening
    return {
        "stage": stage,
        "remaining": len(waiting),
        "model": _model_out(
            model, _decision_count(db, access.project.id, TITLE_ABSTRACT), settings.screening.retrain_every
        ),
        "records": [
            {
                **record_out(record, access.user.id, policy, blind),
                "priority": {"rank": ranks[record.id][0], "score": ranks[record.id][1]} if record.id in ranks else None,
            }
            for record in waiting[:limit]
        ],
    }


# --- Stopping rules and quality assurance ---


def _screening_sequence(records: list[models.Record], policy: ReviewPolicy) -> list[bool]:
    """Each decided record's relevance, in the order reviewers first decided it."""
    screened = []
    for record in records:
        final = policy.final(record)
        decided = [d for d in record.decisions if d.stage == TITLE_ABSTRACT and d.decision != "undecided"]
        if final in ("include", "exclude") and decided:
            screened.append((min(d.created_at for d in decided), record.id, final == "include"))
    return [relevant for _, _, relevant in sorted(screened)]


def _evaluation_out(evaluation: models.StoppingEvaluation | None, records_total: int) -> dict | None:
    if evaluation is None:
        return None
    return {
        "id": evaluation.id,
        "method": evaluation.method,
        "result": evaluation.result,
        "created_at": evaluation.created_at,
        "accepted_at": evaluation.accepted_at,
        "accepted_by": evaluation.accepted_by.full_name if evaluation.accepted_by else None,
        "acceptance_rationale": evaluation.acceptance_rationale,
        "still_applies": evaluation.records_total == records_total,
    }


@router.post("/screening/stopping", status_code=201)
def evaluate_stopping(
    body: StoppingRequest,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    """Test whether prioritized screening can stop at the recall target (hypergeometric test)."""
    require_stage_open(db, access.project.id, "screening")
    settings, policy = _settings(db, access.project.id)
    records = _unique_records(db, access.project.id)
    result = hypergeometric_test(
        _screening_sequence(records, policy),
        len(records),
        body.recall_target or settings.screening.recall_target,
        body.alpha or settings.screening.stopping_alpha,
    )
    evaluation = models.StoppingEvaluation(
        project_id=access.project.id,
        stage=TITLE_ABSTRACT,
        method=result.method,
        result=result.as_dict(),
        records_total=len(records),
        decisions_count=_decision_count(db, access.project.id, TITLE_ABSTRACT),
        created_by_id=access.user.id,
    )
    db.add(evaluation)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="screening.stopping_evaluated",
        entity_type="stopping_evaluation",
        entity_id=evaluation.id,
        details={"can_stop": result.can_stop, "p_value": result.p_value, "recall_target": result.recall_target},
    )
    db.commit()
    return _evaluation_out(evaluation, len(records))


@router.get("/screening/stopping")
def latest_stopping(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    evaluation = db.scalar(
        select(models.StoppingEvaluation)
        .where(models.StoppingEvaluation.project_id == access.project.id)
        .order_by(models.StoppingEvaluation.id.desc())
        .limit(1)
    )
    return _evaluation_out(evaluation, len(_unique_records(db, access.project.id)))


@router.post("/screening/stopping/{evaluation_id}/accept")
def accept_stopping(
    evaluation_id: int,
    body: AcceptStoppingRequest,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    """Sign the decision to stop screening: records nobody screened are then reported as excluded by automation."""
    require_stage_open(db, access.project.id, "screening")
    evaluation = get_in_project(db, models.StoppingEvaluation, evaluation_id, access.project.id, "Stopping evaluation")
    records = _unique_records(db, access.project.id)
    latest_id = db.scalar(
        select(func.max(models.StoppingEvaluation.id)).where(models.StoppingEvaluation.project_id == access.project.id)
    )
    if evaluation.id != latest_id or evaluation.records_total != len(records):
        raise HTTPException(status_code=409, detail="Screening has changed since this test; run the test again")
    if evaluation.decisions_count != _decision_count(db, access.project.id, TITLE_ABSTRACT):
        raise HTTPException(status_code=409, detail="Decisions have been made since this test; run the test again")
    if not evaluation.result.get("can_stop"):
        raise HTTPException(status_code=409, detail="The test doesn't support stopping yet")
    evaluation.accepted_by_id, evaluation.accepted_at = access.user.id, models.utcnow()
    evaluation.acceptance_rationale = body.rationale.strip()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="screening.stopping_rule_accepted",
        entity_type="stopping_evaluation",
        entity_id=evaluation.id,
        details={"rationale": evaluation.acceptance_rationale, "result": evaluation.result},
    )
    db.commit()
    return _evaluation_out(evaluation, len(records))


def _qa_out(
    sample: models.QASample | None, records: list[models.Record], policy: ReviewPolicy, target: float
) -> dict | None:
    if sample is None:
        return None
    by_id = {record.id: record for record in records}
    in_sample = [by_id[i] for i in sample.record_ids if i in by_id]
    decided = [record for record in in_sample if policy.final(record) in ("include", "exclude")]
    sample_includes = sum(1 for record in decided if policy.final(record) == "include")
    sample_set = set(sample.record_ids)
    found_outside = sum(1 for record in records if record.id not in sample_set and policy.final(record) == "include")
    found = found_outside + sample_includes
    remaining = max(0, sample.pool_size - len(decided))
    estimate: dict[str, float | None] = {
        "recall": None,
        "recall_low": None,
        "recall_high": None,
        "estimated_missed": None,
    }
    interval = wilson_interval(sample_includes, len(decided))
    if decided and interval is not None:
        rate = sample_includes / len(decided)
        missed, missed_high, missed_low = rate * remaining, interval[1] * remaining, interval[0] * remaining
        if found + missed_high > 0:
            estimate = {
                "recall": found / (found + missed) if found + missed else 1.0,
                "recall_low": found / (found + missed_high),
                "recall_high": found / (found + missed_low) if found + missed_low else 1.0,
                "estimated_missed": missed,
            }
    return {
        "id": sample.id,
        "created_at": sample.created_at,
        "size": len(sample.record_ids),
        "pool_size": sample.pool_size,
        "screened": len(decided),
        "includes_found_in_sample": sample_includes,
        "record_ids": sample.record_ids,
        **estimate,
        "recall_target": target,
        "below_target": estimate["recall_low"] is not None and estimate["recall_low"] < target,
    }


@router.post("/screening/qa-samples", status_code=201)
def draw_qa_sample(
    body: QASampleRequest,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    """Draw a random sample of records nobody has screened, for reviewers to screen and estimate recall from."""
    require_stage_open(db, access.project.id, "screening")
    settings, policy = _settings(db, access.project.id)
    records = _unique_records(db, access.project.id)
    pool = [record.id for record in records if not any(d.stage == TITLE_ABSTRACT for d in record.decisions)]
    if not pool:
        raise HTTPException(status_code=400, detail="Every record has been screened, so there is nothing to sample")
    sample = models.QASample(
        project_id=access.project.id,
        stage=TITLE_ABSTRACT,
        record_ids=random.SystemRandom().sample(pool, min(body.size, len(pool))),
        pool_size=len(pool),
        created_by_id=access.user.id,
    )
    db.add(sample)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="screening.qa_sample_drawn",
        entity_type="qa_sample",
        entity_id=sample.id,
        details={"size": len(sample.record_ids), "pool_size": len(pool)},
    )
    db.commit()
    return _qa_out(sample, records, policy, settings.screening.recall_target)


@router.get("/screening/qa")
def latest_qa(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)):
    settings, policy = _settings(db, access.project.id)
    sample = db.scalar(
        select(models.QASample)
        .where(models.QASample.project_id == access.project.id)
        .order_by(models.QASample.id.desc())
        .limit(1)
    )
    return _qa_out(sample, _unique_records(db, access.project.id), policy, settings.screening.recall_target)


# --- Settings ---


@router.get("/review-settings")
def get_review_settings(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    settings = load_settings(db, access.project.id)
    return {
        **settings.model_dump(),
        "exclusion_reasons": [{"code": code, "label": label} for code, label in exclusion_reasons(settings).items()],
    }


def _stage_completed(db: Session, project_id: int, stage: str) -> bool:
    try:
        require_stage_open(db, project_id, stage)
        return False
    except WorkflowError as exc:
        return "completed" in str(exc)


@router.put("/review-settings")
def update_review_settings(
    body: ReviewSettingsData,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    """Change how screening and extraction run. Settings for a signed-off stage can't change until it's reopened."""
    before = load_settings(db, access.project.id)
    locked = [
        (before.screening.title_abstract_reviewers != body.screening.title_abstract_reviewers, "screening"),
        (before.screening.full_text_reviewers != body.screening.full_text_reviewers, "full_text_screening"),
        (before.extraction != body.extraction, "extraction"),
    ]
    for changed, stage in locked:
        if changed and _stage_completed(db, access.project.id, stage):
            raise HTTPException(
                status_code=409, detail=f"Reopen the {stage.replace('_', ' ')} stage to change how it runs"
            )
    row = db.scalar(select(models.ReviewSettings).where(models.ReviewSettings.project_id == access.project.id))
    if row is None:
        row = models.ReviewSettings(project_id=access.project.id)
        db.add(row)
    row.screening, row.extraction = body.screening.model_dump(), body.extraction.model_dump()
    row.updated_by_id = access.user.id
    if before != body:
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="review_settings.updated",
            entity_type="project",
            entity_id=access.project.id,
            details={"before": before.model_dump(), "after": body.model_dump()},
        )
    db.commit()
    return get_review_settings(access, db)


# --- Synthesis ---


def synthesis_out(report: models.SynthesisReport) -> dict:
    return {
        "id": report.id,
        "content": report.content,
        "record_count": report.record_count,
        "provider": report.ai_run.provider,
        "model": report.ai_run.model,
        "created_at": report.created_at,
    }


@router.post("/synthesis", status_code=201, dependencies=[Depends(ai_rate_limit)])
async def create_synthesis(
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "synthesis")
    _, policy = _settings(db, access.project.id)
    included_ids = {record.id for record in included_records(db, access.project.id, policy)}
    records = db.scalars(
        with_record_details(select(models.Record).where(models.Record.id.in_(included_ids)).order_by(models.Record.id))
    ).all()
    if not records:
        raise HTTPException(status_code=400, detail="Include at least one record before generating a synthesis")

    study_rows = dataset_content(db, access.project.id)["studies"]
    study_data = [
        {
            "study": study["label"],
            "reports": [report["title"] for report in study["reports"]],
            "extracted_data": {
                (f"{value['field']} [{value['arm']}]" if value["arm"] else value["field"]): value["display"]
                for value in study["values"]
                if value["display"]
            },
        }
        for study in study_rows
    ]
    appraisals = {
        record.title: run.appraisal.judgments
        for record in records
        if (run := latest_run(record, "appraisal")) is not None and run.appraisal is not None
    }
    for item in study_data:
        item["risk_of_bias"] = [appraisals[title] for title in item["reports"] if title in appraisals]
    ai = project_ai(db, access)
    run = new_ai_run(access, "synthesis", SYNTHESIS_PROMPT, ai)
    try:
        result = await generate_narrative_synthesis(ai, study_data)
    except LLMError as exc:
        run.status, run.error = "failed", str(exc)
        record_usage(run, ai, exc.usage)
        db.add(run)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    run.status = "succeeded"
    record_usage(run, ai, result.usage)
    report = models.SynthesisReport(
        project_id=access.project.id, ai_run=run, content=result.value, record_count=len(records)
    )
    db.add(report)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="ai.synthesis",
        entity_type="synthesis_report",
        entity_id=report.id,
        details={
            "provider": run.provider,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "records": len(records),
        },
    )
    db.commit()
    return synthesis_out(report)


@router.get("/synthesis")
def latest_synthesis(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    report = db.scalar(
        select(models.SynthesisReport)
        .where(models.SynthesisReport.project_id == access.project.id)
        .order_by(models.SynthesisReport.id.desc())
        .limit(1)
    )
    return synthesis_out(report) if report else None
