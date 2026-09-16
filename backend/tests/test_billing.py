"""Billing and plan limits: public plans, limits at every place work is created, AI credits that exempt users' own
keys, the simulated checkout and its webhooks (idempotent, signed), cancellation, administrator plan assignment, and
the Stripe adapter's signatures, event mapping, and checkout request."""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select
from test_governance import make_admin
from workflow_helpers import RECORDS, add_member, create_project, lock_protocol, open_screening, url

import billing
import entitlements
import models
from database import SessionLocal


@pytest.fixture(autouse=True)
def restore_plans():
    """Plans are shared rows; put their limits and prices back after each test."""
    with SessionLocal() as db:
        saved = {plan.id: (dict(plan.limits), dict(plan.provider_prices)) for plan in db.scalars(select(models.Plan))}
    yield
    with SessionLocal() as db:
        for plan in db.scalars(select(models.Plan)):
            if plan.id in saved:
                plan.limits, plan.provider_prices = saved[plan.id]
        db.commit()


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")


def set_limits(code: str, **limits) -> None:
    with SessionLocal() as db:
        plan = db.scalar(select(models.Plan).where(models.Plan.code == code))
        assert plan is not None
        plan.limits = {**plan.limits, **limits}
        db.commit()


def set_prices(code: str, prices: dict) -> None:
    with SessionLocal() as db:
        plan = db.scalar(select(models.Plan).where(models.Plan.code == code))
        assert plan is not None
        plan.provider_prices = prices
        db.commit()


def user_id(client, headers) -> int:
    return client.get("/api/auth/me", headers=headers).json()["id"]


def account_path(client, headers, suffix: str = "") -> str:
    return f"/api/billing/accounts/user/{user_id(client, headers)}{suffix}"


def test_plans_are_public(client):
    response = client.get("/api/billing/plans")

    assert response.status_code == 200
    body = response.json()
    assert [plan["code"] for plan in body["plans"]] == ["free", "researcher", "team", "institution"]
    assert body["enabled"] is False


def test_without_billing_every_account_is_unlimited(client, auth_headers):
    for number in range(3):
        create_project(client, auth_headers, title=f"Review {number}")


def test_the_free_plan_limits_projects(client, auth_headers, billing_on):
    create_project(client, auth_headers, title="First")

    second = client.post("/api/projects", json={"title": "Second"}, headers=auth_headers)

    assert second.status_code == 402
    assert "Free plan allows 1 projects" in second.json()["detail"]
    assert "Billing" in second.json()["detail"]


def test_members_and_invitations_are_limited(client, auth_headers, make_user, billing_on):
    set_limits("free", members_per_project=2)
    project_id = create_project(client, auth_headers)
    add_member(client, project_id, auth_headers, make_user, "screener")

    email, _ = make_user()
    added = client.post(url(project_id, "members"), json={"email": email, "role": "viewer"}, headers=auth_headers)
    invited = client.post(
        url(project_id, "invitations"), json={"email": "someone@example.org", "role": "viewer"}, headers=auth_headers
    )

    assert added.status_code == invited.status_code == 402


def test_record_imports_are_limited(client, auth_headers, fake_provider, billing_on):
    project_id = create_project(client, auth_headers)
    lock_protocol(client, project_id, auth_headers, fake_provider)
    set_limits("free", records_per_month=1)

    response = client.post(
        url(project_id, "imports"), json={"file_name": "export.csv", "records": RECORDS}, headers=auth_headers
    )

    assert response.status_code == 402
    assert "records added this month" in response.json()["detail"]


def test_ai_credits_limit_the_servers_keys_but_never_your_own(client, auth_headers, fake_provider, billing_on):
    project_id = create_project(client, auth_headers)
    records = open_screening(client, project_id, auth_headers, fake_provider)
    set_limits("free", ai_credits_per_month=0)
    body = {"record_ids": [records[0]["id"]]}

    blocked = client.post(url(project_id, "screening/ai"), json=body, headers=auth_headers)
    assert blocked.status_code == 402
    assert "AI credits" in blocked.json()["detail"]

    model = client.get(url(project_id), headers=auth_headers).json()["ai_model"]
    saved = client.put(
        f"/api/me/api-keys/{model['provider']}", json={"api_key": "my-own-key-123"}, headers=auth_headers
    )
    assert saved.status_code == 200
    fake_provider('{"decision": "Include", "reasoning": "Adults.", "confidence": 0.9}')

    allowed = client.post(url(project_id, "screening/ai"), json=body, headers=auth_headers)
    assert allowed.status_code == 202, allowed.text


