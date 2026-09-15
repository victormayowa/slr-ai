"""PRISMA 2020 flow diagram: counts computed from stored searches, decisions, documents, and studies, and the diagram
as SVG or as CSV in the layout of the PRISMA2020 R package's data template (Haddaway et al. 2022).

Records from citation searching and grey literature ("other methods") are counted in their own column. OmniReview
screens every record at title and abstract, so the other-methods column also reports records screened and excluded.
"""

import csv
import io
from dataclasses import dataclass, field
from html import escape
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import models
from review_data import FULL_TEXT, TITLE_ABSTRACT, ReviewPolicy
from review_settings import exclusion_reasons, load_settings

OTHER_KINDS = ("other", "citation")


@dataclass
class Column:
    records_identified: int = 0
    duplicates_removed: int = 0
    records_screened: int = 0
    records_excluded: int = 0
    excluded_by_automation: int = 0
    awaiting_screening: int = 0
    reports_sought: int = 0
    reports_not_retrieved: int = 0
    reports_assessed: int = 0
    reports_excluded: dict[str, int] = field(default_factory=dict)
    awaiting_full_text: int = 0


def accepted_stopping(db: Session, project_id: int, stage: str, records_total: int) -> models.StoppingEvaluation | None:
    """The accepted stopping evaluation that still applies (no records were added since it was accepted)."""
    evaluation = db.scalar(
        select(models.StoppingEvaluation)
        .where(
            models.StoppingEvaluation.project_id == project_id,
            models.StoppingEvaluation.stage == stage,
            models.StoppingEvaluation.accepted_at.is_not(None),
        )
        .order_by(models.StoppingEvaluation.id.desc())
        .limit(1)
    )
    if evaluation is None or evaluation.records_total != records_total:
        return None
    return evaluation


def flow_counts(db: Session, project_id: int) -> dict[str, Any]:
    settings = load_settings(db, project_id)
    policy = ReviewPolicy(settings.screening.title_abstract_reviewers, settings.screening.full_text_reviewers)
    reasons = exclusion_reasons(settings)
    runs = db.scalars(select(models.SearchRun).where(models.SearchRun.project_id == project_id)).all()
    records = db.scalars(
        select(models.Record)
        .where(models.Record.project_id == project_id)
        .options(
            selectinload(models.Record.decisions),
            selectinload(models.Record.adjudications),
            selectinload(models.Record.search_run),
        )
    ).all()
    retrieved = set(
        db.scalars(
            select(models.Document.record_id).where(
                models.Document.project_id == project_id, models.Document.role == "full_text"
            )
        )
    )
    unique_total = sum(1 for r in records if r.duplicate_of_id is None)
    stopped = accepted_stopping(db, project_id, TITLE_ABSTRACT, unique_total) is not None

    columns = {"databases": Column(), "other": Column()}
    by_source: dict[str, int] = {}
    identification = {"databases": 0, "registers": 0, "uploads": 0, "citation_searching": 0, "websites_and_other": 0}
    for run in runs:
        by_source[run.source_label] = by_source.get(run.source_label, 0) + run.result_count
        key = {
            "database": "databases",
            "register": "registers",
            "import": "uploads",
            "citation": "citation_searching",
        }.get(run.kind, "websites_and_other")
        identification[key] += run.result_count
        columns["other" if run.kind in OTHER_KINDS else "databases"].records_identified += run.result_count

    included_records: list[models.Record] = []
    for record in records:
        column = columns["other" if record.search_run.kind in OTHER_KINDS else "databases"]
        if record.duplicate_of_id is not None:
            column.duplicates_removed += 1
            continue
        ta = policy.final(record, TITLE_ABSTRACT)
        if ta in ("include", "exclude"):
            column.records_screened += 1
        if ta == "exclude":
            column.records_excluded += 1
        elif ta != "include":
            if stopped:
                column.excluded_by_automation += 1
            else:
                column.awaiting_screening += 1
            continue
        if ta != "include":
            continue
        column.reports_sought += 1
        ft_status = policy.status(record, FULL_TEXT)
        if ft_status.final == "not_retrieved":
            column.reports_not_retrieved += 1
        elif ft_status.final in ("include", "exclude"):
            column.reports_assessed += 1
            if ft_status.final == "exclude":
                label = reasons.get(ft_status.reason_code or "", ft_status.reason_code or "Reason not recorded")
                column.reports_excluded[label] = column.reports_excluded.get(label, 0) + 1
            else:
                included_records.append(record)
        elif record.id not in retrieved:
            column.reports_not_retrieved += 1
        else:
            column.awaiting_full_text += 1

    study_of = {
        record_id: study_id
        for record_id, study_id in db.execute(
            select(models.StudyReport.record_id, models.StudyReport.study_id).where(
                models.StudyReport.record_id.in_([r.id for r in included_records])
            )
        ).tuples()
    }
    studies = {study_of.get(record.id, -record.id) for record in included_records}
    return {
        "identification": {**identification, "by_source": by_source},
        "columns": {name: column.__dict__ for name, column in columns.items()},
        "stopping_rule_applied": stopped,
        "included": {"studies": len(studies), "reports": len(included_records)},
    }


