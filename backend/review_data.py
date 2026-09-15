"""Rules about records shared by the API routes, the workflow gates, and scripts."""

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
