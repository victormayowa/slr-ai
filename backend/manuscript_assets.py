"""Tables and figures embedded in the manuscript, built live from the review's records: study characteristics, risk of
bias, summary of findings, GRADE evidence profile, searches, AI use, the PRISMA flow diagram, analysis plots, and robvis
risk of bias plots."""

import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_disclosure import ai_use
from appraisal_tools import JUDGMENT_SETS, TOOLS
from certainty_routes import SOF_COLUMNS, _assessments, sof_table_rows, summary_of_findings
from evidence_catalog import approved_analyses, run_date
from grading import DOWNGRADE_DOMAINS
from prisma_flow import flow_counts, to_svg
from publishing_tools import tool_path
from stats_engine import StatsEngineUnavailable, StatsRunError, render_robvis
from storage import document_storage
from synthesis_data import extraction_source

TABLES = {
    "study_characteristics": "Characteristics of included studies",
    "risk_of_bias": "Risk of bias in included studies",
    "sof": "Summary of findings",
    "grade_profile": "GRADE evidence profile",
    "searches": "Information sources and searches",
    "ai_use": "Use of artificial intelligence",
}
RATING_WORDS = {0: "Not serious", -1: "Serious", -2: "Very serious", 1: "Rated up", 2: "Rated up twice"}
OUTCOME_FIELD_TYPES = {"dichotomous", "continuous", "effect_estimate", "dta_2x2"}


@dataclass
class Table:
    key: str
    title: str
    header: list[str]
    rows: list[list[str]]
    note: str = ""


@dataclass
class Figure:
    key: str
    title: str
    files: dict[str, bytes] = field(default_factory=dict)

    def best(self, preferred: tuple[str, ...]) -> tuple[str, bytes] | None:
        return next(((fmt, self.files[fmt]) for fmt in preferred if fmt in self.files), None)


def table(db: Session, project: models.Project, key: str) -> Table | None:
    project_id = project.id
    if key == "study_characteristics":
        content, _, _ = extraction_source(db, project_id)
        fields = [f for f in content["fields"] if not f["per_arm"] and f["field_type"] not in OUTCOME_FIELD_TYPES][:6]
        rows = []
        for study in content["studies"]:
            values = {(v["field_id"], v["arm_id"]): v["display"] for v in study["values"]}
            rows.append(
                [study["label"], "; ".join(a["label"] for a in study["arms"])]
                + [values.get((f["id"], None), "") for f in fields]
            )
        return Table(key, TABLES[key], ["Study", "Arms", *[f["name"] for f in fields]], rows)
    if key == "risk_of_bias":
        labels = {s["study_id"]: s["label"] for s in extraction_source(db, project_id)[0]["studies"]}
        rows = []
        for assessment in db.scalars(
            select(models.AppraisalAssessment)
            .where(
                models.AppraisalAssessment.project_id == project_id, models.AppraisalAssessment.status == "signed_off"
            )
            .order_by(models.AppraisalAssessment.tool, models.AppraisalAssessment.id)
        ):
            tool = TOOLS[assessment.tool]
            domain_labels = {d.key: d for d in tool.domains}
            judged = "; ".join(
                f"{domain_labels[d.domain].label}: "
                f"{dict(JUDGMENT_SETS[domain_labels[d.domain].judgments]).get(d.judgment, d.judgment)}"
                for d in assessment.domains
                if d.domain in domain_labels
            )
            overall = dict(JUDGMENT_SETS[tool.overall_judgments]).get(assessment.overall_judgment or "", "")
            study = labels.get(assessment.study_id, str(assessment.study_id))
            rows.append(
                [f"{study}{f' ({assessment.outcome})' if assessment.outcome else ''}", tool.label, judged, overall]
            )
        return Table(key, TABLES[key], ["Study (result)", "Tool", "Domain judgments", "Overall"], rows)
    if key == "sof":
        return Table(key, TABLES[key], list(SOF_COLUMNS), sof_table_rows(summary_of_findings(db, project)))
    if key == "grade_profile":
        rows = []
        for grade in _assessments(db, project_id):
            domains = grade.domains or {}
            rows.append(
                [grade.outcome]
                + [RATING_WORDS.get(int((domains.get(k) or {}).get("rating", 0)), "") for k in DOWNGRADE_DOMAINS]
                + [grade.certainty.replace("_", " ").capitalize()]
            )
        return Table(key, TABLES[key], ["Outcome", *DOWNGRADE_DOMAINS.values(), "Certainty"], rows)
    if key == "searches":
        runs = db.scalars(
            select(models.SearchRun).where(models.SearchRun.project_id == project_id).order_by(models.SearchRun.id)
        )
        rows = [[r.source_label, r.interface or r.database, run_date(r), str(r.result_count)] for r in runs]
        return Table(key, TABLES[key], ["Source", "Interface", "Date searched", "Records"], rows)
    if key == "ai_use":
        rows = [
            [
                t["label"],
                ", ".join(t["models"]),
                str(t["runs"]),
                ", ".join(t["prompt_versions"]),
                f"{t['first']} to {t['last']}",
            ]
            for t in ai_use(db, project_id)["tasks"]
        ]
        return Table(key, TABLES[key], ["Task", "Models", "Runs", "Prompt versions", "Dates"], rows)
    return None


