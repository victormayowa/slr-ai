"""Living review surveillance: saved strategies rerun on schedule, new records deduplicated against the review and
ranked by the project's screening model, AI screening suggestions, retraction checks of included studies, watched feeds,
and alerts. Surveillance records stay out of the review until a reviewer promotes them into a living update."""

import logging
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from active_learning import NotEnoughDecisions, TrainedModel, train
from dedup import normalized_title
from extraction_data import included_records
from permissions import Permission, has_permission
from projects_routes import ProjectAccess
from review_data import TITLE_ABSTRACT
from review_settings import review_policy
from search_sources import CONNECTORS
from services.errors import SearchError
from services.reference_checks import ReferenceCheckError, check_reference

logger = logging.getLogger(__name__)

MAX_RESULTS = 500
DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_new_records": 1,
    "min_predicted_relevant": 1,
    "relevance_threshold": 0.5,
    "large_trial_participants": 1000,
}
FEED_INTERVAL = timedelta(days=1)
RETRACTION_INTERVAL = timedelta(days=7)
FEED_MAX_BYTES = 5 * 1024 * 1024
PARTICIPANTS = re.compile(
    r"(?:\b[nN]\s*=\s*(\d{1,3}(?:,\d{3})+|\d{2,7})\b)|(?:\b(\d{1,3}(?:,\d{3})+|\d{2,7})\s+(?:participants|patients|adults|children|women|men|subjects|individuals|people|infants|residents)\b)"
)


def sample_size(text: str) -> int | None:
    values = [int((a or b).replace(",", "")) for a, b in PARTICIPANTS.findall(text or "")]
    return max(values) if values else None


def record_keys(doi: str, identifiers: dict[str, str] | None, title: str, year: str) -> set[str]:
    keys = set()
    if doi:
        keys.add(f"doi:{doi.lower().removeprefix('https://doi.org/')}")
    if (identifiers or {}).get("pmid"):
        keys.add(f"pmid:{identifiers['pmid']}")  # type: ignore[index]
    if normalized_title(title):
        keys.add(f"title:{normalized_title(title)}:{(year or '')[:4]}")
    return keys


def known_keys(db: Session, project_id: int) -> set[str]:
    keys: set[str] = set()
    for record in db.scalars(select(models.Record).where(models.Record.project_id == project_id)):
        keys |= record_keys(record.doi, record.identifiers, record.title, record.year)
    for candidate in db.scalars(
        select(models.SurveillanceCandidate).where(models.SurveillanceCandidate.project_id == project_id)
    ):
        keys |= record_keys(candidate.doi, candidate.identifiers, candidate.title, candidate.year)
    return keys


def relevance_model(db: Session, project_id: int) -> TrainedModel | None:
    policy = review_policy(db, project_id)
    records = db.scalars(
        select(models.Record).where(models.Record.project_id == project_id, models.Record.duplicate_of_id.is_(None))
    ).all()
    texts, labels = [], []
    for record in records:
        final = policy.final(record, TITLE_ABSTRACT)
        if final in ("include", "exclude"):
            texts.append(f"{record.title}\n{record.abstract}")
            labels.append(final == "include")
    try:
        return train(texts, labels, [f"{r.title}\n{r.abstract}" for r in records])
    except NotEnoughDecisions:
        return None


def alert(db: Session, project_id: int, kind: str, title: str, detail: dict[str, Any]) -> models.SurveillanceAlert:
    row = models.SurveillanceAlert(project_id=project_id, kind=kind, title=title[:500], detail=detail, status="open")
    db.add(row)
    return row


def schedule_access(db: Session, schedule: models.SurveillanceSchedule) -> ProjectAccess | None:
    membership = db.scalar(
        select(models.ProjectMember).where(
            models.ProjectMember.project_id == schedule.project_id,
            models.ProjectMember.user_id == schedule.created_by_id,
        )
    )
    if membership is None or not has_permission(membership.role, Permission.RUN_SEARCH):
        return None
    return ProjectAccess(user=membership.user, project=membership.project, membership=membership)