def legacy_counts(flow: dict[str, Any]) -> dict[str, Any]:
    """The /prisma summary: totals across both columns."""
    identification, columns = flow["identification"], flow["columns"]

    def total(name: str) -> int:
        return sum(column[name] for column in columns.values())

    excluded_full_text: dict[str, int] = {}
    for column in columns.values():
        for reason, count in column["reports_excluded"].items():
            excluded_full_text[reason] = excluded_full_text.get(reason, 0) + count
    return {
        "identified_from_databases": identification["databases"],
        "identified_from_registers": identification["registers"],
        "identified_from_other_methods": identification["citation_searching"] + identification["websites_and_other"],
        "identified_from_uploads": identification["uploads"],
        "other_methods": {
            "citation_searching": identification["citation_searching"],
            "grey_literature_and_websites": identification["websites_and_other"],
        },
        "by_source": identification["by_source"],
        "duplicates_removed": total("duplicates_removed"),
        "screened": total("records_screened") + total("awaiting_screening") + total("excluded_by_automation"),
        "excluded": total("records_excluded"),
        "included": total("reports_sought"),
        "awaiting_decision": total("awaiting_screening"),
        "excluded_by_automation": total("excluded_by_automation"),
        "reports_sought_for_retrieval": total("reports_sought"),
        "reports_not_retrieved": total("reports_not_retrieved"),
        "reports_assessed": total("reports_assessed"),
        "reports_excluded": excluded_full_text,
        "awaiting_full_text_decision": total("awaiting_full_text"),
        "studies_included": flow["included"]["studies"],
        "reports_of_included_studies": flow["included"]["reports"],
    }


def _reason_list(reasons: dict[str, int]) -> str:
    return "; ".join(f"{reason}, {count}" for reason, count in sorted(reasons.items(), key=lambda item: -item[1]))


def to_csv(flow: dict[str, Any]) -> str:
    identification, db, other = flow["identification"], flow["columns"]["databases"], flow["columns"]["other"]
    database_sources = "; ".join(f"{name}, {count}" for name, count in identification["by_source"].items())
    rows = [
        ("previous_studies", "Studies included in previous version of review", ""),
        ("previous_reports", "Reports of studies included in previous version of review", ""),
        (
            "database_results",
            "Records identified from databases",
            identification["databases"] + identification["uploads"],
        ),
        ("database_specific_results", "Records identified from each database or source", database_sources),
        ("register_results", "Records identified from registers", identification["registers"]),
        (
            "website_results",
            "Records identified from websites and other grey literature sources",
            identification["websites_and_other"],
        ),
        ("organisation_results", "Records identified from organisations", 0),
        ("citations_results", "Records identified from citation searching", identification["citation_searching"]),
        ("duplicates", "Duplicate records removed", db["duplicates_removed"]),
        (
            "excluded_automatic",
            "Records marked as ineligible by automation tools (accepted stopping rule)",
            db["excluded_by_automation"],
        ),
        ("excluded_other", "Records removed for other reasons", 0),
        ("records_screened", "Records screened", db["records_screened"]),
        ("records_excluded", "Records excluded", db["records_excluded"]),
        ("dbr_sought_reports", "Reports sought for retrieval", db["reports_sought"]),
        ("dbr_notretrieved_reports", "Reports not retrieved", db["reports_not_retrieved"]),
        ("other_sought_reports", "Reports sought for retrieval (other methods)", other["reports_sought"]),
        ("other_notretrieved_reports", "Reports not retrieved (other methods)", other["reports_not_retrieved"]),
        ("dbr_assessed", "Reports assessed for eligibility", db["reports_assessed"]),
        ("dbr_excluded", "Reports excluded, with reasons", _reason_list(db["reports_excluded"])),
        ("other_assessed", "Reports assessed for eligibility (other methods)", other["reports_assessed"]),
        ("other_excluded", "Reports excluded, with reasons (other methods)", _reason_list(other["reports_excluded"])),
        ("new_studies", "New studies included in review", flow["included"]["studies"]),
        ("new_reports", "Reports of new included studies", flow["included"]["reports"]),
        ("total_studies", "Total studies included in review", flow["included"]["studies"]),
        ("total_reports", "Reports of total included studies", flow["included"]["reports"]),
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["data", "node", "box", "description", "boxtext", "tooltips", "url", "n"])
    for data, description, n in rows:
        writer.writerow([data, "", "", description, "", "", "", n])
    return buffer.getvalue()


_BOX_WIDTH = 300
_LINE_HEIGHT = 18


