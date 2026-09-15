"""Living reviews: surveillance schedules, runs, candidate records and their promotion into a living update, alerts,
watched feeds, retraction checks, provisional impact assessments, versioned releases, and evidence and gap maps."""

import asyncio
import hashlib
import json
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import grading
import models
from audit import record_event
from certainty_routes import effect_summary
from database import get_db
from extraction_data import included_studies
from jobs import job_out, start_job
from manuscript_state import get_manuscript, latest_version
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from publication_routes import DepositIn, create_deposit, deposit_out
from publication_state import stage_completed
from rate_limiting import ai_rate_limit
from search_sources import CONNECTORS
from stats_engine import ANALYSIS_TYPES, StatsEngineUnavailable, StatsRunError, run_analysis
from surveillance import DEFAULT_THRESHOLDS, check_feeds, check_retractions, run_schedule
from synthesis_data import extraction_source
from synthesis_routes import final_run
from workflow import STAGES, WorkflowError, reopen_stage

router = APIRouter(prefix="/api/projects/{project_id}", tags=["living"])
OUTCOME_FIELD_TYPES = {"dichotomous", "continuous", "effect_estimate", "dta_2x2"}


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


# --- Schedules and runs ---


def schedule_out(s: models.SurveillanceSchedule) -> dict:
    return {
        "id": s.id,
        "strategy_id": s.strategy_id,
        "database": s.strategy.database,
        "connector": s.connector,
        "frequency_days": s.frequency_days,
        "next_run_at": s.next_run_at,
        "last_run_at": s.last_run_at,
        "active": s.active,
        "thresholds": {**DEFAULT_THRESHOLDS, **(s.thresholds or {})},
    }


def run_out(r: models.SurveillanceRun) -> dict:
    return {
        "id": r.id,
        "schedule_id": r.schedule_id,
        "kind": r.kind,
        "status": r.status,
        "database": r.database,
        "retrieved": r.retrieved,
        "new_candidates": r.new_candidates,
        "duplicates": r.duplicates,
        "error": r.error,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }


class ScheduleIn(BaseModel):
    strategy_id: int
    connector: str = Field(max_length=40)
    frequency_days: int = Field(30, ge=1, le=365)
    active: bool = True
    thresholds: dict[str, float] = Field(default_factory=dict)


def _validate_schedule(db: Session, access: ProjectAccess, body: ScheduleIn) -> None:
    get_in_project(db, models.SearchStrategy, body.strategy_id, access.project.id, "Search strategy")
    if body.connector not in CONNECTORS:
        raise HTTPException(status_code=422, detail=f"Unknown search connector: {body.connector}")
    unknown = set(body.thresholds) - set(DEFAULT_THRESHOLDS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown thresholds: {', '.join(sorted(unknown))}")


@router.get("/surveillance/schedules")
def list_schedules(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.SurveillanceSchedule)
        .where(models.SurveillanceSchedule.project_id == access.project.id)
        .order_by(models.SurveillanceSchedule.id)
    )
    return {
        "schedules": [schedule_out(s) for s in rows],
        "connectors": {k: c.label for k, c in CONNECTORS.items()},
        "default_thresholds": DEFAULT_THRESHOLDS,
    }


