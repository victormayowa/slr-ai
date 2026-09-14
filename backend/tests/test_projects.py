import pytest

from permissions import Permission, ProjectRole, has_permission


def create_project(client, headers, **fields):
    response = client.post("/api/projects", json={"title": "Aspirin review", **fields}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def add_member(client, project_id, headers, email, role):
    return client.post(f"/api/projects/{project_id}/members", json={"email": email, "role": role}, headers=headers)


def member_id(client, project_id, headers, email):
    members = client.get(f"/api/projects/{project_id}/members", headers=headers).json()
    return next(m["user_id"] for m in members if m["email"] == email)


@pytest.fixture
def team(client, make_user):
    """A project with an owner and one member in every other role: (project, {role: (email, headers)})."""
    owner_email, owner_headers = make_user()
    project = create_project(client, owner_headers)
    members = {ProjectRole.OWNER: (owner_email, owner_headers)}
    for role in ProjectRole:
        if role is ProjectRole.OWNER:
            continue
        email, headers = make_user()
        assert add_member(client, project["id"], owner_headers, email, role).status_code == 201
        members[role] = (email, headers)
    return project, members


def test_creator_becomes_owner(client, auth_headers):
    project = create_project(client, auth_headers, description="Does aspirin prevent heart attacks?")

    assert project["role"] == "owner"
    assert project["member_count"] == 1
    assert [p["id"] for p in client.get("/api/projects", headers=auth_headers).json()] == [project["id"]]


def test_project_list_only_includes_your_projects(client, make_user):
    _, alice = make_user()
    _, bob = make_user()
    create_project(client, alice, title="Alice's review")

    assert client.get("/api/projects", headers=bob).json() == []


def test_non_members_get_404_so_projects_stay_hidden(client, make_user):
    _, alice = make_user()
    _, bob = make_user()
    project_path = f"/api/projects/{create_project(client, alice)['id']}"

    assert client.get(project_path, headers=bob).status_code == 404
    assert client.patch(project_path, json={"title": "Taken over"}, headers=bob).status_code == 404
    assert client.delete(project_path, headers=bob).status_code == 404
    assert client.get(f"{project_path}/members", headers=bob).status_code == 404


def test_project_routes_require_login(client):
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/projects", json={"title": "x"}).status_code == 401


def test_every_role_can_view_and_edits_follow_the_permission_matrix(client, team):
    project, members = team
    project_path = f"/api/projects/{project['id']}"
    viewer_id = member_id(client, project["id"], members[ProjectRole.OWNER][1], members[ProjectRole.VIEWER][0])

    for role, (_, headers) in members.items():
        assert client.get(project_path, headers=headers).json()["role"] == role

        edit = client.patch(project_path, json={"description": f"edited by {role}"}, headers=headers)
        assert edit.status_code == (200 if has_permission(role, Permission.EDIT_PROJECT) else 403), role

        role_change = client.patch(f"{project_path}/members/{viewer_id}", json={"role": "viewer"}, headers=headers)
        assert role_change.status_code == (200 if has_permission(role, Permission.MANAGE_MEMBERS) else 403), role


def test_read_only_roles_cannot_edit(client, team):
    project, members = team
    for role in (ProjectRole.VIEWER, ProjectRole.AUDITOR, ProjectRole.SCREENER):
        response = client.patch(f"/api/projects/{project['id']}", json={"title": "x"}, headers=members[role][1])
        assert response.status_code == 403, role


def test_only_owners_can_delete_projects(client, team):
    project, members = team
    project_path = f"/api/projects/{project['id']}"

    assert client.delete(project_path, headers=members[ProjectRole.LEAD_REVIEWER][1]).status_code == 403
    assert client.delete(project_path, headers=members[ProjectRole.OWNER][1]).status_code == 204
    assert client.get(project_path, headers=members[ProjectRole.OWNER][1]).status_code == 404


def test_lead_reviewer_can_add_members_but_not_owners(client, team, make_user):
    project, members = team
    lead = members[ProjectRole.LEAD_REVIEWER][1]
    new_screener, _ = make_user()
    new_owner, _ = make_user()

    assert add_member(client, project["id"], lead, new_screener, "screener").status_code == 201
    assert add_member(client, project["id"], lead, new_owner, "owner").status_code == 403


def test_owner_can_hand_over_ownership(client, team):
    project, members = team
    owner_email, owner = members[ProjectRole.OWNER]
    lead_email, lead = members[ProjectRole.LEAD_REVIEWER]
    lead_id = member_id(client, project["id"], owner, lead_email)
    owner_id = member_id(client, project["id"], owner, owner_email)

    promote = client.patch(f"/api/projects/{project['id']}/members/{lead_id}", json={"role": "owner"}, headers=owner)
    assert promote.status_code == 200

    step_down = client.patch(f"/api/projects/{project['id']}/members/{owner_id}", json={"role": "viewer"}, headers=lead)
    assert step_down.status_code == 200


def test_last_owner_cannot_be_demoted_or_removed(client, auth_headers):
    project = create_project(client, auth_headers)
    me = client.get("/api/auth/me", headers=auth_headers).json()["id"]
    member_path = f"/api/projects/{project['id']}/members/{me}"

    assert client.patch(member_path, json={"role": "viewer"}, headers=auth_headers).status_code == 409
    assert client.delete(member_path, headers=auth_headers).status_code == 409


def test_members_can_leave_a_project(client, team):
    project, members = team
    screener_email, screener = members[ProjectRole.SCREENER]
    screener_id = member_id(client, project["id"], screener, screener_email)

    assert client.delete(f"/api/projects/{project['id']}/members/{screener_id}", headers=screener).status_code == 204
    assert client.get(f"/api/projects/{project['id']}", headers=screener).status_code == 404


def test_members_cannot_remove_other_members_without_permission(client, team):
    project, members = team
    viewer_id = member_id(client, project["id"], members[ProjectRole.OWNER][1], members[ProjectRole.VIEWER][0])

    response = client.delete(
        f"/api/projects/{project['id']}/members/{viewer_id}", headers=members[ProjectRole.SCREENER][1]
    )
    assert response.status_code == 403


def test_adding_unknown_or_existing_member_is_rejected(client, make_user):
    email, owner = make_user()
    project = create_project(client, owner)

    assert add_member(client, project["id"], owner, "nobody@example.org", "viewer").status_code == 404
    assert add_member(client, project["id"], owner, email, "viewer").status_code == 409


def test_invalid_role_rejected(client, make_user):
    _, owner = make_user()
    other, _ = make_user()
    project = create_project(client, owner)

    assert add_member(client, project["id"], owner, other, "superuser").status_code == 422


def test_cannot_create_project_in_an_organization_you_do_not_belong_to(client, auth_headers):
    response = client.post("/api/projects", json={"title": "x", "organization_id": 999999}, headers=auth_headers)

    assert response.status_code == 404
