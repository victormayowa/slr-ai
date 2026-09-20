"""Submission readiness, statements, submission packages, FAIR repository packages, and the submission stage's
requirements and snapshot."""

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from claims import CITATION, EMBED, EVIDENCE, embeds
from code_export import bundle_files
from dataset_export import to_csv, to_json, to_xlsx
from docx_style import styled_document
from evidence_catalog import approved_analyses
from manuscript_assets import figure, table
from manuscript_checklists import CHECKLISTS, applicable, checklist_context, complete, evaluate
from manuscript_export import ExportUnavailable, export_manuscript
from manuscript_state import (
    approval_state,
    cited_ids,
    get_manuscript,
    latest_version,
    prisma_checklist,
    references,
    verify,
)
from prisma_flow import flow_counts, to_svg
from prisma_flow import to_csv as prisma_csv
from protocol_export import protocol_document, to_docx
from stats_engine import export_bundle
from storage import document_storage
from synthesis_data import extraction_source

MAIN_SECTIONS = ("introduction", "methods", "results", "discussion", "conclusions")
GENERAL_STATEMENTS = ("funding", "competing_interests", "data_availability", "registration")
STATEMENT_LABELS = {
    "funding": "Funding",
    "competing_interests": "Competing interests",
    "data_availability": "Data availability",
    "ethics": "Ethics approval",
    "registration": "Registration",
    "author_contributions": "Author contributions (CRediT)",
    "ai_use": "Use of artificial intelligence",
    "acknowledgements": "Acknowledgements",
}


def plain(text: str) -> str:
    lines = [
        line
        for line in text.splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and not EMBED.match(line.strip())
        and not line.lstrip().startswith("|")
    ]
    return re.sub(r"\s+([.,;:])", r"\1", CITATION.sub("", EVIDENCE.sub("", "\n".join(lines))))


def word_count(text: str) -> int:
    return len(re.findall(r"[\w'’-]+(?:[.,]\d+)*", plain(text)))


def first_int(value: str) -> int | None:
    match = re.search(r"\d[\d,]*", value or "")
    return int(match.group(0).replace(",", "")) if match else None


def stage_completed(db: Session, project_id: int, stage: str) -> bool:
    row = db.scalar(
        select(models.ProjectStage).where(
            models.ProjectStage.project_id == project_id, models.ProjectStage.stage == stage
        )
    )
    return row is not None and row.completed_at is not None


def statements(db: Session, project: models.Project, manuscript: models.Manuscript) -> dict[str, str]:
    given = dict(manuscript.statements)
    out = {key: value for key, value in given.items() if value}
    registrations = db.scalars(
        select(models.ProtocolRegistration).where(models.ProtocolRegistration.project_id == project.id)
    ).all()
    if "registration" not in out and registrations:
        out["registration"] = "; ".join(
            f"Not registered: {r.waiver_reason}"
            if r.status == "waived"
            else f"{r.registry_name} {r.registration_id}".strip()
            for r in registrations
        )
    if "competing_interests" not in out and any(a.competing_interests for a in manuscript.authors):
        out["competing_interests"] = " ".join(
            f"{a.name}: {a.competing_interests}" for a in manuscript.authors if a.competing_interests
        )
    if manuscript.authors and any(a.credit_roles for a in manuscript.authors):
        out["author_contributions"] = "; ".join(
            f"{a.name}: {', '.join(a.credit_roles)}" for a in manuscript.authors if a.credit_roles
        )
    ai_section = next((s for s in manuscript.sections if s.key == "ai_use"), None)
    if ai_section and ai_section.content.strip():
        out["ai_use"] = plain(ai_section.content).strip()
    deposits = db.scalars(
        select(models.RepositoryDeposit).where(
            models.RepositoryDeposit.project_id == project.id, models.RepositoryDeposit.status == "published"
        )
    ).all()
    if "data_availability" not in out and deposits:
        out["data_availability"] = (
            "Data, code, and other materials are available at "
            + "; ".join(f"https://doi.org/{d.doi}" if d.doi else d.url for d in deposits)
            + "."
        )
    return out


def _check(key: str, label: str, passed: bool | None, detail: str, severity: str = "error") -> dict[str, Any]:
    status = "not_applicable" if passed is None else ("pass" if passed else "fail")
    return {"key": key, "label": label, "status": status, "detail": detail, "severity": severity}


def _requirement(requirements: Mapping[str, Any], name: str) -> str | None:
    entry = requirements.get(name)
    if not entry or not entry.get("grounded") and entry.get("source") != "reviewer":
        return None
    return str(entry.get("value") or "") or None