@router.post("/surveillance/schedules", status_code=201)
def create_schedule(
    body: ScheduleIn,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    if not stage_completed(db, access.project.id, "search"):
        raise HTTPException(status_code=409, detail="Sign off the original search before scheduling surveillance")
    _validate_schedule(db, access, body)
    schedule = models.SurveillanceSchedule(
        project_id=access.project.id,
        strategy_id=body.strategy_id,
        connector=body.connector,
        frequency_days=body.frequency_days,
        next_run_at=models.utcnow() + timedelta(days=body.frequency_days),
        active=body.active,
        thresholds=body.thresholds,
        created_by_id=access.user.id,
    )
    db.add(schedule)
    db.flush()
    _audit(db, access, "surveillance.schedule_created", "surveillance_schedule", schedule.id, body.model_dump())
    db.commit()
    db.refresh(schedule)
    return schedule_out(schedule)


@router.put("/surveillance/schedules/{schedule_id}")
def update_schedule(
    schedule_id: int,
    body: ScheduleIn,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    schedule = get_in_project(db, models.SurveillanceSchedule, schedule_id, access.project.id, "Schedule")
    _validate_schedule(db, access, body)
    schedule.strategy_id, schedule.connector, schedule.active, schedule.thresholds = (
        body.strategy_id,
        body.connector,
        body.active,
        body.thresholds,
    )
    if body.frequency_days != schedule.frequency_days:
        schedule.frequency_days = body.frequency_days
        schedule.next_run_at = (schedule.last_run_at or models.utcnow()) + timedelta(days=body.frequency_days)
    schedule.created_by_id = access.user.id
    _audit(db, access, "surveillance.schedule_updated", "surveillance_schedule", schedule.id, body.model_dump())
    db.commit()
    db.refresh(schedule)
    return schedule_out(schedule)


@router.delete("/surveillance/schedules/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: int,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    schedule = get_in_project(db, models.SurveillanceSchedule, schedule_id, access.project.id, "Schedule")
    schedule.active = False
    _audit(db, access, "surveillance.schedule_stopped", "surveillance_schedule", schedule.id, {})
    db.commit()
    from fastapi import Response

    return Response(status_code=204)


@router.post("/surveillance/schedules/{schedule_id}/run", dependencies=[Depends(ai_rate_limit)])
async def run_now(
    schedule_id: int,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    schedule = get_in_project(db, models.SurveillanceSchedule, schedule_id, access.project.id, "Schedule")
    run = await asyncio.to_thread(run_schedule, db, schedule)
    _audit(
        db,
        access,
        "surveillance.run",
        "surveillance_run",
        run.id,
        {"status": run.status, "new": run.new_candidates, "duplicates": run.duplicates},
    )
    db.commit()
    return run_out(run)


@router.get("/surveillance/runs")
def list_runs(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(models.SurveillanceRun)
        .where(models.SurveillanceRun.project_id == access.project.id)
        .order_by(models.SurveillanceRun.id.desc())
        .limit(200)
    )
    return [run_out(r) for r in rows]


# --- Candidates ---


def candidate_out(c: models.SurveillanceCandidate) -> dict:
    return {
        "id": c.id,
        "run_id": c.run_id,
        "title": c.title,
        "authors": c.authors,
        "year": c.year,
        "venue": c.venue,
        "doi": c.doi,
        "abstract": c.abstract,
        "url": c.url,
        "relevance": c.relevance,
        "sample_size": c.sample_size,
        "ai_decision": c.ai_decision,
        "ai_reasoning": c.ai_reasoning,
        "status": c.status,
        "decision_reason": c.decision_reason,
        "record_id": c.record_id,
        "created_at": c.created_at,
    }


@router.get("/surveillance/candidates")
def list_candidates(
    status: str = "",
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    query = select(models.SurveillanceCandidate).where(models.SurveillanceCandidate.project_id == access.project.id)
    if status:
        query = query.where(models.SurveillanceCandidate.status == status)
    rows = db.scalars(
        query.order_by(models.SurveillanceCandidate.relevance.desc().nulls_last(), models.SurveillanceCandidate.id)
    )
    return [candidate_out(c) for c in rows]


class CandidateDecision(BaseModel):
    decision: Literal["promote", "dismiss", "pending"]
    reason: str = Field("", max_length=2000)


@router.put("/surveillance/candidates/{candidate_id}")
def decide_candidate(
    candidate_id: int,
    body: CandidateDecision,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    """A reviewer promotes a candidate into the next living update or dismisses it. The AI suggestion never decides."""
    candidate = get_in_project(db, models.SurveillanceCandidate, candidate_id, access.project.id, "Candidate")
    if candidate.status == "imported":
        raise HTTPException(status_code=409, detail="This record is already in the review")
    if body.decision == "dismiss" and len(body.reason.strip()) < 3:
        raise HTTPException(status_code=422, detail="Give a reason for dismissing the record")
    candidate.status = {"promote": "promoted", "dismiss": "dismissed", "pending": "pending"}[body.decision]
    candidate.decision_reason, candidate.decided_by_id, candidate.decided_at = (
        body.reason.strip(),
        access.user.id,
        models.utcnow(),
    )
    _audit(
        db,
        access,
        "surveillance.candidate_decided",
        "surveillance_candidate",
        candidate.id,
        {"decision": body.decision, "reason": candidate.decision_reason, "ai_decision": candidate.ai_decision},
    )
    db.commit()
    return candidate_out(candidate)


class CandidateIds(BaseModel):
    candidate_ids: list[int] = Field(min_length=1, max_length=500)


@router.post("/surveillance/candidates/ai", status_code=202, dependencies=[Depends(ai_rate_limit)])
async def suggest_candidates(
    body: CandidateIds,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    found = set(
        db.scalars(
            select(models.SurveillanceCandidate.id).where(
                models.SurveillanceCandidate.project_id == access.project.id,
                models.SurveillanceCandidate.id.in_(body.candidate_ids),
            )
        )
    )
    if found != set(body.candidate_ids):
        raise HTTPException(status_code=404, detail="Some candidates weren't found in this project")
    return job_out(await start_job(db, access, "surveillance", sorted(found)))


class LivingUpdateIn(BaseModel):
    rationale: str = Field(min_length=10, max_length=2000)


@router.post("/living/updates")
def start_living_update(
    body: LivingUpdateIn,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    """Bring promoted records into the review. The search stage (and every later stage) reopens, so the new records go
    through the same gates: screening, full text, extraction, appraisal, synthesis, certainty, and the manuscript."""
    project = access.project
    promoted = db.scalars(
        select(models.SurveillanceCandidate)
        .where(models.SurveillanceCandidate.project_id == project.id, models.SurveillanceCandidate.status == "promoted")
        .order_by(models.SurveillanceCandidate.id)
    ).all()
    if not promoted:
        raise HTTPException(status_code=409, detail="Promote at least one surveillance record first")
    reopened: list[str] = []
    if stage_completed(db, project.id, "search"):
        try:
            reopened = reopen_stage(db, project, "search", access.user, f"Living update: {body.rationale}")
        except WorkflowError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    by_run: dict[int, list[models.SurveillanceCandidate]] = defaultdict(list)
    for candidate in promoted:
        by_run[candidate.run_id].append(candidate)
    for run_id, candidates in by_run.items():
        surveillance_run = db.get(models.SurveillanceRun, run_id)
        schedule = (
            db.get(models.SurveillanceSchedule, surveillance_run.schedule_id)
            if surveillance_run and surveillance_run.schedule_id
            else None
        )
        connector = CONNECTORS.get(schedule.connector) if schedule else None
        searched = (
            surveillance_run.started_at.date().isoformat() if surveillance_run else models.utcnow().date().isoformat()
        )
        search_run = models.SearchRun(
            project_id=project.id,
            strategy_id=schedule.strategy_id if schedule else None,
            kind=connector.kind if connector else "database",
            database=surveillance_run.database if surveillance_run else "Surveillance",
            source_label=f"Surveillance update: {surveillance_run.database if surveillance_run else ''} ({searched})",
            connector=schedule.connector if schedule else None,
            interface=connector.interface if connector else None,
            query=surveillance_run.query if surveillance_run else None,
            result_count=len(candidates),
            searched_on=searched,
            filters={"surveillance_run_id": run_id, "living_update": body.rationale},
            executed_by_id=access.user.id,
        )
        search_run.records = [
            models.Record(
                project_id=project.id,
                title=c.title,
                authors=c.authors,
                year=c.year,
                venue=c.venue,
                doi=c.doi,
                external_id=c.external_id,
                abstract=c.abstract,
                identifiers=c.identifiers,
                url=c.url,
            )
            for c in candidates
        ]
        db.add(search_run)
        db.flush()
        for candidate, record in zip(candidates, search_run.records, strict=True):
            candidate.status, candidate.record_id = "imported", record.id
    _audit(
        db,
        access,
        "living.update_started",
        "project",
        project.id,
        {"rationale": body.rationale, "records": len(promoted), "reopened": reopened},
    )
    db.commit()
    return {"imported": len(promoted), "reopened": reopened}


# --- Alerts, feeds, retractions ---


def alert_out(a: models.SurveillanceAlert) -> dict:
    return {
        "id": a.id,
        "kind": a.kind,
        "title": a.title,
        "detail": a.detail,
        "status": a.status,
        "note": a.note,
        "acknowledged_at": a.acknowledged_at,
        "created_at": a.created_at,
    }


@router.get("/surveillance/alerts")
def list_alerts(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.SurveillanceAlert)
        .where(models.SurveillanceAlert.project_id == access.project.id)
        .order_by(models.SurveillanceAlert.id.desc())
        .limit(500)
    )
    return [alert_out(a) for a in rows]


class AlertUpdate(BaseModel):
    status: Literal["open", "acknowledged", "dismissed"]
    note: str = Field("", max_length=2000)


@router.put("/surveillance/alerts/{alert_id}")
def update_alert(
    alert_id: int,
    body: AlertUpdate,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    row = get_in_project(db, models.SurveillanceAlert, alert_id, access.project.id, "Alert")
    row.status, row.note = body.status, body.note
    row.acknowledged_by_id, row.acknowledged_at = (
        (access.user.id, models.utcnow()) if body.status != "open" else (None, None)
    )
    _audit(
        db, access, "surveillance.alert_updated", "surveillance_alert", row.id, {"status": row.status, "note": row.note}
    )
    db.commit()
    return alert_out(row)


class FeedIn(BaseModel):
    label: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=10, max_length=1000, pattern=r"^https?://")


def feed_out(f: models.WatchFeed) -> dict:
    return {
        "id": f.id,
        "label": f.label,
        "url": f.url,
        "items_seen": len(f.seen_ids),
        "last_checked_at": f.last_checked_at,
        "last_error": f.last_error,
    }


@router.get("/surveillance/feeds")
def list_feeds(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)):
    return [
        feed_out(f)
        for f in db.scalars(
            select(models.WatchFeed)
            .where(models.WatchFeed.project_id == access.project.id)
            .order_by(models.WatchFeed.id)
        )
    ]


@router.post("/surveillance/feeds", status_code=201)
def add_feed(
    body: FeedIn, access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)), db: Session = Depends(get_db)
):
    from publication_routes import _public_url

    _public_url(body.url)
    feed = models.WatchFeed(
        project_id=access.project.id, label=body.label, url=body.url, seen_ids=[], created_by_id=access.user.id
    )
    db.add(feed)
    db.flush()
    _audit(db, access, "surveillance.feed_added", "watch_feed", feed.id, body.model_dump())
    db.commit()
    return feed_out(feed)


@router.delete("/surveillance/feeds/{feed_id}", status_code=204)
def delete_feed(
    feed_id: int, access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)), db: Session = Depends(get_db)
):
    from fastapi import Response

    feed = get_in_project(db, models.WatchFeed, feed_id, access.project.id, "Feed")
    db.delete(feed)
    db.commit()
    return Response(status_code=204)


@router.post("/surveillance/feeds/check")
async def check_feeds_now(
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)), db: Session = Depends(get_db)
):
    run = await asyncio.to_thread(check_feeds, db, access.project.id)
    db.commit()
    return run_out(run)


