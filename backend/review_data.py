"""Rules about records shared by the API routes, the workflow gates, and scripts.

A record's final decision at a stage comes only from reviewers. With one reviewer per record, the latest decision
counts. With two, both must decide; if they disagree (at full text, also on the exclusion reason), a third reviewer
adjudicates. AI suggestions never count.
"""

from dataclasses import dataclass

import models

TITLE_ABSTRACT = models.TITLE_ABSTRACT
FULL_TEXT = models.FULL_TEXT
SCREENING_STAGES = (TITLE_ABSTRACT, FULL_TEXT)
# The workflow stage (workflow.STAGES) where each screening stage's decisions are made.
WORKFLOW_STAGE = {TITLE_ABSTRACT: "screening", FULL_TEXT: "full_text_screening"}
FINAL_DECISIONS = {TITLE_ABSTRACT: ("include", "exclude"), FULL_TEXT: ("include", "exclude", "not_retrieved")}


@dataclass(frozen=True)
class ScreeningStatus:
    # "unscreened", "undecided", "awaiting_second_reviewer", "conflict", "decided", or "adjudicated"
    state: str
    # The decision that counts: include, exclude, not_retrieved, undecided (single review only), or None.
    final: str | None
    reason_code: str | None
    # Reviewers who recorded a decision other than "undecided".
    reviewers_decided: int


def screening_status(
    record: models.Record, stage: str = TITLE_ABSTRACT, reviewers_required: int = 1
) -> ScreeningStatus:
    adjudication = next((a for a in reversed(record.adjudications) if a.stage == stage), None)
    decisions = [d for d in record.decisions if d.stage == stage]
    decided = [d for d in decisions if d.decision != "undecided"]
    if adjudication is not None:
        return ScreeningStatus("adjudicated", adjudication.decision, adjudication.reason_code, len(decided))
    if not decisions:
        return ScreeningStatus("unscreened", None, None, 0)
    if reviewers_required <= 1:
        latest = max(decisions, key=lambda d: d.id)
        state = "undecided" if latest.decision == "undecided" else "decided"
        return ScreeningStatus(state, latest.decision, latest.reason_code, len(decided))
    if not decided:
        return ScreeningStatus("undecided", None, None, 0)
    if len(decided) < reviewers_required:
        return ScreeningStatus("awaiting_second_reviewer", None, None, len(decided))
    outcomes = {
        (d.decision, d.reason_code if stage == FULL_TEXT and d.decision == "exclude" else None) for d in decided
    }
    if len(outcomes) > 1:
        return ScreeningStatus("conflict", None, None, len(decided))
    decision, reason = next(iter(outcomes))
    return ScreeningStatus("decided", decision, reason, len(decided))


def final_decision(record: models.Record, stage: str = TITLE_ABSTRACT, reviewers_required: int = 1) -> str | None:
    """The decision that counts at the stage. AI suggestions never count."""
    return screening_status(record, stage, reviewers_required).final


@dataclass(frozen=True)
class ReviewPolicy:
    """How many reviewers each screening stage needs (review_settings.ScreeningSettings)."""

    title_abstract_reviewers: int = 1
    full_text_reviewers: int = 1

    def reviewers(self, stage: str) -> int:
        return self.full_text_reviewers if stage == FULL_TEXT else self.title_abstract_reviewers

    def status(self, record: models.Record, stage: str = TITLE_ABSTRACT) -> ScreeningStatus:
        return screening_status(record, stage, self.reviewers(stage))

    def final(self, record: models.Record, stage: str = TITLE_ABSTRACT) -> str | None:
        return self.status(record, stage).final

    def sought(self, record: models.Record) -> bool:
        """Included at title and abstract, so its full text is sought."""
        return record.duplicate_of_id is None and self.final(record, TITLE_ABSTRACT) == "include"

    def included(self, record: models.Record) -> bool:
        """Included in the review: included at both title and abstract and full text."""
        return self.sought(record) and self.final(record, FULL_TEXT) == "include"


def latest_run(record: models.Record, task: str) -> models.AIRun | None:
    return next((run for run in reversed(record.ai_runs) if run.task == task), None)


def has_successful_run(record: models.Record, task: str) -> bool:
    run = latest_run(record, task)
    return run is not None and run.status == "succeeded"
