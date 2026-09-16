"""FHIR R5 export in the Evidence-Based Medicine on FHIR (EBMonFHIR) style: Citation resources for the review and its
included studies, EvidenceVariable resources for the question's elements and outcomes, and Evidence resources for each
GRADE outcome with its statistic, confidence interval, sample size, and certainty ratings.

Statistic types are given as text; certainty uses the HL7 certainty-type and certainty-rating code systems. The output
is checked for its basic structure here, not against the official FHIR validator.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import citations
import models
from certainty_routes import _assessments, analysis_for, effect_summary, intervention_name
from extraction_data import included_studies
from grading import DOWNGRADE_DOMAINS, RATIO

CERTAINTY_TYPE = "http://terminology.hl7.org/CodeSystem/certainty-type"
CERTAINTY_RATING = "http://terminology.hl7.org/CodeSystem/certainty-rating"
DOMAIN_CODES = {
    "risk_of_bias": "RiskOfBias",
    "inconsistency": "Inconsistency",
    "indirectness": "Indirectness",
    "imprecision": "Imprecision",
    "publication_bias": "PublicationBias",
}
DOMAIN_RATINGS = {0: "no-concern", -1: "serious-concern", -2: "very-serious-concern"}
OVERALL_RATINGS = {"high": "high", "moderate": "moderate", "low": "low", "very_low": "very-low"}
STATISTIC_NAMES = {
    "RR": "Relative Risk",
    "OR": "Odds Ratio",
    "HR": "Hazard Ratio",
    "RD": "Risk Difference",
    "MD": "Mean Difference",
    "SMD": "Standardized Mean Difference",
    "ROM": "Ratio of Means",
}
ROLE_ELEMENTS = {
    "population": "population",
    "intervention": "exposure",
    "exposure": "exposure",
    "comparator": "referenceExposure",
    "comparison": "referenceExposure",
}


def _id(kind: str, value: object) -> str:
    return f"{kind}-{value}"


def _entry(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "fullUrl": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, resource['resourceType'] + '/' + resource['id'])}",
        "resource": resource,
    }


def citation_resource(resource_id: str, csl: dict[str, Any]) -> dict[str, Any]:
    artifact: dict[str, Any] = {"title": [{"text": csl.get("title", "")}]}
    if csl.get("abstract"):
        artifact["abstract"] = [{"text": csl["abstract"]}]
    identifiers = []
    if csl.get("DOI"):
        identifiers.append({"system": "https://doi.org", "value": csl["DOI"]})
        artifact["webLocation"] = [{"url": f"https://doi.org/{csl['DOI']}"}]
    if csl.get("PMID"):
        identifiers.append({"system": "https://pubmed.ncbi.nlm.nih.gov", "value": str(csl["PMID"])})
    if identifiers:
        artifact["identifier"] = identifiers
    year = citations.year_of(csl)
    form: dict[str, Any] = {}
    if csl.get("container-title"):
        form["publishedIn"] = {"title": csl["container-title"]}
    if year != "n.d.":
        form["articleDate"] = year
    if form:
        artifact["publicationForm"] = [form]
    authors = [f"{a.get('family', '')} {a.get('given', '')}".strip() for a in csl.get("author", [])]
    if authors:
        artifact["contributorship"] = {"summary": [{"value": ", ".join(authors)}]}
    return {"resourceType": "Citation", "id": resource_id, "status": "active", "citedArtifact": artifact}


def build_bundle(db: Session, project: models.Project) -> dict[str, Any]:
    resources: list[dict[str, Any]] = []
    protocol = project.protocol
    manuscript = db.scalar(select(models.Manuscript).where(models.Manuscript.project_id == project.id))
    review_title = manuscript.title if manuscript else project.title
    resources.append(
        {
            "resourceType": "Citation",
            "id": _id("review", project.id),
            "status": "active",
            "title": review_title,
            "citedArtifact": {
                "title": [{"text": review_title}],
                "abstract": [{"text": protocol.question}] if protocol and protocol.question else [],
            },
        }
    )
    for study in included_studies(db, project.id):
        for report in study.reports:
            record = report.record
            csl = citations.csl_from_record(record.id, record)
            csl["abstract"] = record.abstract
            resources.append(citation_resource(_id("study-report", record.id), csl))
    elements = (protocol.question_elements if protocol else None) or {}
    variables: dict[str, str] = {}
    for element, text in elements.items():
        if element in ROLE_ELEMENTS and text:
            resource_id = _id("variable", element)
            variables[element] = resource_id
            resources.append(
                {
                    "resourceType": "EvidenceVariable",
                    "id": resource_id,
                    "status": "active",
                    "title": text,
                    "description": f"{element.capitalize()}: {text}",
                }
            )
    for grade in _assessments(db, project.id):
        outcome_id = _id("outcome", grade.id)
        resources.append(
            {"resourceType": "EvidenceVariable", "id": outcome_id, "status": "active", "title": grade.outcome}
        )
        variable_definition = [
            {
                "variableRole": {"coding": [{"code": ROLE_ELEMENTS[element]}], "text": ROLE_ELEMENTS[element]},
                "observed": {"reference": f"EvidenceVariable/{resource_id}"},
            }
            for element, resource_id in variables.items()
        ]
        variable_definition.append(
            {
                "variableRole": {"coding": [{"code": "measuredVariable"}], "text": "measuredVariable"},
                "observed": {"reference": f"EvidenceVariable/{outcome_id}"},
            }
        )
        evidence: dict[str, Any] = {
            "resourceType": "Evidence",
            "id": _id("evidence", grade.id),
            "status": "active" if grade.status == "signed_off" else "draft",
            "title": f"{intervention_name(project)} and {grade.outcome}",
            "variableDefinition": variable_definition,
            "certainty": [
                {
                    "type": {"coding": [{"system": CERTAINTY_TYPE, "code": "Overall"}]},
                    "rating": {"coding": [{"system": CERTAINTY_RATING, "code": OVERALL_RATINGS[grade.certainty]}]},
                    "subcomponent": [
                        {
                            "type": {"coding": [{"system": CERTAINTY_TYPE, "code": code}]},
                            "rating": {
                                "coding": [
                                    {
                                        "system": CERTAINTY_RATING,
                                        "code": DOMAIN_RATINGS.get(
                                            int((grade.domains.get(key) or {}).get("rating", 0)), "no-concern"
                                        ),
                                    }
                                ]
                            },
                            **(
                                {"note": [{"text": grade.domains[key]["rationale"]}]}
                                if (grade.domains.get(key) or {}).get("rationale")
                                else {}
                            ),
                        }
                        for key, code in DOMAIN_CODES.items()
                        if key in DOWNGRADE_DOMAINS
                    ],
                }
            ],
        }
        analysis, run = analysis_for(db, grade)
        if analysis is not None and run is not None:
            summary = effect_summary(run)
            measure = run.spec.get("measure", "")
            ratio = measure in RATIO
            value = summary.get("exp_estimate") if ratio else summary.get("estimate")
            low = summary.get("exp_ci_lower") if ratio else summary.get("ci_lower")
            high = summary.get("exp_ci_upper") if ratio else summary.get("ci_upper")
            statistic: dict[str, Any] = {
                "description": analysis.title,
                "statisticType": {"text": STATISTIC_NAMES.get(measure, measure or analysis.analysis_type)},
            }
            if isinstance(value, int | float):
                statistic["quantity"] = {"value": round(value, 6)}
            sample: dict[str, Any] = {}
            if summary.get("studies"):
                sample["numberOfStudies"] = summary["studies"]
            if summary.get("participants"):
                sample["numberOfParticipants"] = summary["participants"]
            if sample:
                statistic["sampleSize"] = sample
            if isinstance(low, int | float) and isinstance(high, int | float):
                statistic["attributeEstimate"] = [
                    {
                        "type": {"text": "95% confidence interval"},
                        "level": 0.95,
                        "range": {"low": {"value": round(low, 6)}, "high": {"value": round(high, 6)}},
                    }
                ]
            evidence["statistic"] = [statistic]
        resources.append(evidence)
    bundle = {
        "resourceType": "Bundle",
        "id": _id("review-bundle", project.id),
        "type": "collection",
        "timestamp": models.utcnow().isoformat(),
        "entry": [_entry(r) for r in resources],
    }
    problems = structure_problems(bundle)
    if problems:
        raise ValueError("; ".join(problems))
    return bundle


def structure_problems(bundle: dict[str, Any]) -> list[str]:
    problems = []
    ids = set()
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        kind, resource_id = resource.get("resourceType"), resource.get("id")
        if kind not in ("Citation", "EvidenceVariable", "Evidence") or not resource_id:
            problems.append(f"Unexpected resource {kind}/{resource_id}")
        if (kind, resource_id) in ids:
            problems.append(f"Duplicate resource {kind}/{resource_id}")
        ids.add((kind, resource_id))
        if resource.get("status") not in ("draft", "active", "retired", "unknown"):
            problems.append(f"{kind}/{resource_id} has no valid status")
    for entry in bundle.get("entry", []):
        resource = entry["resource"]
        if resource.get("resourceType") != "Evidence":
            continue
        for definition in resource.get("variableDefinition", []):
            reference = definition["observed"]["reference"].split("/", 1)
            if (reference[0], reference[1]) not in ids:
                problems.append(f"Evidence/{resource['id']} references a missing {reference[0]}")
    return problems
