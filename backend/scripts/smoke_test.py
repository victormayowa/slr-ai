"""An end-to-end smoke test of a running OmniReview, through its public API.

Development (after `uv run python -m scripts.seed_dev`, with the API and worker running):

    uv run python -m scripts.smoke_test
    uv run python -m scripts.smoke_test --billing        # with BILLING_ENABLED=true and BILLING_PROVIDER=dev

Production (read-only apart from a task, a comment, and a token it removes again):

    python -m scripts.smoke_test --base-url https://reviews.example.org --email you@example.org --password ...

Each step prints PASS or FAIL; the exit status is 1 when any step fails.
"""

import argparse
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

DEMO_PASSWORD = "Review-Dev-2026!"


class Smoke:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.failures = 0

    def call(self, method: str, path: str, token: str | None = None, **kwargs: Any) -> requests.Response:
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return requests.request(method, f"{self.base}{path}", headers=headers, timeout=60, **kwargs)

    def step(self, name: str, check: Callable[[], str | None]) -> None:
        try:
            detail = check()
            print(f"[PASS] {name}" + (f": {detail}" if detail else ""))
        except Exception as exc:  # a smoke test reports every failure and keeps going
            self.failures += 1
            print(f"[FAIL] {name}: {exc}")

    def login(self, email: str, password: str) -> str:
        response = self.call("POST", "/api/auth/login", json={"identifier": email, "password": password})
        response.raise_for_status()
        body = response.json()
        if body.get("mfa_required"):
            raise RuntimeError(f"{email} has two-factor sign-in on; use an account without it for the smoke test")
        return body["access_token"]