def readiness(
    db: Session,
    project: models.Project,
    manuscript: models.Manuscript,
    guideline: models.JournalGuideline | None,
    package: models.SubmissionPackage | None,
) -> list[dict[str, Any]]:
    requirements = guideline.requirements if guideline else {}
    sections = {s.key: s.content for s in manuscript.sections}
    approvals = approval_state(db, manuscript)
    cited = cited_ids(manuscript)
    refs = [r for r in references(db, project.id) if r.id in cited]
    checks = [
        _check(
            "approval",
            "Every author approved the current manuscript version",
            approvals["all_approved"],
            "Create a version and collect every author's approval" if not approvals["all_approved"] else "",
        ),
        _check(
            "claims", "Every sentence verified or acknowledged", verify(db, project, manuscript)["unresolved"] == 0, ""
        ),
        _check(
            "retractions",
            "No cited reference is retracted",
            not any(r.retracted for r in refs),
            ", ".join(str(r.id) for r in refs if r.retracted),
        ),
        _check(
            "references_verified",
            "Cited references verified against Crossref or PubMed",
            all(r.verification_status == "verified" for r in refs),
            f"{sum(1 for r in refs if r.verification_status != 'verified')} not verified",
            "warning",
        ),
        _check("prisma", "PRISMA 2020 checklist complete", complete(prisma_checklist(db, project, manuscript)), ""),
    ]
    main_words = sum(word_count(sections.get(k, "")) for k in MAIN_SECTIONS)
    limit = first_int(_requirement(requirements, "word_limit") or "")
    checks.append(
        _check(
            "word_limit",
            f"Main text within the word limit ({main_words} words{f' of {limit}' if limit else ''})",
            None if limit is None else main_words <= limit,
            "",
        )
    )
    abstract_words = word_count(sections.get("abstract", ""))
    abstract_limit = first_int(_requirement(requirements, "abstract_word_limit") or "")
    checks.append(
        _check(
            "abstract_word_limit",
            f"Abstract within its word limit ({abstract_words} "
            f"words{f' of {abstract_limit}' if abstract_limit else ''})",
            None if abstract_limit is None else abstract_words <= abstract_limit,
            "",
        )
    )
    structure = _requirement(requirements, "abstract_structure")
    if structure:
        wanted = [h.strip().casefold() for h in re.split(r"[,;]", structure) if h.strip() and len(h.strip()) < 40]
        headings = [
            line.lstrip("#").strip().casefold()
            for line in sections.get("abstract", "").splitlines()
            if line.lstrip().startswith("#")
        ]
        missing = [h for h in wanted if not any(h in heading for heading in headings)]
        checks.append(
            _check(
                "abstract_structure",
                "Abstract uses the journal's headings",
                not missing,
                f"Missing: {', '.join(missing)}" if missing else "",
                "warning",
            )
        )
    max_refs = first_int(_requirement(requirements, "max_references") or "")
    checks.append(
        _check(
            "max_references",
            f"Reference count within the limit ({len(refs)} cited)",
            None if max_refs is None else len(refs) <= max_refs,
            "",
        )
    )
    all_content = "\n".join(sections.values())
    embedded = embeds(all_content)
    table_count = sum(1 for kind, _ in embedded if kind == "table")
    figure_keys = [key for kind, key in embedded if kind == "figure"]
    for name, count, label in (("max_tables", table_count, "tables"), ("max_figures", len(figure_keys), "figures")):
        maximum = first_int(_requirement(requirements, name) or "")
        checks.append(
            _check(
                name, f"Number of {label} within the limit ({count})", None if maximum is None else count <= maximum, ""
            )
        )
    formats = _requirement(requirements, "figure_formats")
    if formats and figure_keys:
        wanted_formats = {
            f
            for f in ("tiff", "eps", "pdf", "png", "jpeg", "svg")
            if f in formats.casefold() or (f == "tiff" and "tif" in formats.casefold())
        }
        missing_formats: list[str] = []
        for key in figure_keys:
            parts = key.split(":")
            available = {"svg", "png", "pdf"} if parts[0] in ("prisma", "robvis") else set()
            if parts[0] == "run" and len(parts) == 3 and parts[1].isdigit():
                run = db.get(models.AnalysisRun, int(parts[1]))
                available = {p["format"] for p in (run.plots if run else []) if p["name"] == parts[2]}
            if wanted_formats and not wanted_formats & available:
                missing_formats.append(key)
        checks.append(
            _check(
                "figure_formats",
                f"Figures available in an accepted format ({formats})",
                not missing_formats,
                f"Not available for: {', '.join(missing_formats)}" if missing_formats else "",
                "warning",
            )
        )
    dpi = first_int(_requirement(requirements, "figure_resolution_dpi") or "")
    if dpi:
        checks.append(
            _check(
                "figure_resolution",
                f"Figure resolution at least {dpi} dpi (PNG 300 dpi, TIFF 600 dpi, SVG and PDF are vector)",
                dpi <= 600,
                "",
                "warning",
            )
        )
    keywords = _requirement(requirements, "keywords")
    if keywords:
        numbers = [int(n) for n in re.findall(r"\d+", keywords)]
        low, high = (min(numbers), max(numbers)) if numbers else (1, 100)
        count = len(manuscript.keywords)
        checks.append(
            _check(
                "keywords",
                f"Keywords ({count}) as required: {keywords}",
                low <= count <= high if len(numbers) > 1 else count >= 1 and (not numbers or count <= numbers[0]),
                "",
                "warning",
            )
        )
    if _requirement(requirements, "highlights"):
        items = (manuscript.extras.get("highlights") or {}).get("items") or []
        checks.append(_check("highlights", "Highlights written", bool(items), ""))
    if package is not None:
        checks.append(
            _check(
                "cover_letter",
                "Cover letter written",
                bool(package.cover_letter.strip()),
                "",
                "error" if _requirement(requirements, "cover_letter") else "warning",
            )
        )
    given = statements(db, project, manuscript)
    required = set(GENERAL_STATEMENTS)
    required_text = (_requirement(requirements, "required_statements") or "").casefold()
    for key, words in (
        ("ethics", "ethic"),
        ("author_contributions", "contribution"),
        ("ai_use", "artificial intelligence"),
        ("acknowledgements", "acknowledg"),
    ):
        if words in required_text:
            required.add(key)
    missing_statements = [STATEMENT_LABELS[k] for k in sorted(required) if not given.get(k)]
    checks.append(
        _check(
            "statements",
            "Required statements present",
            not missing_statements,
            f"Missing: {', '.join(missing_statements)}" if missing_statements else "",
        )
    )
    authors = manuscript.authors
    corresponding = [a for a in authors if a.corresponding]
    checks.append(
        _check(
            "authors",
            "Author details complete (affiliations, corresponding author's email)",
            bool(authors)
            and all(a.affiliation for a in authors)
            and bool(corresponding)
            and bool(corresponding[0].email),
            "",
            "warning",
        )
    )
    return checks