def run_schedule(
    db: Session, schedule: models.SurveillanceSchedule, now: datetime | None = None
) -> models.SurveillanceRun:
    now = now or models.utcnow()
    connector = CONNECTORS.get(schedule.connector)
    run = models.SurveillanceRun(
        project_id=schedule.project_id,
        schedule_id=schedule.id,
        kind="search",
        status="running",
        query=schedule.strategy.query,
        database=connector.label if connector else schedule.connector,
        started_at=now,
    )
    db.add(run)
    db.flush()
    schedule.last_run_at = now
    schedule.next_run_at = now + timedelta(days=schedule.frequency_days)
    if connector is None:
        run.status, run.error, run.finished_at = "failed", f"Unknown search connector: {schedule.connector}", now
        return run
    if schedule_access(db, schedule) is None:
        run.status, run.error, run.finished_at = (
            "failed",
            "The person who set up this schedule can no longer run searches",
            now,
        )
        return run
    try:
        results, _ = connector.search(schedule.strategy.query, MAX_RESULTS)
    except SearchError as exc:
        run.status, run.error, run.finished_at = "failed", str(exc), models.utcnow()
        return run
    thresholds = {**DEFAULT_THRESHOLDS, **(schedule.thresholds or {})}
    keys = known_keys(db, schedule.project_id)
    model = relevance_model(db, schedule.project_id)
    new: list[models.SurveillanceCandidate] = []
    for result in results:
        title = str(result.get("title") or "No Title")[:2000]
        doi = str(result.get("doi") or "")[:255]
        identifiers = result.get("identifiers") or {}
        year = str(result.get("year") or "")[:20]
        found = record_keys(doi, identifiers, title, year)
        if found & keys:
            run.duplicates += 1
            continue
        keys |= found
        abstract = str(result.get("abstract") or "")
        candidate = models.SurveillanceCandidate(
            project_id=schedule.project_id,
            run_id=run.id,
            title=title,
            authors=str(result.get("authors") or "")[:5000],
            year=year,
            venue=str(result.get("venue") or "")[:1000],
            doi=doi,
            abstract=abstract,
            identifiers=identifiers,
            url=str(result.get("url") or "")[:1000],
            external_id=str(result.get("id") or "")[:100],
            relevance=model.score(f"{title}\n{abstract}") if model else None,
            sample_size=sample_size(f"{title}\n{abstract}"),
            status="pending",
        )
        db.add(candidate)
        new.append(candidate)
    db.flush()
    run.retrieved, run.new_candidates = len(results), len(new)
    run.status, run.finished_at = "succeeded", models.utcnow()
    if new and len(new) >= thresholds["min_new_records"]:
        alert(
            db,
            schedule.project_id,
            "new_records",
            f"{len(new)} new records from {connector.label}",
            {"run_id": run.id, "count": len(new)},
        )
    relevant = [c for c in new if c.relevance is not None and c.relevance >= thresholds["relevance_threshold"]]
    if relevant and len(relevant) >= thresholds["min_predicted_relevant"]:
        alert(
            db,
            schedule.project_id,
            "new_eligible",
            f"{len(relevant)} new records look relevant to the review",
            {"run_id": run.id, "candidate_ids": [c.id for c in relevant]},
        )
    for candidate in new:
        if candidate.sample_size and candidate.sample_size >= thresholds["large_trial_participants"]:
            alert(
                db,
                schedule.project_id,
                "large_trial",
                f"Large study: {candidate.title[:300]} ({candidate.sample_size} participants)",
                {"candidate_id": candidate.id, "participants": candidate.sample_size},
            )
    return run


def check_retractions(db: Session, project_id: int) -> models.SurveillanceRun:
    """Look for retractions of included studies; also updates matching manuscript references."""
    run = models.SurveillanceRun(
        project_id=project_id, kind="retractions", status="running", database="Crossref and PubMed"
    )
    db.add(run)
    db.flush()
    records = included_records(db, project_id, review_policy(db, project_id))
    alerted = {
        a.detail.get("record_id")
        for a in db.scalars(
            select(models.SurveillanceAlert).where(
                models.SurveillanceAlert.project_id == project_id, models.SurveillanceAlert.kind == "retraction"
            )
        )
    }
    errors = 0
    for record in records:
        pmid = (record.identifiers or {}).get("pmid", "")
        if not record.doi and not pmid:
            continue
        run.retrieved += 1
        try:
            result = check_reference({"title": record.title}, record.doi, pmid)
        except ReferenceCheckError:
            errors += 1
            continue
        for ref in db.scalars(
            select(models.ManuscriptReference).where(models.ManuscriptReference.record_id == record.id)
        ):
            ref.retracted, ref.retraction, ref.checked_at = result.retracted, result.retraction, models.utcnow()
        if result.retracted:
            run.new_candidates += 1
            if record.id not in alerted:
                alert(
                    db,
                    project_id,
                    "retraction",
                    f"Included study retracted: {record.title[:300]}",
                    {"record_id": record.id, **result.retraction},
                )
    run.status, run.finished_at = "succeeded", models.utcnow()
    if errors:
        run.error = f"{errors} records couldn't be checked"
    return run