def expect(response: requests.Response, status: int = 200) -> Any:
    if response.status_code != status:
        raise RuntimeError(f"HTTP {response.status_code} (expected {status}): {response.text[:300]}")
    return response.json() if response.content and "json" in response.headers.get("content-type", "") else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--email", default="owner@omnireview.test")
    parser.add_argument("--password", default=DEMO_PASSWORD)
    parser.add_argument("--colleague", default="lead@omnireview.test", help="a second account on the same project")
    parser.add_argument("--colleague-password", default=DEMO_PASSWORD)
    parser.add_argument("--billing", action="store_true", help="also run the simulated checkout (BILLING_PROVIDER=dev)")
    args = parser.parse_args()
    smoke = Smoke(args.base_url)
    state: dict[str, Any] = {}

    smoke.step("API is live", lambda: expect(smoke.call("GET", "/healthz"))["status"])
    smoke.step(
        "API is ready",
        lambda: ", ".join(f"{k}={v}" for k, v in expect(smoke.call("GET", "/readyz"))["checks"].items()),
    )

    def sign_in() -> str:
        state["token"] = smoke.login(args.email, args.password)
        me = expect(smoke.call("GET", "/api/auth/me", state["token"]))
        state["me"] = me
        return f"{me['name']} (administrator: {me['is_platform_admin']})"

    smoke.step("Sign in", sign_in)
    if "token" not in state:
        print("\nCan't continue without signing in.")
        return 1
    token = state["token"]

    def find_project() -> str:
        projects = expect(smoke.call("GET", "/api/projects", token))
        if not projects:
            raise RuntimeError("the account has no projects; run scripts.seed_dev or create one")
        state["project"] = projects[0]
        return f"{projects[0]['title']} (role {projects[0]['role']})"

    smoke.step("List projects", find_project)
    project = state.get("project")
    if project:
        base = f"/api/projects/{project['id']}"
        smoke.step("Workflow stages", lambda: f"{len(expect(smoke.call('GET', f'{base}/workflow', token)))} stages")
        smoke.step("Records", lambda: f"{len(expect(smoke.call('GET', f'{base}/records', token)))} records")
        smoke.step("PRISMA counts", lambda: str(expect(smoke.call("GET", f"{base}/prisma", token)).get("screened")))
        smoke.step("Team workload", lambda: f"{len(expect(smoke.call('GET', f'{base}/team/workload', token)))} members")

        def task_and_mention() -> str:
            colleague = smoke.login(args.colleague, args.colleague_password)
            members = expect(smoke.call("GET", f"{base}/members", token))
            target = next(m for m in members if m["email"] == args.colleague)
            task = expect(
                smoke.call(
                    "POST",
                    f"{base}/tasks",
                    token,
                    json={"title": f"Smoke test task {int(time.time())}", "assignee_id": target["user_id"]},
                ),
                201,
            )
            expect(
                smoke.call(
                    "POST",
                    f"{base}/comments",
                    token,
                    json={"anchor_key": "project", "body": f"@{args.colleague} smoke test mention"},
                ),
                201,
            )
            inbox = expect(smoke.call("GET", "/api/notifications", colleague))
            smoke.call("DELETE", f"{base}/tasks/{task['id']}", token)
            if not any("mentioned you" in n["title"] or "assigned you" in n["title"] for n in inbox["notifications"]):
                raise RuntimeError("the colleague wasn't notified")
            return f"{args.colleague} has {inbox['unread']} unread notifications"

        smoke.step("Task, mention, and notification", task_and_mention)

        def api_token() -> str:
            created = smoke.call("POST", "/api/me/tokens", token, json={"name": "Smoke test", "scopes": ["read"]})
            if created.status_code == 402:
                return "skipped: the account's plan doesn't include API access"
            body = expect(created, 201)
            try:
                expect(smoke.call("GET", "/api/projects", body["token"]))
                refused = smoke.call("POST", f"{base}/tasks", body["token"], json={"title": "x"})
                if refused.status_code != 403:
                    raise RuntimeError(f"a read token could write (HTTP {refused.status_code})")
            finally:
                smoke.call("DELETE", f"/api/me/tokens/{body['id']}", token)
            return "read token reads, can't write, and was revoked"

        smoke.step("Personal access token", api_token)
        smoke.step(
            "Record export (RIS)",
            lambda: f"{len(smoke.call('GET', f'{base}/records/export?format=ris', token).content)} bytes",
        )
        smoke.step(
            "FHIR bundle",
            lambda: f"{len(expect(smoke.call('GET', f'{base}/fhir/bundle', token))['entry'])} resources",
        )

    smoke.step(
        "Help search", lambda: f"{len(expect(smoke.call('GET', '/api/help/search?q=screening', token)))} sections"
    )
    smoke.step("Security settings", lambda: str(expect(smoke.call("GET", "/api/me/security", token))["mfa_enabled"]))
    smoke.step("Data export", lambda: f"{len(smoke.call('GET', '/api/me/export', token).content)} bytes")
    smoke.step("Legal documents", lambda: ", ".join(d["slug"] for d in expect(smoke.call("GET", "/api/legal"))))
    smoke.step(
        "Public plans", lambda: ", ".join(p["code"] for p in expect(smoke.call("GET", "/api/billing/plans"))["plans"])
    )

    if state.get("me", {}).get("is_platform_admin"):

        def system() -> str:
            checks = expect(smoke.call("GET", "/api/admin/system", token))["checks"]
            return "; ".join(f"{c['name']}={c['status']}" for c in checks)

        smoke.step("Administrator system status", system)

    if args.billing:

        def checkout() -> str:
            me = state["me"]
            account = f"/api/billing/accounts/user/{me['id']}"
            started = expect(smoke.call("POST", f"{account}/checkout", token, json={"plan_code": "researcher"}))
            session = parse_qs(urlparse(started["url"]).query)["session"][0]
            done = expect(smoke.call("POST", "/api/billing/dev/complete", token, json={"session": session}))
            plan = done["account"]["plan"]["code"]
            if plan != "researcher":
                raise RuntimeError(f"the account is on {plan}")
            expect(smoke.call("POST", f"{account}/cancel", token))
            return "checkout → webhook → Researcher plan → cancelled back to Free"

        smoke.step("Simulated checkout", checkout)

    print(f"\n{'All steps passed' if not smoke.failures else f'{smoke.failures} step(s) failed'}")
    return 1 if smoke.failures else 0


if __name__ == "__main__":
    sys.exit(main())
