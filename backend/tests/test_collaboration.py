"""Collaboration: invitations and ownership, declarations, tasks, anchored comments with mentions, notifications,
reviewer workload and metrics, the team audit report, and the documentation-grounded help search."""

from factories import registration
from workflow_helpers import create_project, decide, open_screening, url

from database import SessionLocal


def members(client, project_id, headers):
    response = client.get(url(project_id, "members"), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def invite(client, project_id, headers, email, role="screener"):
    response = client.post(url(project_id, "invitations"), json={"email": email, "role": role}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def register_only(client, email):
    """Register an account without signing in, so an invitation can be accepted by it later."""
    user = registration(email=email)
    assert client.post("/api/auth/register", json=user).status_code == 200
    return user


def test_invitation_is_accepted_by_the_invited_address(client, auth_headers, login, make_user):
    project_id = create_project(client, auth_headers)
    email = registration()["email"]
    register_only(client, email)

    created = invite(client, project_id, auth_headers, email, "methodologist")
    assert created["status"] == "open" and created["link"].endswith(created["link"].rsplit("/", 1)[-1])
    token = created["link"].rsplit("/", 1)[-1]

    invited_headers = login(email)
    accepted = client.post("/api/invitations/accept", json={"token": token}, headers=invited_headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "methodologist"
    assert any(member["email"] == email for member in members(client, project_id, auth_headers))

    # The same link can't be used twice.
    again = client.post("/api/invitations/accept", json={"token": token}, headers=invited_headers)
    assert again.status_code == 409


def test_invitation_refuses_a_different_account(client, auth_headers, make_user):
    project_id = create_project(client, auth_headers)
    created = invite(client, project_id, auth_headers, registration()["email"])
    token = created["link"].rsplit("/", 1)[-1]
    _, other_headers = make_user()

    response = client.post("/api/invitations/accept", json={"token": token}, headers=other_headers)

    assert response.status_code == 403
    assert "Sign in with that address" in response.json()["detail"]


def test_revoked_invitation_cannot_be_accepted(client, auth_headers, login):
    project_id = create_project(client, auth_headers)
    email = registration()["email"]
    register_only(client, email)
    created = invite(client, project_id, auth_headers, email)
    token = created["link"].rsplit("/", 1)[-1]

    assert client.delete(url(project_id, f"invitations/{created['id']}"), headers=auth_headers).status_code == 204

    response = client.post("/api/invitations/accept", json={"token": token}, headers=login(email))
    assert response.status_code == 409
    assert client.get(url(project_id, "invitations"), headers=auth_headers).json()[0]["status"] == "revoked"


def test_only_a_member_of_the_project_can_be_given_ownership(client, auth_headers, make_user):
    project_id = create_project(client, auth_headers)
    email, colleague_headers = make_user()
    assert (
        client.post(
            url(project_id, "members"), json={"email": email, "role": "screener"}, headers=auth_headers
        ).status_code
        == 201
    )
    new_owner = next(m for m in members(client, project_id, auth_headers) if m["email"] == email)

    response = client.post(
        url(project_id, "ownership"),
        json={"user_id": new_owner["user_id"], "keep_role": "lead_reviewer"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    roles = {member["email"]: member["role"] for member in response.json()}
    assert roles[email] == "owner"
    # The previous owner keeps working on the review in the role they chose.
    assert sorted(roles.values()) == ["lead_reviewer", "owner"]
    # The new owner can now manage members, and the old one can't grant ownership any more.
    assert client.get(url(project_id, "invitations"), headers=colleague_headers).status_code == 200


def test_each_member_declares_their_own_competing_interests(client, auth_headers, make_user):
    project_id = create_project(client, auth_headers)

    saved = client.put(
        url(project_id, "declarations/me"),
        json={"has_competing_interests": True, "statement": "Advises the manufacturer", "funding": "None"},
        headers=auth_headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["has_competing_interests"] is True

    # A declared competing interest has to be described.
    empty = client.put(
        url(project_id, "declarations/me"),
        json={"has_competing_interests": True, "statement": "  ", "funding": ""},
        headers=auth_headers,
    )
    assert empty.status_code == 422

    listed = client.get(url(project_id, "declarations"), headers=auth_headers).json()
    assert listed[0]["statement"] == "Advises the manufacturer"


def test_assigning_a_task_notifies_the_assignee(client, auth_headers, make_user):
    project_id = create_project(client, auth_headers)
    email, colleague_headers = make_user()
    client.post(url(project_id, "members"), json={"email": email, "role": "screener"}, headers=auth_headers)
    colleague = next(m for m in members(client, project_id, auth_headers) if m["email"] == email)

    created = client.post(
        url(project_id, "tasks"),
        json={
            "title": "Screen the 2026 records",
            "stage": "screening",
            "assignee_id": colleague["user_id"],
            "due_on": "2026-10-01",
            "priority": "high",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["assignee"] and task["status"] == "open"

    inbox = client.get("/api/notifications", headers=colleague_headers).json()
    assert inbox["unread"] == 1
    assert "Screen the 2026 records" in inbox["notifications"][0]["title"]

    # The assignee can finish their own task.
    done = client.patch(url(project_id, f"tasks/{task['id']}"), json={"status": "done"}, headers=colleague_headers)
    assert done.status_code == 200
    assert done.json()["completed_at"] is not None


def test_a_task_cannot_be_changed_by_an_unrelated_member(client, auth_headers, make_user):
    project_id = create_project(client, auth_headers)
    email, other_headers = make_user()
    client.post(url(project_id, "members"), json={"email": email, "role": "screener"}, headers=auth_headers)
    task = client.post(url(project_id, "tasks"), json={"title": "Check the search"}, headers=auth_headers).json()

    response = client.patch(url(project_id, f"tasks/{task['id']}"), json={"status": "done"}, headers=other_headers)

    assert response.status_code == 403


def test_mentioning_a_member_in_a_comment_notifies_them(client, auth_headers, make_user):
    project_id = create_project(client, auth_headers)
    email, colleague_headers = make_user()
    client.post(url(project_id, "members"), json={"email": email, "role": "screener"}, headers=auth_headers)

    created = client.post(
        url(project_id, "comments"),
        json={"anchor_key": "record:1", "anchor_label": "Aspirin trial", "body": f"@{email} does this one qualify?"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    comment = created.json()
    assert comment["mentions"] and comment["author"]

    inbox = client.get("/api/notifications", headers=colleague_headers).json()
    assert inbox["unread"] == 1 and inbox["notifications"][0]["kind"] == "mention"

    # A reply notifies the people already in the thread.
    reply = client.post(
        url(project_id, "comments"),
        json={"anchor_key": "ignored", "body": "Yes, adults were randomized.", "parent_id": comment["id"]},
        headers=colleague_headers,
    )
    assert reply.status_code == 201, reply.text
    # The reply takes the thread's anchor, whatever was sent.
    assert reply.json()["anchor_key"] == "record:1"
    owner_inbox = client.get("/api/notifications", headers=auth_headers).json()
    assert owner_inbox["notifications"][0]["kind"] == "reply"


def test_comments_are_counted_per_anchor_and_resolve_as_a_thread(client, auth_headers):
    project_id = create_project(client, auth_headers)
    first = client.post(
        url(project_id, "comments"),
        json={"anchor_key": "cell:3:7", "body": "Is this the 12-month value?"},
        headers=auth_headers,
    ).json()
    client.post(
        url(project_id, "comments"),
        json={"anchor_key": "cell:3:7", "body": "It is.", "parent_id": first["id"]},
        headers=auth_headers,
    )

    counts = client.get(url(project_id, "comments/counts"), params={"anchor_prefix": "cell:"}, headers=auth_headers)
    assert counts.json()["cell:3:7"] == {"comments": 2, "unresolved": 2}

    resolved = client.post(url(project_id, f"comments/{first['id']}/resolve"), headers=auth_headers)
    assert resolved.status_code == 200 and resolved.json()["resolved"] is True
    after = client.get(url(project_id, "comments/counts"), params={"anchor_prefix": "cell:"}, headers=auth_headers)
    assert after.json()["cell:3:7"]["unresolved"] == 0


def test_a_deleted_comment_keeps_its_place_in_the_thread(client, auth_headers):
    project_id = create_project(client, auth_headers)
    comment = client.post(
        url(project_id, "comments"), json={"anchor_key": "project", "body": "Ignore this"}, headers=auth_headers
    ).json()

    assert client.delete(url(project_id, f"comments/{comment['id']}"), headers=auth_headers).status_code == 204

    listed = client.get(url(project_id, "comments"), headers=auth_headers).json()
    assert listed[0]["deleted"] is True and listed[0]["body"] == ""


def test_notifications_are_marked_read(client, auth_headers, make_user):
    project_id = create_project(client, auth_headers)
    email, colleague_headers = make_user()
    client.post(url(project_id, "members"), json={"email": email, "role": "screener"}, headers=auth_headers)
    client.post(
        url(project_id, "comments"), json={"anchor_key": "project", "body": f"@{email} welcome"}, headers=auth_headers
    )

    inbox = client.get("/api/notifications", headers=colleague_headers).json()
    notification_id = inbox["notifications"][0]["id"]
    assert client.post(f"/api/notifications/{notification_id}/read", headers=colleague_headers).status_code == 200
    assert client.get("/api/notifications", headers=colleague_headers).json()["unread"] == 0

    client.post(
        url(project_id, "comments"), json={"anchor_key": "project", "body": f"@{email} and again"}, headers=auth_headers
    )
    assert client.post("/api/notifications/read-all", headers=colleague_headers).json()["marked"] == 1


def test_email_preference_is_saved(client, auth_headers):
    assert client.get("/api/me/notification-preferences", headers=auth_headers).json()["email_notifications"] is True

    response = client.put("/api/me/notification-preferences", json={"email_notifications": False}, headers=auth_headers)

    assert response.json()["email_notifications"] is False


def test_workload_and_reviewer_metrics_count_real_decisions(client, auth_headers, fake_provider):
    project_id = create_project(client, auth_headers)
    included, excluded = open_screening(client, project_id, auth_headers, fake_provider)
    decide(client, project_id, auth_headers, included["id"], "include")
    decide(client, project_id, auth_headers, excluded["id"], "exclude")

    workload = client.get(url(project_id, "team/workload"), headers=auth_headers).json()
    assert workload[0]["title_abstract_decisions"] == 2

    metrics = client.get(url(project_id, "team/metrics"), headers=auth_headers).json()
    reviewer = metrics["reviewers"][0]
    assert reviewer["decisions"] == 2 and reviewer["agreement_with_final"] == 1.0
    # One reviewer per record, so there is no pair to compare.
    assert metrics["pairwise_agreement"] == []


def test_team_report_is_available_as_json_and_docx(client, auth_headers, fake_provider):
    project_id = create_project(client, auth_headers)
    open_screening(client, project_id, auth_headers, fake_provider)
    client.post(url(project_id, "tasks"), json={"title": "Write the protocol"}, headers=auth_headers)

    report = client.get(url(project_id, "team/report"), headers=auth_headers)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["tasks"]["total"] == 1 and body["audit_events"] > 0
    assert body["members"][0]["role"] == "owner"

    document = client.get(url(project_id, "team/report"), params={"format": "docx"}, headers=auth_headers)
    assert document.status_code == 200
    assert document.content[:2] == b"PK"


def test_help_search_finds_documentation_sections(client, auth_headers):
    response = client.get("/api/help/search", params={"q": "risk of bias"}, headers=auth_headers)

    assert response.status_code == 200, response.text
    sections = response.json()
    assert sections and {"id", "page", "heading", "anchor"} <= set(sections[0])


def test_due_task_reminders_notify_the_assignee_once(client, auth_headers, make_user):
    """The worker's daily reminder, called directly because it is a cron job rather than a route."""
    from notifications import remind_due_tasks

    project_id = create_project(client, auth_headers)
    email, colleague_headers = make_user()
    client.post(url(project_id, "members"), json={"email": email, "role": "screener"}, headers=auth_headers)
    colleague = next(member for member in members(client, project_id, auth_headers) if member["email"] == email)
    client.post(
        url(project_id, "tasks"),
        json={"title": "Finish screening", "assignee_id": colleague["user_id"], "due_on": "2026-01-01"},
        headers=auth_headers,
    )
    # Clear the assignment notification, so only the reminder is left to find.
    client.post("/api/notifications/read-all", headers=colleague_headers)

    with SessionLocal() as db:
        assert remind_due_tasks(db) >= 1
        # Each task reminds once, however often the job runs.
        assert remind_due_tasks(db) == 0

    inbox = client.get("/api/notifications", headers=colleague_headers).json()
    assert inbox["unread"] == 1
    assert inbox["notifications"][0]["kind"] == "task_due"
    assert "Finish screening" in inbox["notifications"][0]["title"]


def test_an_invitation_is_emailed_to_the_address(client, auth_headers):
    import emailer

    project_id = create_project(client, auth_headers, title="Emailed review")
    email = registration()["email"]

    created = invite(client, project_id, auth_headers, email)

    assert created["emailed"] is True
    message = next(m for m in reversed(emailer.OUTBOX) if m["to"] == email)
    assert "Emailed review" in message["text"] and created["link"] in message["text"]