@router.post("/surveillance/retractions/check")
async def check_retractions_now(
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)), db: Session = Depends(get_db)
):
    run = await asyncio.to_thread(check_retractions, db, access.project.id)
    _audit(
        db,
        access,
        "surveillance.retractions_checked",
        "surveillance_run",
        run.id,
        {"checked": run.retrieved, "retracted": run.new_candidates},
    )
    db.commit()
    return run_out(run)


# --- Impact assessment ---


class CandidateRow(BaseModel):
    label: str = Field(min_length=1, max_length=300)
    values: dict[str, float] = Field(default_factory=dict)
    rob: str = Field("", max_length=30)


class ImpactIn(BaseModel):
    analysis_id: int
    rows: list[CandidateRow] = Field(min_length=1, max_length=50)


def impact_out(i: models.ImpactAssessment) -> dict:
    return {
        "id": i.id,
        "analysis_id": i.analysis_id,
        "candidate_rows": i.candidate_rows,
        "baseline": i.baseline,
        "provisional": i.provisional,
        "shift": i.shift,
        "grade_changes": i.grade_changes,
        "status": i.status,
        "error": i.error,
        "created_at": i.created_at,
    }


def _main(summary: dict[str, Any]) -> dict[str, Any]:
    ratio = summary.get("exp_estimate") is not None
    return {
        "k": summary.get("k") or summary.get("studies"),
        "estimate": summary.get("exp_estimate") if ratio else summary.get("estimate"),
        "ci_lower": summary.get("exp_ci_lower") if ratio else summary.get("ci_lower"),
        "ci_upper": summary.get("exp_ci_upper") if ratio else summary.get("ci_upper"),
        "I2": summary.get("I2"),
        "ratio": ratio,
    }