def test_usage_counts_only_tokens_on_the_servers_keys(client, auth_headers):
    project_id = create_project(client, auth_headers)
    with SessionLocal() as db:
        for source, tokens in (("platform", 5000), ("user", 9000)):
            db.add(
                models.AIRun(
                    project_id=project_id,
                    task="screening",
                    provider="gemini",
                    model="test",
                    prompt_version="screening-v3",
                    status="succeeded",
                    key_source=source,
                    input_tokens=tokens,
                    output_tokens=0,
                )
            )
        db.commit()
        project = db.get(models.Project, project_id)
        assert project is not None
        assert entitlements.usage_of(db, entitlements.account_for_project(project), "ai_credits_per_month") == 5.0


def test_api_tokens_and_webhooks_need_a_plan_that_includes_them(client, auth_headers, billing_on):
    project_id = create_project(client, auth_headers)

    token = client.post("/api/me/tokens", json={"name": "Script", "scopes": ["read"]}, headers=auth_headers)
    webhook = client.post(
        url(project_id, "webhooks"),
        json={"url": "https://example.org/hook", "events": ["task.*"]},
        headers=auth_headers,
    )

    assert token.status_code == 402 and "API access" in token.json()["detail"]
    assert webhook.status_code == 402


def test_simulated_checkout_changes_the_plan_through_an_idempotent_webhook(
    client, auth_headers, billing_on, monkeypatch
):
    monkeypatch.setenv("BILLING_PROVIDER", "dev")
    create_project(client, auth_headers, title="First")

    checkout = client.post(
        account_path(client, auth_headers, "/checkout"), json={"plan_code": "researcher"}, headers=auth_headers
    )
    assert checkout.status_code == 200, checkout.text
    session = parse_qs(urlparse(checkout.json()["url"]).query)["session"][0]

    completed = client.post("/api/billing/dev/complete", json={"session": session}, headers=auth_headers)
    assert completed.status_code == 200, completed.text
    account = completed.json()["account"]
    assert account["plan"]["code"] == "researcher"
    assert account["subscription"]["status"] == "active"
    assert client.post("/api/projects", json={"title": "Second"}, headers=auth_headers).status_code == 201
    inbox = client.get("/api/notifications", headers=auth_headers).json()["notifications"]
    assert any("Researcher plan" in notification["title"] for notification in inbox)

    # The same webhook delivered twice is applied once.
    body, headers = billing.PROVIDERS["dev"].completion_webhook(session, "paid")  # type: ignore[attr-defined]
    first = client.post("/api/billing/webhooks/dev", content=body, headers=headers)
    second = client.post("/api/billing/webhooks/dev", content=body, headers=headers)
    assert first.json()["result"] == "processed"
    assert second.json()["result"] == "duplicate"
    event_id = json.loads(body)["id"]
    with SessionLocal() as db:
        assert len(db.scalars(select(models.BillingEvent).where(models.BillingEvent.event_id == event_id)).all()) == 1


def test_a_tampered_webhook_is_refused(client, billing_on, monkeypatch):
    monkeypatch.setenv("BILLING_PROVIDER", "dev")
    body = json.dumps(
        {"id": "evt_forged", "type": "subscription.updated", "account": "user:1", "plan": "team"}
    ).encode()

    response = client.post("/api/billing/webhooks/dev", content=body, headers={"x-dev-signature": "0" * 64})

    assert response.status_code == 400