def parse_feed(content: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(content)  # noqa: S314 (feeds are size-limited and parsed without external entities)
    items = []
    for item in root.iter("item"):
        items.append(
            {
                "id": (item.findtext("guid") or item.findtext("link") or item.findtext("title") or "").strip(),
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
            }
        )
    atom = "{http://www.w3.org/2005/Atom}"
    for entry in root.iter(f"{atom}entry"):
        link = entry.find(f"{atom}link")
        items.append(
            {
                "id": (entry.findtext(f"{atom}id") or "").strip(),
                "title": (entry.findtext(f"{atom}title") or "").strip(),
                "link": link.get("href", "") if link is not None else "",
            }
        )
    return [i for i in items if i["id"]]


def check_feed(db: Session, feed: models.WatchFeed) -> list[dict[str, str]]:
    """New items since the last check. The first check records what's there without alerting."""
    feed.last_checked_at = models.utcnow()
    try:
        response = requests.get(feed.url, timeout=30, stream=True)
        response.raise_for_status()
        content = response.raw.read(FEED_MAX_BYTES + 1, decode_content=True)
        if len(content) > FEED_MAX_BYTES:
            raise ValueError("feed too large")
        items = parse_feed(content)
    except (requests.RequestException, ValueError, ET.ParseError) as exc:
        feed.last_error = (
            "The feed couldn't be read"
            if not isinstance(exc, ValueError)
            else "The feed is too large or not RSS or Atom"
        )
        return []
    feed.last_error = None
    seen = set(feed.seen_ids)
    new = [i for i in items if i["id"] not in seen]
    first_check = not feed.seen_ids
    feed.seen_ids = ([i["id"] for i in new] + list(feed.seen_ids))[:500]
    if new and not first_check:
        alert(
            db,
            feed.project_id,
            "feed_update",
            f"{len(new)} new items in {feed.label}",
            {"feed_id": feed.id, "items": new[:20]},
        )
    return [] if first_check else new


def check_feeds(db: Session, project_id: int) -> models.SurveillanceRun:
    run = models.SurveillanceRun(project_id=project_id, kind="feeds", status="running", database="Watched feeds")
    db.add(run)
    db.flush()
    for feed in db.scalars(select(models.WatchFeed).where(models.WatchFeed.project_id == project_id)):
        run.retrieved += 1
        run.new_candidates += len(check_feed(db, feed))
    run.status, run.finished_at = "succeeded", models.utcnow()
    return run


def run_due(db: Session, now: datetime | None = None) -> list[int]:
    """Run every due schedule, and daily feed and weekly retraction checks for projects with surveillance."""
    now = now or models.utcnow()
    runs: list[int] = []
    schedules = db.scalars(
        select(models.SurveillanceSchedule).where(
            models.SurveillanceSchedule.active.is_(True), models.SurveillanceSchedule.next_run_at <= now
        )
    ).all()
    for schedule in schedules:
        try:
            runs.append(run_schedule(db, schedule, now).id)
            db.commit()
        except Exception:
            logger.exception("Surveillance schedule %s failed", schedule.id)
            db.rollback()
    projects = set(
        db.scalars(select(models.SurveillanceSchedule.project_id).where(models.SurveillanceSchedule.active.is_(True)))
    )
    for project_id in projects:
        for kind, interval, action in (
            ("feeds", FEED_INTERVAL, check_feeds),
            ("retractions", RETRACTION_INTERVAL, check_retractions),
        ):
            last = db.scalar(
                select(models.SurveillanceRun.started_at)
                .where(models.SurveillanceRun.project_id == project_id, models.SurveillanceRun.kind == kind)
                .order_by(models.SurveillanceRun.id.desc())
                .limit(1)
            )
            if last is None or now - last >= interval:
                try:
                    runs.append(action(db, project_id).id)
                    db.commit()
                except Exception:
                    logger.exception("Surveillance %s check failed for project %s", kind, project_id)
                    db.rollback()
    return runs


async def process_candidates(db: Session, access: ProjectAccess, job: models.AIJob, candidate_ids: list[int]) -> None:
    """AI screening suggestions for surveillance candidates (a background job whose record_ids are candidate ids)."""
    from ai_access import new_ai_run, project_ai, record_usage
    from ai_tasks import _accepted_criteria
    from llm.prompts import SCREENING_PROMPT
    from services.ai_screening import evaluate_eligibility
    from services.errors import LLMError

    candidates = db.scalars(
        select(models.SurveillanceCandidate)
        .where(
            models.SurveillanceCandidate.project_id == job.project_id,
            models.SurveillanceCandidate.id.in_(candidate_ids),
        )
        .order_by(models.SurveillanceCandidate.id)
    ).all()
    job.total = len(candidates)
    criteria = _accepted_criteria(db, job.project_id)
    ai = project_ai(db, access)
    included = []
    for candidate in candidates:
        run = new_ai_run(access, "surveillance", SCREENING_PROMPT, ai)
        db.add(run)
        try:
            result = await evaluate_eligibility(
                ai, f"Title: {candidate.title}\nAbstract: {candidate.abstract or 'No abstract'}", criteria
            )
        except LLMError as exc:
            run.status, run.error = "failed", str(exc)
            record_usage(run, ai, exc.usage)
            job.failed += 1
        else:
            run.status = "succeeded"
            record_usage(run, ai, result.usage)
            db.flush()
            candidate.ai_decision, candidate.ai_reasoning, candidate.ai_run_id = (
                result.value.decision,
                result.value.reasoning,
                run.id,
            )
            if result.value.decision == "Include":
                included.append(candidate.id)
        job.processed += 1
        db.commit()
    if included:
        alert(
            db,
            job.project_id,
            "new_eligible",
            f"AI suggests including {len(included)} surveillance records",
            {"candidate_ids": included},
        )
        db.commit()


def next_run(now: datetime, frequency_days: int) -> datetime:
    return now + timedelta(days=max(1, math.floor(frequency_days)))
