"""Rules about records shared by the API routes, the workflow gates, and scripts."""

import re
from collections.abc import Iterable

import models

TITLE_ABSTRACT = models.TITLE_ABSTRACT


def final_decision(record: models.Record) -> str | None:
    """The most recent reviewer decision at title/abstract screening. AI suggestions never count."""
    decisions = [d for d in record.decisions if d.stage == TITLE_ABSTRACT]
    return max(decisions, key=lambda d: d.id).decision if decisions else None


def latest_run(record: models.Record, task: str) -> models.AIRun | None:
    return next((run for run in reversed(record.ai_runs) if run.task == task), None)


def has_successful_run(record: models.Record, task: str) -> bool:
    run = latest_run(record, task)
    return run is not None and run.status == "succeeded"


def dedup_key(record: models.Record) -> str:
    """Same DOI, or with no DOI the same normalized title, counts as a duplicate."""
    doi = record.doi.strip().lower()
    if doi:
        return doi
    return "title:" + re.sub(r"[^a-z0-9]+", " ", record.title.lower()).strip()


def find_duplicates(records: Iterable[models.Record]) -> dict[int, int]:
    """Map each duplicate not yet marked as one to the id of the earliest record it duplicates."""
    kept: dict[str, int] = {}
    duplicates: dict[int, int] = {}
    for record in sorted(records, key=lambda r: r.id):
        if record.duplicate_of_id is not None:
            continue
        key = dedup_key(record)
        if key in kept:
            duplicates[record.id] = kept[key]
        else:
            kept[key] = record.id
    return duplicates