def test_a_failed_payment_and_cancellation(client, auth_headers, billing_on, monkeypatch):
    monkeypatch.setenv("BILLING_PROVIDER", "dev")
    create_project(client, auth_headers, title="First")
    checkout = client.post(
        account_path(client, auth_headers, "/checkout"), json={"plan_code": "researcher"}, headers=auth_headers
    ).json()
    session = parse_qs(urlparse(checkout["url"]).query)["session"][0]
    client.post("/api/billing/dev/complete", json={"session": session}, headers=auth_headers)

    failed = client.post(
        "/api/billing/dev/complete", json={"session": session, "outcome": "failed"}, headers=auth_headers
    )
    account = failed.json()["account"]
    # A missed payment keeps the plan while the account is past due.
    assert account["subscription"]["status"] == "past_due" and account["plan"]["code"] == "researcher"

    cancelled = client.post(account_path(client, auth_headers, "/cancel"), headers=auth_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["plan"]["code"] == "free"
    client.post("/api/projects", json={"title": "Second"}, headers=auth_headers)
    assert client.post("/api/projects", json={"title": "Third"}, headers=auth_headers).status_code == 402


def test_checkout_explains_plans_that_are_not_sold_online(client, auth_headers, billing_on, monkeypatch):
    institution = client.post(
        account_path(client, auth_headers, "/checkout"), json={"plan_code": "institution"}, headers=auth_headers
    )
    assert institution.status_code == 409 and "Contact sales" in institution.json()["detail"]

    # The default provider takes no online payments.
    manual = client.post(
        account_path(client, auth_headers, "/checkout"), json={"plan_code": "team"}, headers=auth_headers
    )
    assert manual.status_code == 409 and "arranged directly" in manual.json()["detail"]


def test_administrators_put_organizations_on_a_plan(client, auth_headers, make_user, billing_on):
    admin_email, admin_headers = make_user()
    make_admin(admin_email)
    member_email, member_headers = make_user()
    assert client.get("/api/admin/subscriptions", headers=member_headers).status_code == 403

    organization = client.post(
        "/api/admin/organizations", json={"name": f"Institute {time.time_ns()}"}, headers=admin_headers
    )
    assert organization.status_code == 201, organization.text
    organization_id = organization.json()["id"]
    client.put(
        f"/api/admin/organizations/{organization_id}/members",
        json={"email": member_email, "role": "admin"},
        headers=admin_headers,
    )
    assigned = client.put(
        "/api/admin/subscriptions",
        json={"account_kind": "organization", "account_id": organization_id, "plan_code": "team", "note": "Invoice 42"},
        headers=admin_headers,
    )
    assert assigned.status_code == 200, assigned.text

    for number in range(3):
        response = client.post(
            "/api/projects",
            json={"title": f"Org review {number}", "organization_id": organization_id},
            headers=member_headers,
        )
        assert response.status_code == 201, response.text
    accounts = client.get("/api/billing/accounts", headers=member_headers).json()
    assert {"organization": "Team"} == {a["kind"]: a["plan"] for a in accounts if a["kind"] == "organization"}


def test_administrators_cannot_overwrite_a_stripe_subscription(client, make_user):
    admin_email, admin_headers = make_user()
    make_admin(admin_email)
    customer_email, customer_headers = make_user()
    customer_id = user_id(client, customer_headers)
    with SessionLocal() as db:
        team = db.scalar(select(models.Plan).where(models.Plan.code == "team"))
        assert team is not None
        db.add(
            models.Subscription(
                user_id=customer_id,
                plan_id=team.id,
                status="active",
                provider="stripe",
                provider_subscription_id="sub_1",
            )
        )
        db.commit()

    response = client.put(
        "/api/admin/subscriptions",
        json={"account_kind": "user", "account_id": customer_id, "plan_code": "free"},
        headers=admin_headers,
    )

    assert response.status_code == 409


def stripe_header(body: bytes, secret: str, timestamp: int | None = None) -> str:
    timestamp = timestamp or int(time.time())
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


def test_stripe_signatures_are_checked():
    body = b'{"id": "evt_1"}'

    billing.verify_stripe_signature(body, stripe_header(body, "whsec_test"), "whsec_test")
    with pytest.raises(billing.BillingError):
        billing.verify_stripe_signature(body, stripe_header(body, "whsec_other"), "whsec_test")
    with pytest.raises(billing.BillingError):
        billing.verify_stripe_signature(body, stripe_header(body, "whsec_test", int(time.time()) - 3600), "whsec_test")


@pytest.fixture
def stripe(monkeypatch, billing_on):
    monkeypatch.setenv("BILLING_PROVIDER", "stripe")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    set_prices("researcher", {"stripe": {"month": "price_researcher_month"}})


def test_stripe_subscription_events_set_the_plan(client, auth_headers, stripe):
    account = f"user:{user_id(client, auth_headers)}"
    subscription = {
        "id": "sub_123",
        "customer": "cus_123",
        "status": "active",
        "cancel_at_period_end": False,
        "metadata": {"account": account, "plan": "researcher"},
        "items": {
            "data": [
                {
                    "current_period_end": int(time.time()) + 30 * 86400,
                    "price": {"id": "price_researcher_month", "recurring": {"interval": "month"}},
                }
            ]
        },
    }

    def send(event_id: str, kind: str):
        body = json.dumps({"id": event_id, "type": kind, "data": {"object": subscription}}).encode()
        return client.post(
            "/api/billing/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": stripe_header(body, "whsec_test")},
        )

    created = send(f"evt_{time.time_ns()}", "customer.subscription.created")
    assert created.status_code == 200 and created.json()["result"] == "processed", created.text
    detail = client.get(account_path(client, auth_headers), headers=auth_headers).json()
    assert detail["plan"]["code"] == "researcher" and detail["subscription"]["interval"] == "month"

    deleted = send(f"evt_{time.time_ns()}", "customer.subscription.deleted")
    assert deleted.json()["result"] == "processed"
    assert client.get(account_path(client, auth_headers), headers=auth_headers).json()["plan"]["code"] == "free"


