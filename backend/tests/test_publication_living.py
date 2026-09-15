"""From a completed evidence synthesis to a verified manuscript, a confirmed submission package, deposits, peer review,
and a living review with surveillance, impact assessment, releases, and a living update, through the API. Needs R.
Network services (Crossref, repositories, search connectors) are replaced with fakes."""

import io
import json
import zipfile

import pytest
from test_evidence_synthesis import OUTCOME, open_appraisal, requires_r
from workflow_helpers import add_member, appraise, complete_stage, create_project, url, workflow

import manuscript_routes
import publication_routes
import publication_state
import search_sources
import surveillance
from search_sources import Connector
from services.reference_checks import CheckResult
from services.repositories import DepositResult

pytestmark = requires_r

GUIDELINES = (
    "Instructions for authors. Research articles report original work. The main text must not exceed 4,000 words, "
    "excluding the abstract, tables, figure legends, and references. Systematic reviews must follow the PRISMA 2020 "
    "statement and include a completed checklist as a supplementary file."
)


@pytest.fixture
def project(client, auth_headers):
    return create_project(client, auth_headers), auth_headers


def complete_certainty(client, project_id, headers, make_user, fake_provider):
    studies, fields, arms = open_appraisal(client, project_id, headers, fake_provider)
    for study in studies:
        appraise(client, project_id, headers, study["id"])
    complete_stage(client, project_id, headers, "appraisal")
    pairs = [{"study_id": sid, "treatment_arm_id": a, "control_arm_id": p} for sid, (a, p) in arms.items()]
    spec = {
        "field_id": fields[OUTCOME],
        "measure": "RR",
        "arms": pairs,
        "publication_bias": {"funnel": True},
        "plot_formats": ["svg", "png"],
    }
    body = {
        "title": "Aspirin versus placebo",
        "outcome": OUTCOME,
        "analysis_type": "pairwise",
        "spec": spec,
        "prespecified": True,
        "plan_reference": OUTCOME,
    }
    analysis = client.post(url(project_id, "analyses"), json=body, headers=headers).json()
    statistician = add_member(client, project_id, headers, make_user, "statistician")
    note = {"note": "Random effects suit these two trials."}
    assert (
        client.post(url(project_id, f"analyses/{analysis['id']}/approve"), json=note, headers=statistician).status_code
        == 200
    )
    run = client.post(url(project_id, f"analyses/{analysis['id']}/runs"), headers=statistician).json()["run"]
    assert run["is_final"], run["error"]
    complete_stage(client, project_id, headers, "synthesis")
    ratings = {
        k: {"rating": 0} for k in ("risk_of_bias", "inconsistency", "indirectness", "imprecision", "publication_bias")
    }
    grade_body = {
        "outcome": OUTCOME,
        "analysis_id": analysis["id"],
        "domains": ratings,
        "baseline_risks": [{"label": "Moderate risk", "risk": 0.15}],
    }
    grade = client.post(url(project_id, "grade"), json=grade_body, headers=headers).json()
    signed = client.post(
        url(project_id, f"grade/{grade['id']}/sign-off"),
        json={"note": "Reviewed against the GRADE handbook."},
        headers=headers,
    )
    assert signed.status_code == 200, signed.text
    complete_stage(client, project_id, headers, "certainty")
    return studies, analysis


def unresolved(client, project_id, headers):
    report = client.get(url(project_id, "manuscript/verification"), headers=headers).json()
    return [
        (s["key"], c["status"], c["text"])
        for s in report["sections"]
        for c in s["sentences"]
        if c["status"] != "verified" and not c["acknowledged"]
    ]