def blocking(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in checks if c["status"] == "fail" and c["severity"] == "error"]


def _docx(paragraphs: list[tuple[str, str]]) -> bytes:
    document = styled_document()
    for style, text in paragraphs:
        if style.startswith("h"):
            document.add_heading(text, level=int(style[1:]))
        else:
            document.add_paragraph(text)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _table_csv(header: list[str], rows: list[list[str]]) -> bytes:
    import csv

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode()


async def supplementary_files(
    db: Session, project: models.Project, manuscript: models.Manuscript | None
) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    strategies = db.scalars(
        select(models.SearchStrategy)
        .where(models.SearchStrategy.project_id == project.id)
        .order_by(models.SearchStrategy.id)
    ).all()
    files["supplements/search_strategies.md"] = (
        "# Search strategies\n\n"
        + "\n\n".join(f"## {s.database} (version {s.version})\n\n```\n{s.query}\n```" for s in strategies)
        + "\n"
    ).encode()
    searches = table(db, project, "searches")
    if searches:
        files["supplements/searches.csv"] = _table_csv(searches.header, searches.rows)
    flow = flow_counts(db, project.id)
    files["supplements/prisma_flow.csv"] = prisma_csv(flow).encode()
    files["supplements/prisma_flow.svg"] = to_svg(flow).encode()
    content, source, _ = extraction_source(db, project.id)
    metadata = {"project": project.title, "source": source}
    files["data/extraction_dataset.csv"] = to_csv(content)
    files["data/extraction_dataset.json"] = to_json(content, metadata)
    files["data/extraction_dataset.xlsx"] = to_xlsx(content, metadata)
    storage = document_storage()
    for analysis, run in approved_analyses(db, project.id):
        plots = {f"plots/{p['name']}.{p['format']}": storage.read(p["storage_key"]) for p in run.plots}
        files[f"analyses/analysis-{analysis.id}-run-{run.id}.zip"] = export_bundle(
            bundle_files(run, analysis.title), plots
        )
    title, blocks = protocol_document(db, project)
    files["supplements/protocol.docx"] = to_docx(title, blocks)
    if manuscript is not None:
        context = checklist_context(db, project, manuscript)
        for key, checklist in CHECKLISTS.items():
            if not applicable(checklist, context):
                continue
            items = evaluate(checklist, manuscript, context, {})
            files[f"checklists/{key}.csv"] = _table_csv(
                ["Item", "Topic", "Status", "Location"],
                [[i["item_id"], i["topic"], i["status"], i["location"]] for i in items],
            )
    return files