def test_stripe_checkout_sends_the_price_and_account(client, auth_headers, stripe, monkeypatch):
    sent: dict = {}

    class Reply:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    def fake_request(method, address, data=None, auth=None, timeout=None):
        sent.update({"method": method, "url": address, "data": data, "auth": auth})
        return Reply()

    monkeypatch.setattr(billing.requests, "request", fake_request)

    response = client.post(
        account_path(client, auth_headers, "/checkout"),
        json={"plan_code": "researcher", "interval": "month"},
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["url"].startswith("https://checkout.stripe.com/")
    assert sent["url"].endswith("/checkout/sessions") and sent["auth"] == ("sk_test_123", "")
    assert sent["data"]["line_items[0][price]"] == "price_researcher_month"
    assert sent["data"]["metadata[account]"] == f"user:{user_id(client, auth_headers)}"
    # A yearly checkout isn't offered without a yearly price.
    yearly = client.post(
        account_path(client, auth_headers, "/checkout"),
        json={"plan_code": "researcher", "interval": "year"},
        headers=auth_headers,
    )
    assert yearly.status_code == 409


def test_api_access_comes_from_an_organization_plan_too(client, make_user, billing_on):
    admin_email, admin_headers = make_user()
    make_admin(admin_email)
    member_email, member_headers = make_user()
    # On the Free plan personally, API access is refused.
    assert (
        client.post("/api/me/tokens", json={"name": "Script", "scopes": ["read"]}, headers=member_headers).status_code
        == 402
    )

    organization = client.post(
        "/api/admin/organizations", json={"name": f"Paying institute {time.time_ns()}"}, headers=admin_headers
    ).json()
    client.put(
        f"/api/admin/organizations/{organization['id']}/members",
        json={"email": member_email, "role": "member"},
        headers=admin_headers,
    )
    client.put(
        "/api/admin/subscriptions",
        json={"account_kind": "organization", "account_id": organization["id"], "plan_code": "team"},
        headers=admin_headers,
    )

    created = client.post("/api/me/tokens", json={"name": "Script", "scopes": ["read"]}, headers=member_headers)
    assert created.status_code == 201, created.text
    assert (
        client.get("/api/projects", headers={"Authorization": f"Bearer {created.json()['token']}"}).status_code == 200
    )
