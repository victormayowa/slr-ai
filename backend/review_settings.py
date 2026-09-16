"""Per-project settings for how screening and extraction are run, and the full-text exclusion reasons.

Changing a setting is audited with its previous and new values, because it changes how decisions become final.
"""

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from review_data import ReviewPolicy


class ExclusionReason(BaseModel):
    code: str = Field(pattern=r"^[a-z0-9_]{2,60}$")
    label: str = Field(min_length=2, max_length=200)


class ScreeningSettings(BaseModel):
    title_abstract_reviewers: Literal[1, 2] = 1
    full_text_reviewers: Literal[1, 2] = 1
    # With two reviewers, each sees neither the other's decision nor the AI suggestion until they record their own.
    blind_dual_screening: bool = True
    recall_target: float = Field(0.95, ge=0.5, le=1.0)
    stopping_alpha: float = Field(0.05, gt=0, lt=0.5)
    # Retrain the prioritization model after this many new decisions.
    retrain_every: int = Field(10, ge=1, le=1000)
    # Require an accepted calibration report (governance_routes.py) before AI screening suggestions can be run.
    require_ai_calibration: bool = False
    custom_exclusion_reasons: list[ExclusionReason] = Field(default_factory=list, max_length=30)


class ExtractionSettings(BaseModel):
    # "single": one extractor's values are final. "dual": two extractors, with discrepancies reconciled.
    # "human_and_ai": one extractor, checked against AI suggestions as the second extractor.
    mode: Literal["single", "dual", "human_and_ai"] = "single"
    # Numbers agree when they differ by no more than max(absolute, relative × the larger magnitude).
    numeric_absolute_tolerance: float = Field(0.0, ge=0)
    numeric_relative_tolerance: float = Field(0.0, ge=0, le=0.5)


class ReviewSettingsData(BaseModel):
    screening: ScreeningSettings = ScreeningSettings.model_validate({})
    extraction: ExtractionSettings = ExtractionSettings.model_validate({})


STANDARD_EXCLUSION_REASONS: dict[str, str] = {
    "wrong_population": "Wrong population",
    "wrong_intervention": "Wrong intervention or exposure",
    "wrong_comparator": "Wrong comparator",
    "wrong_outcomes": "Wrong outcomes",
    "wrong_study_design": "Wrong study design",
    "wrong_setting": "Wrong setting",
    "wrong_publication_type": "Wrong publication type",
    "not_primary_research": "Not primary research",
    "insufficient_data": "Insufficient data reported",
    "duplicate": "Duplicate report",
    "language": "Language not covered by the protocol",
    "other": "Other (explained in the note)",
}


def load_settings(db: Session, project_id: int) -> ReviewSettingsData:
    row = db.scalar(select(models.ReviewSettings).where(models.ReviewSettings.project_id == project_id))
    if row is None:
        return ReviewSettingsData()
    return ReviewSettingsData(
        screening=ScreeningSettings.model_validate(row.screening or {}),
        extraction=ExtractionSettings.model_validate(row.extraction or {}),
    )


def review_policy(db: Session, project_id: int) -> ReviewPolicy:
    screening = load_settings(db, project_id).screening
    return ReviewPolicy(screening.title_abstract_reviewers, screening.full_text_reviewers)


def exclusion_reasons(settings: ReviewSettingsData) -> dict[str, str]:
    reasons = dict(STANDARD_EXCLUSION_REASONS)
    other = reasons.pop("other")
    reasons.update({reason.code: reason.label for reason in settings.screening.custom_exclusion_reasons})
    reasons["other"] = other
    return reasons
