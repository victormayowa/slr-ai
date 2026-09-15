"""Search quality: strategy versions, PRESS 2015 peer review, and recall checks against known relevant articles."""

from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from services.literature_sources import europepmc_found_seeds
from services.openalex import openalex_found_seeds
from services.pubmed import pubmed_found_seeds
from services.record_import import normalize_doi


@dataclass(frozen=True)
class PressElement:
    key: str
    label: str
    guidance: str


# The six elements of the PRESS 2015 Evidence-Based Checklist (McGowan et al., J Clin Epidemiol 2016;75:40-46).
PRESS_ELEMENTS = (
    PressElement(
        "translation",
        "Translation of the research question",
        "Does the search match the research question? Are there too many or too few concepts, or wrong combinations?",
    ),
    PressElement(
        "operators",
        "Boolean and proximity operators",
        "Are AND, OR, and NOT used correctly, and could NOT remove relevant records? Is proximity used where it helps?",
    ),
    PressElement(
        "subject_headings",
        "Subject headings",
        "Are headings relevant and complete, correctly exploded or not, and paired with free-text terms?",
    ),
    PressElement(
        "text_words",
        "Text word searching",
        "Are synonyms, spelling variants, abbreviations, and truncation covered, in the right fields?",
    ),
    PressElement(
        "spelling_syntax",
        "Spelling, syntax, and line numbers",
        "Are there spelling mistakes, syntax errors for the interface, or wrong line combinations?",
    ),
    PressElement(
        "limits_filters",
        "Limits and filters",
        "Are limits and filters (dates, languages, designs, humans) justified by the protocol and applied correctly?",
    ),
)
PRESS_KEYS = [element.key for element in PRESS_ELEMENTS]
PRESS_RATINGS = ("no_revision", "revision_suggested", "revision_required")
RECALL_CONNECTORS = ("pubmed", "europepmc", "openalex")


def press_catalog() -> dict:
    return {"elements": [asdict(element) for element in PRESS_ELEMENTS], "ratings": list(PRESS_RATINGS)}


def record_strategy_version(
    db: Session, strategy: models.SearchStrategy, user_id: int | None, note: str | None
) -> None:
    db.add(
        models.SearchStrategyVersion(
            strategy_id=strategy.id,
            version=strategy.version,
            database=strategy.database,
            query=strategy.query,
            note=note,
            created_by_id=user_id,
        )
    )


def version_author(db: Session, strategy: models.SearchStrategy) -> int | None:
    return db.scalar(
        select(models.SearchStrategyVersion.created_by_id).where(
            models.SearchStrategyVersion.strategy_id == strategy.id,
            models.SearchStrategyVersion.version == strategy.version,
        )
    )


def press_status(db: Session, project_id: int) -> dict:
    """PRESS status of each strategy's current version, and whether the search gate's PRESS requirement is met."""
    reviews = db.scalars(
        select(models.PressReview).where(models.PressReview.project_id == project_id).order_by(models.PressReview.id)
    ).all()
    waiver = next((review for review in reversed(reviews) if review.status == "waived"), None)
    strategies = db.scalars(
        select(models.SearchStrategy)
        .where(models.SearchStrategy.project_id == project_id)
        .order_by(models.SearchStrategy.id)
    ).all()
    items = []
    for strategy in strategies:
        completed = [r for r in reviews if r.strategy_id == strategy.id and r.status == "completed"]
        current = [r for r in completed if r.strategy_version == strategy.version]
        if current:
            status = "approved" if current[-1].overall == "approved" else "revisions_required"
        else:
            status = "outdated" if completed else "not_reviewed"
        items.append(
            {
                "strategy_id": strategy.id,
                "database": strategy.database,
                "version": strategy.version,
                "status": status,
                "review_id": current[-1].id if current else None,
            }
        )
    return {
        "waived": waiver is not None,
        "waiver_reason": waiver.comment if waiver else None,
        "strategies": items,
        "met": waiver is not None or (bool(items) and all(item["status"] == "approved" for item in items)),
    }


def parse_seeds(seeds: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Split known relevant articles into PMIDs and DOIs; anything else is returned as unrecognized."""
    pmids, dois, unrecognized = [], [], []
    for seed in seeds:
        value = seed.strip()
        if not value:
            continue
        if value.isdigit():
            pmids.append(value)
        elif (doi := normalize_doi(value).lower()).startswith("10."):
            dois.append(doi)
        else:
            unrecognized.append(value)
    return list(dict.fromkeys(pmids)), list(dict.fromkeys(dois)), unrecognized


def found_seeds(connector: str, query: str, pmids: list[str], dois: list[str]) -> set[str]:
    if connector == "pubmed":
        return pubmed_found_seeds(query, pmids, dois)
    if connector == "europepmc":
        return europepmc_found_seeds(query, pmids, dois)
    if connector == "openalex":
        return openalex_found_seeds(query, pmids, dois)
    raise ValueError(f"Recall checks aren't available for {connector}")