@router.post("/living/impact", status_code=201)
async def impact_assessment(
    body: ImpactIn,
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    """Rerun a final analysis with candidate studies added, provisionally, to see how the pooled result and suggested
    GRADE ratings might change. Nothing in the review changes."""
    analysis = get_in_project(db, models.Analysis, body.analysis_id, access.project.id, "Analysis")
    baseline_run = final_run(analysis)
    if baseline_run is None:
        raise HTTPException(status_code=409, detail="The analysis needs final results first")
    if analysis.analysis_type not in ("pairwise", "bayesian"):
        raise HTTPException(status_code=422, detail="Impact assessments support pairwise and Bayesian meta-analyses")
    data_type = baseline_run.dataset["r_spec"].get("data_type")
    needed = {
        "binary": {"ai", "n1i", "ci", "n2i"},
        "continuous": {"m1i", "sd1i", "n1i", "m2i", "sd2i", "n2i"},
        "generic": {"estimate", "ci_lower", "ci_upper"},
    }.get(data_type, set())
    for row in body.rows:
        missing = needed - set(row.values)
        if missing:
            raise HTTPException(status_code=422, detail=f"{row.label} needs {', '.join(sorted(missing))}")
    added = [
        {
            "study_id": -(i + 1),
            "study": f"{row.label} (candidate)",
            "rob": row.rob,
            **{k: row.values[k] for k in needed},
        }
        for i, row in enumerate(body.rows)
    ]
    rows = list(baseline_run.dataset["rows"]) + added
    template, packages = ANALYSIS_TYPES[analysis.analysis_type]
    assessment = models.ImpactAssessment(
        project_id=access.project.id,
        analysis_id=analysis.id,
        candidate_rows=added,
        status="failed",
        created_by_id=access.user.id,
    )
    db.add(assessment)
    try:
        output = await asyncio.to_thread(run_analysis, template, baseline_run.dataset["r_spec"], rows, packages)
    except StatsEngineUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StatsRunError as exc:
        assessment.error = str(exc)
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    before = _main(effect_summary(baseline_run))
    after_summary = dict(output.results.get("summary") or {})
    after_summary["studies"] = len({r["study_id"] for r in rows})
    after = _main(after_summary)
    assessment.baseline, assessment.provisional, assessment.status = before, after, "succeeded"
    assessment.shift = {
        "estimate": (after["estimate"] - before["estimate"])
        if isinstance(after["estimate"], int | float) and isinstance(before["estimate"], int | float)
        else None,
        "ci_width_before": (before["ci_upper"] - before["ci_lower"])
        if before["ci_upper"] is not None and before["ci_lower"] is not None
        else None,
        "ci_width_after": (after["ci_upper"] - after["ci_lower"])
        if after["ci_upper"] is not None and after["ci_lower"] is not None
        else None,
        "significance_changed": before["ci_lower"] is not None
        and after["ci_lower"] is not None
        and (
            (before["ci_lower"] < (1 if before["ratio"] else 0) < before["ci_upper"])
            != (after["ci_lower"] < (1 if after["ratio"] else 0) < after["ci_upper"])
        ),
    }
    grade = db.scalar(select(models.GradeAssessment).where(models.GradeAssessment.analysis_id == analysis.id))
    measure = baseline_run.spec.get("measure", "")
    start = grade.starting_certainty if grade else "high"
    before_ratings = grading.suggested_ratings(
        analysis.analysis_type,
        measure,
        baseline_run.results or {},
        baseline_run.dataset["rows"],
        grade.mid if grade else None,
        grade.mid_scale if grade else "",
        start,
    )
    after_ratings = grading.suggested_ratings(
        analysis.analysis_type,
        measure,
        output.results,
        rows,
        grade.mid if grade else None,
        grade.mid_scale if grade else "",
        start,
    )
    changes = []
    projected = {k: dict(v) for k, v in ((grade.domains if grade else {}) or {}).items()}
    for key, suggestion in after_ratings.items():
        old = before_ratings.get(key, {}).get("suggested_rating")
        if suggestion["suggested_rating"] != old:
            changes.append(
                {
                    "domain": key,
                    "before": old,
                    "after": suggestion["suggested_rating"],
                    "reason": suggestion["reason"],
                    "current_rating": (grade.domains.get(key) or {}).get("rating") if grade else None,
                }
            )
            projected[key] = {"rating": suggestion["suggested_rating"], "rationale": "projected"}
    assessment.grade_changes = changes + (
        [{"projected_certainty": grading.certainty(start, projected), "current_certainty": grade.certainty}]
        if grade
        else []
    )
    db.flush()
    _audit(
        db,
        access,
        "living.impact_assessed",
        "impact_assessment",
        assessment.id,
        {"analysis_id": analysis.id, "candidates": len(added), "shift": assessment.shift},
    )
    db.commit()
    return impact_out(assessment)


@router.get("/living/impact")
def list_impact(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.ImpactAssessment)
        .where(models.ImpactAssessment.project_id == access.project.id)
        .order_by(models.ImpactAssessment.id.desc())
    )
    return [impact_out(i) for i in rows]


