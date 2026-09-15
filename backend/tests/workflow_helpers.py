"""Helpers for driving a project through the review workflow in tests."""


class ProviderHTTPError(Exception):
    """Stands in for an AI SDK error carrying an HTTP status. Its text includes a fake secret that must never leak."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}: request with key sk-secret-123 failed")
        self.status_code = status_code


PROTOCOL = {
    "review_type": "Systematic Review",
    "framework": "PICO",
    "description": "Does aspirin prevent heart attacks in adults?",
    "suggested_criteria": "Adults; randomized trials",
    "extraction_outline": "Country\nFollow-up",
    "rob_tool": "ROB-2",
}
GENERATED_PROTOCOL = (
    '{"inclusion_criteria": [{"text": "Adults", "element": "population"},'
    ' {"text": "Randomized trials", "element": "study_design"}],'
    ' "exclusion_criteria": [{"text": "Children", "element": "population"}],'
    ' "boolean_searches": [{"database": "PubMed", "string": "aspirin[tiab]"},'
    ' {"database": "Embase", "string": "aspirin"}]}'
)
QUESTION = {
    "framework": "PICO",
    "question": "Does aspirin prevent heart attacks in adults?",
    "elements": {
        "population": "Adults",
        "intervention": "Aspirin",
        "comparator": "Placebo",
        "outcomes": "Heart attacks",
    },
    "finer": {},
}
ANALYSIS_PLAN = {
    "synthesis_approach": "meta_analysis",
    "outcomes": [{"name": "Myocardial infarction", "priority": "primary"}],
}
REQUIRED_SECTIONS = {
    "rationale": "Aspirin may prevent heart attacks, but trials disagree.",
    "objectives": "To assess whether aspirin prevents heart attacks in adults.",
    "synthesis": "Random-effects meta-analysis of risk ratios.",
}
EXTRACTION_REPLY = '{"values": [{"field": "Sample Size", "value": 120, "quote": "Adults randomized to aspirin."}]}'
APPRAISAL_REPLY = (
    '{"domains": [{"domain": "D1: Randomization", "judgment": "Low", "rationale": "Randomized."}],'
    ' "overall": "Low Risk"}'
)
RECORDS = [
    {"title": "Aspirin trial", "doi": "10.1/a", "abstract": "Adults randomized to aspirin."},
    {"title": "Statin trial", "abstract": "Adults randomized to statins."},
]


def url(project_id, suffix=""):
    return f"/api/projects/{project_id}/{suffix}"


def create_project(client, headers, title="Aspirin review"):
    response = client.post("/api/projects", json={"title": title}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def add_member(client, project_id, owner_headers, make_user, role):
    email, headers = make_user()
    response = client.post(url(project_id, "members"), json={"email": email, "role": role}, headers=owner_headers)
    assert response.status_code == 201, response.text
    return headers


def import_records(client, project_id, headers, records=RECORDS, file_name="export.csv"):
    response = client.post(
        url(project_id, "imports"), json={"file_name": file_name, "records": records}, headers=headers
    )
    assert response.status_code == 201, response.text
    return client.get(url(project_id, "records"), headers=headers).json()


def generate_protocol(client, project_id, headers, fake_provider):
    assert client.put(url(project_id, "protocol"), json=PROTOCOL, headers=headers).status_code == 200
    fake_provider(GENERATED_PROTOCOL)
    response = client.post(url(project_id, "protocol/generate"), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def decide(client, project_id, headers, record_id, decision, stage="title_abstract", reason_code=None):
    body = {"decision": decision, "stage": stage}
    if reason_code:
        body["reason_code"] = reason_code
    response = client.put(url(project_id, f"records/{record_id}/decision"), json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def workflow(client, project_id, headers):
    response = client.get(url(project_id, "workflow"), headers=headers)
    assert response.status_code == 200, response.text
    return {stage["stage"]: stage for stage in response.json()}


def complete_stage(client, project_id, headers, stage, note="Reviewed and approved"):
    response = client.post(url(project_id, f"workflow/{stage}/complete"), json={"note": note}, headers=headers)
    assert response.status_code == 200, response.text
    return {item["stage"]: item for item in response.json()}


def design_protocol(client, project_id, headers):
    """Write the review question, analysis plan, and required protocol sections."""
    assert client.put(url(project_id, "question"), json=QUESTION, headers=headers).status_code == 200
    assert client.put(url(project_id, "analysis-plan"), json=ANALYSIS_PLAN, headers=headers).status_code == 200
    for key, content in REQUIRED_SECTIONS.items():
        response = client.put(url(project_id, f"protocol-sections/{key}"), json={"content": content}, headers=headers)
        assert response.status_code == 200, response.text


def lock_protocol(client, project_id, headers, fake_provider):
    """Generate, design, and sign off a protocol with every criterion accepted; waive registration and PRESS."""
    generated = generate_protocol(client, project_id, headers, fake_provider)
    for kind in ("inclusion", "exclusion"):
        client.post(url(project_id, "criteria/accept-all"), json={"kind": kind}, headers=headers)
    design_protocol(client, project_id, headers)
    complete_stage(client, project_id, headers, "protocol")
    waiver = {"reason": "Teaching exercise; the protocol won't be registered."}
    assert client.post(url(project_id, "registrations/waiver"), json=waiver, headers=headers).status_code == 201
    press_waiver = {"reason": "Teaching exercise; no second searcher is available."}
    assert client.post(url(project_id, "press-waiver"), json=press_waiver, headers=headers).status_code == 201
    return generated


def open_screening(client, project_id, headers, fake_provider, records=RECORDS):
    """Lock the protocol, import records, and sign off search, leaving screening open. Returns the records."""
    lock_protocol(client, project_id, headers, fake_provider)
    imported = import_records(client, project_id, headers, records)
    complete_stage(client, project_id, headers, "search")
    return imported


def open_full_text_screening(client, project_id, headers, fake_provider):
    """Screen RECORDS at title and abstract (include the first, exclude the second) and sign off. Returns both."""
    included, excluded = open_screening(client, project_id, headers, fake_provider)
    decide(client, project_id, headers, included["id"], "include")
    decide(client, project_id, headers, excluded["id"], "exclude")
    complete_stage(client, project_id, headers, "screening")
    return included, excluded


def open_extraction(client, project_id, headers, fake_provider):
    """Include the first of RECORDS at title/abstract and full text, and sign off both stages. Returns both records."""
    included, excluded = open_full_text_screening(client, project_id, headers, fake_provider)
    decide(client, project_id, headers, included["id"], "include", stage="full_text")
    complete_stage(client, project_id, headers, "full_text_screening")
    return included, excluded


def form_fields(client, project_id, headers):
    response = client.get(url(project_id, "extraction/form"), headers=headers)
    assert response.status_code == 200, response.text
    return {field["name"]: field for field in response.json()["fields"]}


def extract(client, project_id, headers, study_id, field_id, value=None, **extra):
    body = {"field_id": field_id, "value": value, **extra}
    response = client.put(url(project_id, f"studies/{study_id}/extraction/values"), json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def open_synthesis(client, project_id, headers, fake_provider):
    """Extract a value for the included study, run appraisal, and sign off both stages. Returns the included record."""
    included, _ = open_extraction(client, project_id, headers, fake_provider)
    study = client.get(url(project_id, "studies"), headers=headers).json()[0]
    field = form_fields(client, project_id, headers)["Sample Size"]
    extract(client, project_id, headers, study["id"], field["id"], {"text": "120"})
    complete_stage(client, project_id, headers, "extraction")
    body = {"record_ids": [included["id"]]}
    fake_provider(APPRAISAL_REPLY)
    run_ai(client, project_id, headers, "appraisal/ai", body)
    complete_stage(client, project_id, headers, "appraisal")
    return included


def run_ai(client, project_id, headers, endpoint, body):
    """Start an AI job (tests run it as soon as it's queued), check it completed, and return its records in order."""
    response = client.post(url(project_id, endpoint), json=body, headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "completed", response.json()
    records = {record["id"]: record for record in client.get(url(project_id, "records"), headers=headers).json()}
    return [records[record_id] for record_id in body["record_ids"]]