def convert_svg(svg: bytes, fmt: str) -> bytes | None:
    """PNG (300 dpi) or PDF from SVG with rsvg-convert, when it's installed."""
    converter = tool_path("RSVG_CONVERT_PATH", "rsvg-convert")
    if converter is None:
        return None
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "figure.svg"
        target = Path(folder) / f"figure.{fmt}"
        source.write_bytes(svg)
        args = [converter, "-f", fmt, "-o", str(target), str(source)]
        if fmt == "png":
            args[1:1] = ["-d", "300", "-p", "300"]
        completed = subprocess.run(args, capture_output=True, timeout=60, check=False)
        return target.read_bytes() if completed.returncode == 0 and target.exists() else None


def _signed_off_rows(db: Session, project_id: int, tool_key: str) -> list[dict[str, Any]]:
    labels = {s["study_id"]: s["label"] for s in extraction_source(db, project_id)[0]["studies"]}
    rows = []
    for assessment in db.scalars(
        select(models.AppraisalAssessment).where(
            models.AppraisalAssessment.project_id == project_id,
            models.AppraisalAssessment.tool == tool_key,
            models.AppraisalAssessment.status == "signed_off",
        )
    ):
        rows.append(
            {
                "study": labels.get(assessment.study_id, str(assessment.study_id)),
                "outcome": assessment.outcome,
                "domains": {d.domain: d.judgment for d in assessment.domains},
                "overall": assessment.overall_judgment,
            }
        )
    return rows


async def figure(db: Session, project: models.Project, key: str) -> Figure | None:
    project_id = project.id
    if key == "prisma":
        svg = to_svg(flow_counts(db, project_id)).encode()
        result = Figure(key, "PRISMA 2020 flow diagram", {"svg": svg})
        for fmt in ("png", "pdf"):
            converted = convert_svg(svg, fmt)
            if converted:
                result.files[fmt] = converted
        return result
    parts = key.split(":")
    if parts[0] == "run" and len(parts) == 3 and parts[1].isdigit():
        run = db.get(models.AnalysisRun, int(parts[1]))
        if run is None or run.project_id != project_id:
            return None
        storage = document_storage()
        files = {p["format"]: storage.read(p["storage_key"]) for p in run.plots if p["name"] == parts[2]}
        if not files:
            return None
        return Figure(key, f"{run.analysis.title}: {parts[2].replace('_', ' ')} plot", files)
    if parts[0] == "robvis" and len(parts) == 3 and parts[1] in TOOLS and parts[2] in ("traffic_light", "summary"):
        tool = TOOLS[parts[1]]
        rows = _signed_off_rows(db, project_id, tool.key)
        if not rows:
            return None
        domains = [{"key": d.key, "label": d.label, "kind": d.kind} for d in tool.domains]
        result = Figure(
            key, f"{tool.label} risk of bias {'traffic light' if parts[2] == 'traffic_light' else 'summary'} plot"
        )
        for fmt in ("svg", "png", "pdf"):
            try:
                content, _ = await render_robvis(tool, domains, rows, parts[2], fmt)
            except (StatsEngineUnavailable, StatsRunError):
                continue
            result.files[fmt] = content
        return result if result.files else None
    return None


def available_assets(db: Session, project: models.Project) -> list[dict[str, str]]:
    assets = [
        {"marker": f"[[table:{key}]]", "kind": "table", "key": key, "title": title} for key, title in TABLES.items()
    ]
    assets.append(
        {"marker": "[[figure:prisma]]", "kind": "figure", "key": "prisma", "title": "PRISMA 2020 flow diagram"}
    )
    for analysis, run in approved_analyses(db, project.id):
        for name in sorted({p["name"] for p in run.plots}):
            assets.append(
                {
                    "marker": f"[[figure:run:{run.id}:{name}]]",
                    "kind": "figure",
                    "key": f"run:{run.id}:{name}",
                    "title": f"{analysis.title}: {name.replace('_', ' ')}",
                }
            )
    tools: dict[str, int] = defaultdict(int)
    for tool_key in db.scalars(
        select(models.AppraisalAssessment.tool).where(
            models.AppraisalAssessment.project_id == project.id, models.AppraisalAssessment.status == "signed_off"
        )
    ):
        tools[tool_key] += 1
    for tool_key in tools:
        for kind in ("traffic_light", "summary"):
            assets.append(
                {
                    "marker": f"[[figure:robvis:{tool_key}:{kind}]]",
                    "kind": "figure",
                    "key": f"robvis:{tool_key}:{kind}",
                    "title": f"{TOOLS[tool_key].label} {kind.replace('_', ' ')} plot",
                }
            )
    return assets