def _box(x: int, y: int, lines: list[str], width: int = _BOX_WIDTH, fill: str = "#ffffff") -> tuple[str, int]:
    wrapped: list[str] = []
    for line in lines:
        words, current = line.split(" "), ""
        for word in words:
            if len(current) + len(word) + 1 > width // 7 and current:
                wrapped.append(current)
                current = f"  {word}" if line.startswith("  ") else word
            else:
                current = f"{current} {word}" if current else word
        wrapped.append(current)
    height = 16 + _LINE_HEIGHT * len(wrapped)
    text = "".join(
        f'<text x="{x + 10}" y="{y + 22 + index * _LINE_HEIGHT}">{escape(line)}</text>'
        for index, line in enumerate(wrapped)
    )
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="{fill}" stroke="#333"/>{text}', height


def _arrow(x1: int, y1: int, x2: int, y2: int) -> str:
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#333" stroke-width="1.5" marker-end="url(#arrow)"/>'


def to_svg(flow: dict[str, Any]) -> str:
    identification, db, other = flow["identification"], flow["columns"]["databases"], flow["columns"]["other"]
    parts: list[str] = []
    sources = [f"  {name} (n = {count})" for name, count in identification["by_source"].items() if count]
    rows_y = {"identification": 70, "screening": 330, "retrieval": 470, "eligibility": 590, "included": 800}

    def column(x_main: int, x_side: int, data: dict[str, Any], identified: list[str], removed: list[str]) -> None:
        box, height = _box(x_main, rows_y["identification"], identified)
        parts.append(box)
        side, side_height = _box(x_side, rows_y["identification"], removed)
        parts.append(side)
        parts.append(_arrow(x_main + _BOX_WIDTH, rows_y["identification"] + 30, x_side, rows_y["identification"] + 30))
        parts.append(
            _arrow(
                x_main + _BOX_WIDTH // 2,
                rows_y["identification"] + max(height, side_height),
                x_main + _BOX_WIDTH // 2,
                rows_y["screening"],
            )
        )
        for name, main_lines, side_lines in (
            (
                "screening",
                [f"Records screened (n = {data['records_screened']})"],
                [f"Records excluded (n = {data['records_excluded']})"],
            ),
            (
                "retrieval",
                [f"Reports sought for retrieval (n = {data['reports_sought']})"],
                [f"Reports not retrieved (n = {data['reports_not_retrieved']})"],
            ),
            (
                "eligibility",
                [f"Reports assessed for eligibility (n = {data['reports_assessed']})"],
                ["Reports excluded:"]
                + [f"  {reason} (n = {count})" for reason, count in data["reports_excluded"].items()],
            ),
        ):
            y = rows_y[name]
            box, height = _box(x_main, y, main_lines)
            parts.append(box)
            side, _ = _box(x_side, y, side_lines)
            parts.append(side)
            parts.append(_arrow(x_main + _BOX_WIDTH, y + 20, x_side, y + 20))
            next_row = {"screening": "retrieval", "retrieval": "eligibility", "eligibility": "included"}[name]
            parts.append(_arrow(x_main + _BOX_WIDTH // 2, y + height, x_main + _BOX_WIDTH // 2, rows_y[next_row]))

    column(
        40,
        380,
        db,
        [
            "Records identified from:",
            f"  Databases (n = {identification['databases'] + identification['uploads']})",
            f"  Registers (n = {identification['registers']})",
            *sources[:8],
        ],
        [
            "Records removed before screening:",
            f"  Duplicate records (n = {db['duplicates_removed']})",
            f"  Marked ineligible by automation tools (n = {db['excluded_by_automation']})",
        ],
    )
    column(
        760,
        1100,
        other,
        [
            "Records identified from:",
            f"  Websites and other sources (n = {identification['websites_and_other']})",
            f"  Citation searching (n = {identification['citation_searching']})",
        ],
        [
            "Records removed before screening:",
            f"  Duplicate records (n = {other['duplicates_removed']})",
            f"  Marked ineligible by automation tools (n = {other['excluded_by_automation']})",
        ],
    )
    included, _ = _box(
        40,
        rows_y["included"],
        [
            f"Studies included in review (n = {flow['included']['studies']})",
            f"Reports of included studies (n = {flow['included']['reports']})",
        ],
        width=_BOX_WIDTH,
        fill="#eef6ee",
    )
    parts.append(included)
    parts.append(_arrow(910, rows_y["eligibility"] + 60, 340, rows_y["included"] + 20))
    headers = "".join(
        f'<rect x="{x}" y="20" width="{w}" height="34" fill="#dbe8f5" stroke="#333"/>'
        f'<text x="{x + 10}" y="42" font-weight="bold">{escape(title)}</text>'
        for x, w, title in (
            (40, 640, "Identification of studies via databases and registers"),
            (760, 640, "Identification of studies via other methods"),
        )
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="920" viewBox="0 0 1440 920" '
        'font-family="Arial, Helvetica, sans-serif" font-size="13">'
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">'
        '<path d="M0,0 L9,3 L0,6 z" fill="#333"/></marker></defs>'
        '<rect width="1440" height="920" fill="#ffffff"/>'
        f"{headers}{''.join(parts)}</svg>"
    )