# --- Releases ---


def release_content(db: Session, project: models.Project) -> dict[str, Any]:
    snapshots: dict[str, models.StageSnapshot] = {}
    for snapshot in db.scalars(
        select(models.StageSnapshot)
        .where(models.StageSnapshot.project_id == project.id)
        .order_by(models.StageSnapshot.id)
    ):
        snapshots[snapshot.stage] = snapshot
    analyses = []
    for analysis in db.scalars(
        select(models.Analysis)
        .where(models.Analysis.project_id == project.id, models.Analysis.status == "approved")
        .order_by(models.Analysis.id)
    ):
        run = final_run(analysis)
        if run is None:
            continue
        analyses.append(
            {
                "id": analysis.id,
                "title": analysis.title,
                "outcome": analysis.outcome,
                "run_id": run.id,
                "dataset_sha256": run.dataset_sha256,
                "summary": _main(effect_summary(run)),
            }
        )
    manuscript = get_manuscript(db, project.id)
    version = latest_version(manuscript) if manuscript else None
    return {
        "stages": {
            stage: {"version": s.version, "sha256": s.sha256, "snapshot_id": s.id} for stage, s in snapshots.items()
        },
        "studies": [{"id": s.id, "label": s.label} for s in included_studies(db, project.id)],
        "analyses": analyses,
        "certainty": [
            {"id": g.id, "outcome": g.outcome, "certainty": g.certainty}
            for g in db.scalars(select(models.GradeAssessment).where(models.GradeAssessment.project_id == project.id))
        ],
        "manuscript": {"version_id": version.id, "number": version.number, "sha256": version.sha256}
        if version
        else None,
    }