def test_manuscript_submission_publication_and_living_review(client, project, make_user, fake_provider, monkeypatch):
    project_id, headers = project
    studies, analysis = complete_certainty(client, project_id, headers, make_user, fake_provider)
    monkeypatch.setattr(
        manuscript_routes,
        "check_reference",
        lambda csl, doi, pmid: CheckResult("verified", {"title_similarity": 100.0}),
    )

    # W12: the manuscript starts from the locked evidence base, with generated sections that verify.
    created = client.post(url(project_id, "manuscript"), json={"citation_style": "vancouver"}, headers=headers)
    assert created.status_code == 201, created.text
    manuscript = created.json()
    assert [s["key"] for s in manuscript["sections"]] == [
        "abstract",
        "introduction",
        "methods",
        "results",
        "discussion",
        "conclusions",
        "other_information",
        "ai_use",
    ]
    methods = next(s["content"] for s in manuscript["sections"] if s["key"] == "methods")
    results = next(s["content"] for s in manuscript["sections"] if s["key"] == "results")
    assert "[#protocol]" in methods and "[[figure:prisma]]" in results and "[[table:sof]]" in results
    placeholders = unresolved(client, project_id, headers)
    assert {(key, status) for key, status, _ in placeholders} == {("other_information", "unsupported")}, placeholders
    assert len(placeholders) == 3

    statements = {
        "funding": "No specific funding was received.",
        "competing_interests": "The authors declare no competing interests.",
        "data_availability": "All data and code are deposited on Zenodo.",
    }
    patch = {
        "title": manuscript["title"],
        "citation_style": "vancouver",
        "keywords": ["aspirin", "myocardial infarction", "meta-analysis"],
        "statements": statements,
    }
    assert client.patch(url(project_id, "manuscript"), json=patch, headers=headers).status_code == 200
    suggestion = client.post(
        url(project_id, "manuscript/sections/other_information/regenerate"), headers=headers
    ).json()
    assert suggestion["verification"]["unverified"] == 0
    assert (
        client.post(
            url(project_id, f"manuscript/suggestions/{suggestion['id']}/accept"), json={}, headers=headers
        ).status_code
        == 200
    )
    assert unresolved(client, project_id, headers) == []

    references = client.get(url(project_id, "manuscript/references"), headers=headers).json()["references"]
    first_ref = references[0]["id"]
    intro = (
        f"## Rationale\n\nAspirin is widely used to prevent heart attacks [@{first_ref}].\n\n"
        "We asked whether aspirin prevents heart attacks.\n"
    )
    assert (
        client.put(
            url(project_id, "manuscript/sections/introduction"), json={"content": intro}, headers=headers
        ).status_code
        == 200
    )
    [(_, status, sentence)] = unresolved(client, project_id, headers)
    assert status == "unsupported"
    report = client.get(url(project_id, "manuscript/verification"), headers=headers).json()
    target = next(c for s in report["sections"] for c in s["sentences"] if c["text"] == sentence)
    wrong = client.put(
        url(project_id, "manuscript/sections/results"),
        json={"content": results.replace("[#prisma]", "[#analysis:999]", 1)},
        headers=headers,
    )
    assert wrong.status_code == 200 and any(s == "invalid_link" for _, s, _ in unresolved(client, project_id, headers))
    assert (
        client.put(
            url(project_id, "manuscript/sections/results"), json={"content": results}, headers=headers
        ).status_code
        == 200
    )
    ack = {
        "section_key": "introduction",
        "sentence_hash": target["hash"],
        "note": "The review question, stated by the authors.",
    }
    assert (
        client.post(url(project_id, "manuscript/claims/acknowledgements"), json=ack, headers=headers).json()[
            "unresolved"
        ]
        == 0
    )

    checked = client.post(url(project_id, "manuscript/references/check"), json={}, headers=headers).json()
    assert all(r["verification_status"] == "verified" for r in checked["references"])
    assert len(checked["bibliography"]) == 2
    bibtex = client.get(url(project_id, "manuscript/references/export?format=bibtex"), headers=headers)
    assert "@article{ref" in bibtex.text

    checklists = {c["key"]: c for c in client.get(url(project_id, "manuscript/checklists"), headers=headers).json()}
    prisma = {i["item_id"]: i for i in checklists["prisma_2020"]["items"]}
    assert (prisma["6"]["status"], prisma["16a"]["status"], prisma["3"]["status"]) == (
        "reported",
        "reported",
        "partially_reported",
    ) or prisma["6"]["status"] == "reported"
    assert checklists["prisma_nma"]["applicable"] is False and checklists["ai_use"]["applicable"] is True
    overrides = [
        {"item_id": i["item_id"], "status": "reported", "location": "Discussion"}
        for i in prisma.values()
        if i["status"] not in ("reported", "not_applicable")
    ]
    updated = client.put(
        url(project_id, "manuscript/checklists/prisma_2020"), json={"items": overrides}, headers=headers
    ).json()
    assert updated["complete"] is True

    assert client.get(url(project_id, "manuscript/export?format=md"), headers=headers).status_code == 409
    owner_id = client.get(url(project_id, "members"), headers=headers).json()[0]["user_id"]
    authors = [
        {
            "user_id": owner_id,
            "name": "Olive Owner",
            "email": "olive@example.org",
            "affiliation": "University of Testing",
            "corresponding": True,
            "credit_roles": ["Conceptualization", "Writing – original draft"],
        },
        {"name": "Eve External", "affiliation": "Teaching Hospital", "credit_roles": ["Formal analysis"]},
    ]
    saved = client.put(url(project_id, "manuscript/authors"), json={"authors": authors}, headers=headers).json()
    external_id = saved["authors"][1]["id"]
    version = client.post(url(project_id, "manuscript/versions"), json={"note": "For author approval"}, headers=headers)
    assert version.status_code == 201, version.text
    version_id = version.json()["versions"][-1]["id"]
    assert (
        client.post(url(project_id, f"manuscript/versions/{version_id}/approve"), json={}, headers=headers).status_code
        == 200
    )
    external = {"author_id": external_id, "note": "Approved by email on 15 September 2026."}
    approvals = client.post(
        url(project_id, f"manuscript/versions/{version_id}/approve"), json=external, headers=headers
    ).json()
    assert approvals["all_approved"] is True

    exported = client.get(url(project_id, "manuscript/export?format=md"), headers=headers)
    assert exported.status_code == 200, exported.text
    markdown = zipfile.ZipFile(io.BytesIO(exported.content)).read("manuscript.md").decode()
    assert "# Methods" in markdown and "[#" not in markdown and "# References" in markdown
    docx = client.get(url(project_id, "manuscript/export?format=docx"), headers=headers)
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    assert [
        label
        for label, met in [
            (r["label"], r["met"]) for r in workflow(client, project_id, headers)["manuscript"]["requirements"]
        ]
        if not met
    ] == []
    complete_stage(client, project_id, headers, "manuscript")

    # W13: guidelines, a submission package checked for readiness, confirmation, deposits, and peer review.
    fake_provider(
        json.dumps({"requirements": [{"name": "word_limit", "value": "4000", "quote": "must not exceed 4,000 words"}]})
    )
    guideline = client.post(
        url(project_id, "journal-guidelines"), data={"journal_name": "BMJ Open", "text": GUIDELINES}, headers=headers
    )
    assert guideline.status_code == 201, guideline.text
    assert guideline.json()["requirements"]["word_limit"]["grounded"] is True
    package = client.post(
        url(project_id, "submission-packages"),
        json={"journal_name": "BMJ Open", "guideline_id": guideline.json()["id"]},
        headers=headers,
    ).json()
    assert {c["key"] for c in package["readiness"] if c["status"] == "pass"} >= {
        "approval",
        "claims",
        "prisma",
        "word_limit",
        "statements",
    }

    async def fake_export(db, project, manuscript, fmt):
        return b"PK fake", "application/octet-stream", f"manuscript.{fmt}"

    monkeypatch.setattr(publication_state, "export_manuscript", fake_export)
    built = client.post(url(project_id, f"submission-packages/{package['id']}/build"), headers=headers)
    assert built.status_code == 200, built.text
    paths = {f["path"] for f in built.json()["files"]}
    assert {
        "manuscript/manuscript.docx",
        "title_page.docx",
        "statements.md",
        "supplements/search_strategies.md",
        "data/extraction_dataset.xlsx",
    } <= paths
    assert any(p.startswith("analyses/") for p in paths) and any(p.startswith("figures/") for p in paths)
    assert built.json()["blocking"] == 0, built.json()["readiness"]
    extractor = add_member(client, project_id, headers, make_user, "extractor")
    assert (
        client.post(url(project_id, f"submission-packages/{package['id']}/confirm"), headers=extractor).status_code
        == 403
    )
    assert (
        client.post(url(project_id, f"submission-packages/{package['id']}/confirm"), headers=headers).json()["status"]
        == "confirmed"
    )
    complete_stage(client, project_id, headers, "submission")

    monkeypatch.setattr(
        publication_routes,
        "zenodo_deposit",
        lambda *args: DepositResult(
            "123", "https://sandbox.zenodo.org/deposit/123", "draft", "10.5072/zenodo.123", "122"
        ),
    )
    zenodo = client.post(
        url(project_id, "deposits"), json={"target": "zenodo", "token": "t" * 20, "sandbox": True}, headers=headers
    )
    assert (zenodo.status_code, zenodo.json()["doi"]) == (201, "10.5072/zenodo.123")
    dryad = client.post(url(project_id, "deposits"), json={"target": "dryad"}, headers=headers).json()
    dryad_zip = zipfile.ZipFile(
        io.BytesIO(client.get(url(project_id, f"deposits/{dryad['id']}/package"), headers=headers).content)
    )
    assert {"SUBMISSION_STEPS.md", "README.md", "datacite.json", "LICENSE.txt", "data/data_dictionary.csv"} <= set(
        dryad_zip.namelist()
    )

    review_round = client.post(
        url(project_id, "peer-review/rounds"),
        json={"journal": "BMJ Open", "decision": "Minor revision"},
        headers=headers,
    ).json()
    report = (
        "Reviewer 1\n1. Explain why only two trials were found.\n2. Report the search dates.\n\n"
        "Reviewer 2\n1. Shorten the abstract."
    )
    imported = client.post(
        url(project_id, f"peer-review/rounds/{review_round['id']}/comments/import"),
        json={"text": report},
        headers=headers,
    ).json()
    assert imported["imported"] == 3
    revision = client.get(url(project_id, "manuscript/sections/introduction/revisions"), headers=headers).json()[0][
        "id"
    ]
    comment = imported["comments"][0]
    change = {
        "response": "We added the reasons in the introduction.",
        "status": "addressed",
        "changes": [{"section_key": "introduction", "revision_id": revision}],
    }
    assert (
        client.put(url(project_id, f"peer-review/comments/{comment['id']}"), json=change, headers=headers).json()[
            "status"
        ]
        == "addressed"
    )
    fake_provider("Dear Editor,\n\nThank you for the reviews. [Response needed]")
    letter = client.post(url(project_id, f"peer-review/rounds/{review_round['id']}/response-letter"), headers=headers)
    assert letter.json()["response_letter"].startswith("Dear Editor")

    # W14: surveillance, alerts, AI suggestions, impact assessment, releases, evidence map, and a living update.
    def fake_search(query, limit):
        return [
            {"title": "Aspirin trial A", "doi": "10.1/a"},
            {
                "title": "Aspirin and heart attacks: a large trial",
                "doi": "10.1/c",
                "year": "2026",
                "abstract": "A randomized trial of aspirin in 1,200 participants.",
            },
        ], 2

    monkeypatch.setitem(
        search_sources.CONNECTORS,
        "pubmed",
        Connector("pubmed", "PubMed", "PubMed (test)", "database", fake_search, ("pubmed",), ""),
    )
    strategy_id = client.get(url(project_id, "search-strategies"), headers=headers).json()[0]["id"]
    schedule = client.post(
        url(project_id, "surveillance/schedules"),
        json={"strategy_id": strategy_id, "connector": "pubmed", "frequency_days": 30},
        headers=headers,
    ).json()
    surveillance_run = client.post(
        url(project_id, f"surveillance/schedules/{schedule['id']}/run"), headers=headers
    ).json()
    assert (surveillance_run["status"], surveillance_run["new_candidates"], surveillance_run["duplicates"]) == (
        "succeeded",
        1,
        1,
    )
    [candidate] = client.get(url(project_id, "surveillance/candidates"), headers=headers).json()
    assert candidate["sample_size"] == 1200
    fake_provider(
        json.dumps(
            {"criteria": [], "decision": "Include", "confidence": 0.9, "reasoning": "Adults randomized to aspirin."}
        )
    )
    job = client.post(
        url(project_id, "surveillance/candidates/ai"), json={"candidate_ids": [candidate["id"]]}, headers=headers
    )
    assert job.status_code == 202 and job.json()["status"] == "completed", job.text
    alerts = client.get(url(project_id, "surveillance/alerts"), headers=headers).json()
    assert {"new_records", "large_trial", "new_eligible"} <= {a["kind"] for a in alerts}
    acknowledged = client.put(
        url(project_id, f"surveillance/alerts/{alerts[0]['id']}"),
        json={"status": "acknowledged", "note": "Seen"},
        headers=headers,
    )
    assert acknowledged.json()["status"] == "acknowledged"

    impact = client.post(
        url(project_id, "living/impact"),
        json={
            "analysis_id": analysis["id"],
            "rows": [{"label": "Aspirin trial C", "values": {"ai": 20, "n1i": 600, "ci": 35, "n2i": 600}}],
        },
        headers=headers,
    )
    assert impact.status_code == 201, impact.text
    assert (impact.json()["baseline"]["k"], impact.json()["provisional"]["k"]) == (2, 3)

    release = client.post(url(project_id, "releases"), json={"title": "Version 1"}, headers=headers)
    assert release.status_code == 201, release.text
    assert release.json()["changelog"][0].startswith("First release")
    assert client.post(url(project_id, "releases"), json={"title": "Again"}, headers=headers).status_code == 409
    deposited = client.post(
        url(project_id, f"releases/{release.json()['id']}/deposit"), json={"token": "t" * 20}, headers=headers
    )
    assert deposited.status_code == 201 and deposited.json()["release_id"] == release.json()["id"]

    retracted_doi = "10.1/a"
    monkeypatch.setattr(
        surveillance,
        "check_reference",
        lambda csl, doi, pmid: CheckResult(
            "verified", {}, doi == retracted_doi, {"notices": [{"type": "retraction"}]} if doi == retracted_doi else {}
        ),
    )
    retractions = client.post(url(project_id, "surveillance/retractions/check"), headers=headers).json()
    assert (retractions["retrieved"], retractions["new_candidates"]) == (2, 1)
    assert "retraction" in {
        a["kind"] for a in client.get(url(project_id, "surveillance/alerts"), headers=headers).json()
    }

    evidence_map = client.get(url(project_id, "living/evidence-map"), headers=headers).json()
    assert {"intervention": "Aspirin", "outcome": OUTCOME, "studies": 2, "certainty": "high"} in evidence_map["bubbles"]

    reanalysis = client.post(
        url(project_id, f"peer-review/comments/{imported['comments'][1]['id']}/reanalysis"),
        json={"stage": "submission", "rationale": "Add the search dates to the package."},
        headers=headers,
    )
    assert reanalysis.json()["reanalysis"]["reopened"] == ["submission"]

    assert (
        client.put(
            url(project_id, f"surveillance/candidates/{candidate['id']}"), json={"decision": "promote"}, headers=headers
        ).json()["status"]
        == "promoted"
    )
    update = client.post(
        url(project_id, "living/updates"), json={"rationale": "A large new trial was published."}, headers=headers
    )
    assert update.status_code == 200, update.text
    assert update.json()["imported"] == 1 and update.json()["reopened"][0] == "search"
    assert workflow(client, project_id, headers)["search"]["status"] == "open"
    titles = [r["title"] for r in client.get(url(project_id, "records"), headers=headers).json()]
    assert "Aspirin and heart attacks: a large trial" in titles
