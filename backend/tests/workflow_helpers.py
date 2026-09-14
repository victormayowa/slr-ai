"""Helpers for driving a project through the review workflow in tests."""

PROTOCOL = {
    "review_type": "Systematic Review",
    "framework": "PICO",
    "description": "Does aspirin prevent heart attacks in adults?",
    "suggested_criteria": "Adults; randomized trials",
    "extraction_outline": "Country\nFollow-up",
    "rob_tool": "ROB-2",
}
GENERATED_PROTOCOL = (
    '{"inclusion_criteria": ["Adults", "Randomized trials"], "exclusion_criteria": ["Children"],'
    ' "boolean_searches": [{"database": "PubMed", "string": "aspirin[tiab]"},'
    ' {"database": "Embase", "string": "aspirin"}]}'
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
    response = client.post(url(project_id, "protocol/generate"), json={"provider": "gemini"}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def decide(client, project_id, headers, record_id, decision):
    response = client.put(
        url(project_id, f"records/{record_id}/decision"), json={"decision": decision}, headers=headers
    )
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


def lock_protocol(client, project_id, headers, fake_provider):
    """Generate a protocol, accept every criterion, and sign off the protocol stage."""
    generated = generate_protocol(client, project_id, headers, fake_provider)
    for kind in ("inclusion", "exclusion"):
        client.post(url(project_id, "criteria/accept-all"), json={"kind": kind}, headers=headers)
    complete_stage(client, project_id, headers, "protocol")
    return generated


def open_screening(client, project_id, headers, fake_provider, records=RECORDS):
    """Lock the protocol, import records, and sign off search, leaving screening open. Returns the records."""
    lock_protocol(client, project_id, headers, fake_provider)
    imported = import_records(client, project_id, headers, records)
    complete_stage(client, project_id, headers, "search")
    return imported


def open_extraction(client, project_id, headers, fake_provider):
    """Screen RECORDS (include the first, exclude the second) and sign off screening. Returns both records."""
    included, excluded = open_screening(client, project_id, headers, fake_provider)
    decide(client, project_id, headers, included["id"], "include")
    decide(client, project_id, headers, excluded["id"], "exclude")
    complete_stage(client, project_id, headers, "screening")
    return included, excluded


def open_synthesis(client, project_id, headers, fake_provider):
    """Run and sign off extraction and appraisal for the included record. Returns it."""
    included, _ = open_extraction(client, project_id, headers, fake_provider)
    body = {"record_ids": [included["id"]], "provider": "gemini"}
    fake_provider('{"Sample Size": 120}')
    assert client.post(url(project_id, "extraction/ai"), json=body, headers=headers).status_code == 200
    complete_stage(client, project_id, headers, "extraction")
    fake_provider('{"D1: Randomization": "Low", "Overall": "Low Risk"}')
    assert client.post(url(project_id, "appraisal/ai"), json=body, headers=headers).status_code == 200
    complete_stage(client, project_id, headers, "appraisal")
    return included