def changelog(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    if previous is None:
        return [f"First release: {len(current['studies'])} included studies, {len(current['analyses'])} analyses."]
    lines = []
    before_studies = {s["id"]: s["label"] for s in previous["studies"]}
    after_studies = {s["id"]: s["label"] for s in current["studies"]}
    added = [label for sid, label in after_studies.items() if sid not in before_studies]
    removed = [label for sid, label in before_studies.items() if sid not in after_studies]
    if added:
        lines.append(f"Added {len(added)} studies: {', '.join(added)}")
    if removed:
        lines.append(f"Removed {len(removed)} studies: {', '.join(removed)}")
    before_analyses = {a["id"]: a for a in previous["analyses"]}
    for analysis in current["analyses"]:
        old = before_analyses.get(analysis["id"])
        if old is None:
            lines.append(f"New analysis: {analysis['title']}")
        elif old["run_id"] != analysis["run_id"]:
            b, a = old["summary"], analysis["summary"]
            numbers = (b["estimate"], b["ci_lower"], b["ci_upper"], a["estimate"], a["ci_lower"], a["ci_upper"])
            if all(isinstance(x, int | float) for x in numbers):
                was = f"{b['estimate']:.2f} ({b['ci_lower']:.2f} to {b['ci_upper']:.2f})"
                now = f"{a['estimate']:.2f} ({a['ci_lower']:.2f} to {a['ci_upper']:.2f})"
                lines.append(f"{analysis['title']}: updated from {was} to {now} with {a['k']} studies")
            else:
                lines.append(f"{analysis['title']}: rerun")
    before_certainty = {g["outcome"]: g["certainty"] for g in previous["certainty"]}
    for grade in current["certainty"]:
        old_certainty = before_certainty.get(grade["outcome"])
        if old_certainty and old_certainty != grade["certainty"]:
            lines.append(
                f"Certainty for {grade['outcome']} changed from {old_certainty.replace('_', ' ')} to "
                f"{grade['certainty'].replace('_', ' ')}"
            )
    if (previous.get("manuscript") or {}).get("sha256") != (current.get("manuscript") or {}).get("sha256"):
        lines.append("The manuscript was revised")
    changed = [
        stage
        for stage, info in current["stages"].items()
        if (previous["stages"].get(stage) or {}).get("sha256") != info["sha256"]
    ]
    if changed:
        lines.append(f"Updated stages: {', '.join(changed)}")
    return lines or ["No changes to the evidence since the previous release."]


def release_out(r: models.ReviewRelease, deposits: list[models.RepositoryDeposit]) -> dict:
    return {
        "id": r.id,
        "version": r.version,
        "title": r.title,
        "notes": r.notes,
        "changelog": r.changelog,
        "sha256": r.sha256,
        "created_at": r.created_at,
        "content": r.content,
        "deposits": [deposit_out(d) for d in deposits if d.release_id == r.id],
    }


@router.get("/releases")
def list_releases(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.ReviewRelease)
        .where(models.ReviewRelease.project_id == access.project.id)
        .order_by(models.ReviewRelease.version.desc())
    ).all()
    deposits = list(
        db.scalars(select(models.RepositoryDeposit).where(models.RepositoryDeposit.project_id == access.project.id))
    )
    return [release_out(r, deposits) for r in rows]


class ReleaseIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    notes: str = Field("", max_length=10_000)


@router.post("/releases", status_code=201)
def create_release(
    body: ReleaseIn,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    """Publish a versioned release once every stage is signed off, with a changelog against the previous release."""
    project = access.project
    unfinished = [stage for stage in STAGES if not stage_completed(db, project.id, stage)]
    if unfinished:
        raise HTTPException(status_code=409, detail=f"Sign off every stage before a release: {', '.join(unfinished)}")
    previous = db.scalar(
        select(models.ReviewRelease)
        .where(models.ReviewRelease.project_id == project.id)
        .order_by(models.ReviewRelease.version.desc())
        .limit(1)
    )
    content = release_content(db, project)
    sha = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
    if previous is not None and previous.sha256 == sha:
        raise HTTPException(status_code=409, detail="Nothing changed since the previous release")
    release = models.ReviewRelease(
        project_id=project.id,
        version=(previous.version + 1) if previous else 1,
        title=body.title,
        notes=body.notes,
        changelog=changelog(previous.content if previous else None, content),
        content=content,
        sha256=sha,
        released_by_id=access.user.id,
    )
    db.add(release)
    db.flush()
    _audit(
        db,
        access,
        "living.release_created",
        "review_release",
        release.id,
        {"version": release.version, "sha256": sha, "changelog": release.changelog},
    )
    db.commit()
    return release_out(release, [])


class ReleaseDeposit(BaseModel):
    token: str = Field(min_length=10, max_length=500)
    sandbox: bool = True
    publish: bool = False
    include_manuscript: bool = True


@router.post("/releases/{release_id}/deposit", status_code=201)
async def deposit_release(
    release_id: int,
    body: ReleaseDeposit,
    access: ProjectAccess = Depends(project_access(Permission.EXPORT)),
    db: Session = Depends(get_db),
):
    """Deposit the release on Zenodo: a new version of the previous release's record when there is one."""
    release = get_in_project(db, models.ReviewRelease, release_id, access.project.id, "Release")
    deposit = await create_deposit(
        db,
        access,
        DepositIn(
            target="zenodo",
            repository="",
            gitlab_api_url="",
            token=body.token,
            sandbox=body.sandbox,
            publish=body.publish,
            include_manuscript=body.include_manuscript,
            version=str(release.version),
        ),
        release,
    )
    if deposit.status == "failed":
        raise HTTPException(status_code=502, detail=deposit.error or "The deposit failed")
    return deposit_out(deposit)


# --- Evidence and gap maps ---


@router.get("/living/evidence-map")
def evidence_map(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """Interventions by outcomes (studies with data, and GRADE certainty), a coverage heatmap of studies by fields, and
    trends in records, included studies, and surveillance."""
    project_id = access.project.id
    content, _, _ = extraction_source(db, project_id)
    outcome_fields = [f for f in content["fields"] if f["field_type"] in OUTCOME_FIELD_TYPES]
    certainty = {
        g.outcome.casefold(): g.certainty
        for g in db.scalars(select(models.GradeAssessment).where(models.GradeAssessment.project_id == project_id))
    }
    cells: dict[tuple[str, str], set[int]] = defaultdict(set)
    intervention_names: dict[str, str] = {}
    coverage = []
    for study in content["studies"]:
        arms = {arm["arm_id"]: arm["label"] for arm in study["arms"]}
        states: dict[int, str] = {}
        for value in study["values"]:
            state = (
                "reported"
                if value["display"] and not value["not_reported"]
                else ("not_reported" if value["not_reported"] else "missing")
            )
            if states.get(value["field_id"]) != "reported":
                states[value["field_id"]] = state
            field = next((f for f in outcome_fields if f["id"] == value["field_id"]), None)
            if field and state == "reported":
                label = arms.get(value["arm_id"], "All participants")
                key = label.strip().casefold()
                intervention_names.setdefault(key, label.strip())
                cells[(key, field.get("outcome") or field["name"])].add(study["study_id"])
        coverage.append(
            {
                "study_id": study["study_id"],
                "study": study["label"],
                "fields": {str(f["id"]): states.get(f["id"], "missing") for f in content["fields"]},
            }
        )
    bubbles = [
        {
            "intervention": intervention_names[key],
            "outcome": outcome,
            "studies": len(ids),
            "certainty": certainty.get(outcome.casefold()),
        }
        for (key, outcome), ids in sorted(cells.items())
    ]
    records = db.scalars(
        select(models.Record).where(models.Record.project_id == project_id, models.Record.duplicate_of_id.is_(None))
    ).all()
    by_year = Counter(r.year[:4] for r in records if r.year[:4].isdigit())
    included_years = Counter(
        (report.record.year or "")[:4]
        for study in included_studies(db, project_id)
        for report in study.reports
        if report.is_primary and (report.record.year or "")[:4].isdigit()
    )
    surveillance = [
        {"date": r.started_at.date().isoformat(), "new_candidates": r.new_candidates, "database": r.database}
        for r in db.scalars(
            select(models.SurveillanceRun)
            .where(models.SurveillanceRun.project_id == project_id, models.SurveillanceRun.kind == "search")
            .order_by(models.SurveillanceRun.id)
        )
    ]
    releases = [
        {"version": r.version, "date": r.created_at.date().isoformat(), "studies": len(r.content.get("studies", []))}
        for r in db.scalars(
            select(models.ReviewRelease)
            .where(models.ReviewRelease.project_id == project_id)
            .order_by(models.ReviewRelease.version)
        )
    ]
    return {
        "interventions": sorted({b["intervention"] for b in bubbles}),
        "outcomes": sorted({b["outcome"] for b in bubbles}),
        "bubbles": bubbles,
        "fields": [{"id": f["id"], "name": f["name"]} for f in content["fields"]],
        "coverage": coverage,
        "trends": {
            "records_by_year": dict(sorted(by_year.items())),
            "included_by_year": dict(sorted(included_years.items())),
            "surveillance": surveillance,
            "releases": releases,
        },
    }