def manifest(files: Mapping[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": path, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for path, content in sorted(files.items())
    ]


def zipped(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(files.items()):
            archive.writestr(path, content)
    return buffer.getvalue()


async def build_submission_files(
    db: Session, project: models.Project, manuscript: models.Manuscript, package: models.SubmissionPackage
) -> tuple[dict[str, bytes], list[str]]:
    files: dict[str, bytes] = {}
    notes: list[str] = []
    for fmt, path in (
        ("docx", "manuscript/manuscript.docx"),
        ("pdf", "manuscript/manuscript.pdf"),
        ("tex", "manuscript/manuscript-latex.zip"),
    ):
        try:
            content, _, _ = await export_manuscript(db, project, manuscript, fmt)
            files[path] = content
        except ExportUnavailable as exc:
            notes.append(f"{fmt.upper()} not included: {exc}")
    sections = {s.key: s for s in manuscript.sections}
    corresponding = next((a for a in manuscript.authors if a.corresponding), None)
    title_page = [("h1", manuscript.title)]
    title_page += [
        ("p", f"{a.name}{f', {a.affiliation}' if a.affiliation else ''}{f' (ORCID {a.orcid})' if a.orcid else ''}")
        for a in manuscript.authors
    ]
    if corresponding:
        title_page.append(
            (
                "p",
                "Corresponding author: "
                f"{corresponding.name}{f', {corresponding.email}' if corresponding.email else ''}",
            )
        )
    title_page += [
        ("p", f"Keywords: {', '.join(manuscript.keywords)}"),
        (
            "p",
            f"Word count (main text): {sum(word_count(sections[k].content) for k in MAIN_SECTIONS if k in sections)}",
        ),
    ]
    files["title_page.docx"] = _docx(title_page)
    if "abstract" in sections:
        files["abstract.txt"] = plain(sections["abstract"].content).encode()
    files["keywords.txt"] = "\n".join(manuscript.keywords).encode()
    for kind in ("highlights", "graphical_abstract"):
        items = (manuscript.extras.get(kind) or {}).get("items") or []
        if items:
            files[f"{kind}.txt"] = "\n".join(items).encode()
    if package.cover_letter.strip():
        files["cover_letter.docx"] = _docx([("p", paragraph) for paragraph in package.cover_letter.split("\n\n")])
    given = statements(db, project, manuscript)
    files["statements.md"] = (
        "\n\n".join(f"## {STATEMENT_LABELS.get(k, k)}\n\n{v}" for k, v in given.items()) + "\n"
    ).encode()
    all_content = "\n".join(s.content for s in manuscript.sections)
    table_number = figure_number = 0
    for kind, key in embeds(all_content):
        if kind == "table":
            item = table(db, project, key)
            if item:
                table_number += 1
                files[f"tables/table-{table_number}-{key}.csv"] = _table_csv(item.header, item.rows)
        else:
            picture = await figure(db, project, key)
            if picture:
                figure_number += 1
                for fmt, content in picture.files.items():
                    files[f"figures/figure-{figure_number}.{fmt}"] = content
    files.update(await supplementary_files(db, project, manuscript))
    readme = [f"# Submission package: {manuscript.title}", "", f"Journal: {package.journal_name or 'not chosen'}", ""]
    readme += [
        "OmniReview never submits manuscripts. The corresponding author submits these files on the journal's system.",
        "",
    ]
    readme += [f"- {path}" for path in sorted(files)]
    readme += ["", *[f"Note: {n}" for n in notes]]
    files["README.md"] = "\n".join(readme).encode()
    return files, notes


def latest_package(db: Session, project_id: int) -> models.SubmissionPackage | None:
    return db.scalar(
        select(models.SubmissionPackage)
        .where(models.SubmissionPackage.project_id == project_id)
        .order_by(models.SubmissionPackage.id.desc())
        .limit(1)
    )


def submission_requirements(db: Session, project: models.Project) -> list[tuple[str, bool]]:
    manuscript = get_manuscript(db, project.id)
    package = latest_package(db, project.id)
    version = latest_version(manuscript) if manuscript else None
    built = (
        package is not None
        and manuscript is not None
        and package.status in ("built", "confirmed")
        and version is not None
        and package.manuscript_version_id == version.id
        and approval_state(db, manuscript)["all_approved"]
    )
    return [
        ("A submission package built from the approved manuscript version", built),
        (
            "Every readiness check passes",
            package is not None and bool(package.readiness) and not blocking(package.readiness),
        ),
        ("The corresponding author confirmed the package", package is not None and package.status == "confirmed"),
    ]


def submission_snapshot(db: Session, project: models.Project) -> dict[str, Any]:
    package = latest_package(db, project.id)
    deposits = db.scalars(
        select(models.RepositoryDeposit).where(models.RepositoryDeposit.project_id == project.id)
    ).all()
    return {
        "package": {
            "id": package.id,
            "journal": package.journal_name,
            "manuscript_version_id": package.manuscript_version_id,
            "sha256": package.sha256,
            "files": package.files,
            "readiness": package.readiness,
            "confirmed_by_id": package.confirmed_by_id,
        }
        if package
        else None,
        "deposits": [{"target": d.target, "status": d.status, "doi": d.doi, "url": d.url} for d in deposits],
    }


def datacite_metadata(project: models.Project, manuscript: models.Manuscript | None, version: str) -> dict[str, Any]:
    creators = [
        {
            "name": a.name,
            "nameType": "Personal",
            "affiliation": [a.affiliation] if a.affiliation else [],
            **(
                {
                    "nameIdentifiers": [
                        {"nameIdentifier": f"https://orcid.org/{a.orcid}", "nameIdentifierScheme": "ORCID"}
                    ]
                }
                if a.orcid
                else {}
            ),
        }
        for a in (manuscript.authors if manuscript else [])
    ]
    title = manuscript.title if manuscript else project.title
    return {
        "types": {"resourceTypeGeneral": "Dataset", "resourceType": "Systematic review data and code"},
        "creators": creators,
        "titles": [{"title": f"{title}: data, code, and materials"}],
        "publicationYear": str(models.utcnow().year),
        "subjects": [{"subject": k} for k in (manuscript.keywords if manuscript else [])],
        "rightsList": [
            {
                "rights": "Creative Commons Attribution 4.0 International",
                "rightsIdentifier": "CC-BY-4.0",
                "rightsUri": "https://creativecommons.org/licenses/by/4.0/",
            }
        ],
        "descriptions": [
            {
                "description": (
                    "Search strategies, the locked extraction data set, analysis scripts and results, and reporting "
                    f"checklists for {title}."
                ),
                "descriptionType": "Abstract",
            }
        ],
        "version": version,
    }


async def fair_package(
    db: Session, project: models.Project, version_label: str, include_manuscript: bool
) -> dict[str, bytes]:
    """Files for a repository deposit with a README, data dictionary, licence, and DataCite metadata."""
    manuscript = get_manuscript(db, project.id)
    files = await supplementary_files(db, project, manuscript)
    if include_manuscript and manuscript is not None and approval_state(db, manuscript)["all_approved"]:
        try:
            document, _, _ = await export_manuscript(db, project, manuscript, "docx")
            files["manuscript/manuscript.docx"] = document
        except ExportUnavailable:
            pass
    content, _, _ = extraction_source(db, project.id)
    dictionary = [["field", "section", "type", "unit", "per_arm", "outcome", "description"]]
    dictionary += [
        [
            f["name"],
            f.get("section", ""),
            f["field_type"],
            f.get("unit", ""),
            str(f["per_arm"]),
            f.get("outcome", ""),
            f.get("help_text", ""),
        ]
        for f in content["fields"]
    ]
    files["data/data_dictionary.csv"] = _table_csv(dictionary[0], dictionary[1:])
    title = manuscript.title if manuscript else project.title
    files["LICENSE.txt"] = (
        b"Creative Commons Attribution 4.0 International (CC BY 4.0)\nhttps://creativecommons.org/licenses/by/4.0/legalcode\n\n"
        b"You may share and adapt these materials for any purpose, provided you give appropriate credit.\n"
    )
    files["datacite.json"] = json.dumps(datacite_metadata(project, manuscript, version_label), indent=2).encode()
    readme = [
        f"# {title}: data, code, and materials",
        "",
        f"Version {version_label}. Exported from OmniReview on {models.utcnow().date().isoformat()}.",
        "",
        "## Contents",
        "",
        *[f"- `{path}`" for path in sorted(files)],
        "",
        "## Reuse",
        "",
        "Each analysis bundle in `analyses/` reruns in R with `Rscript --vanilla analysis.R`. The extraction data "
        "set is "
        "described in `data/data_dictionary.csv`. Licensed CC BY 4.0 (see LICENSE.txt); cite the review when reusing.",
    ]
    files["README.md"] = "\n".join(readme).encode()
    return files
