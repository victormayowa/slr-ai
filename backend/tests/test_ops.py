from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_governance import make_admin
from workflow_helpers import create_project

import config_guard
import emailer
import models
import net_safety
import ops
from database import SessionLocal
from scripts.production_check import configuration_checks


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_checks_the_database_schema_and_storage(client):
    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["migrations"] == "ok"
    assert body["checks"]["storage"] == "ok"


def test_readyz_reports_unwritable_storage(client, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where the storage directory should be")
    monkeypatch.setenv("DOCUMENT_STORAGE_DIR", str(blocker))

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["storage"] == "fail"


def test_request_id_generated_when_absent(client):
    assert len(client.get("/healthz").headers["x-request-id"]) == 32


def test_well_formed_request_id_is_reused(client):
    response = client.get("/healthz", headers={"X-Request-ID": "trace-123"})

    assert response.headers["x-request-id"] == "trace-123"


def test_malformed_request_id_is_replaced(client):
    response = client.get("/healthz", headers={"X-Request-ID": "bad id\nwith newline"})

    assert response.headers["x-request-id"] != "bad id\nwith newline"
    assert len(response.headers["x-request-id"]) == 32


@pytest.fixture
def admin(client, make_user):
    email, headers = make_user()
    make_admin(email)
    return headers


def test_system_status_is_for_administrators(client, auth_headers, admin):
    assert client.get("/api/admin/system", headers=auth_headers).status_code == 403

    response = client.get("/api/admin/system", headers=admin)

    assert response.status_code == 200
    names = {check["name"] for check in response.json()["checks"]}
    assert {"database", "migrations", "storage", "worker", "backup", "email", "billing"} <= names


def test_readiness_fails_outside_a_production_configuration(client, admin):
    response = client.get("/api/admin/readiness", headers=admin)

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    failed = {check["name"] for check in body["checks"] if check["status"] == "fail"}
    assert {"app_env", "app_url"} <= failed


def test_a_production_configuration_passes_its_checks(monkeypatch):
    settings = {
        "APP_ENV": "production",
        "JWT_SECRET_KEY": "x" * 64,
        "DATA_ENCRYPTION_KEY": "cHJvZHVjdGlvbi1lbmNyeXB0aW9uLWtleS0zMmJ5dGU=",
        "DATABASE_URL": "postgresql+psycopg://omnireview:strong@postgres:5432/omnireview",
        "CORS_ORIGINS": "https://reviews.example.org",
        "APP_URL": "https://reviews.example.org",
        "BCRYPT_ROUNDS": "12",
        "SMTP_HOST": "smtp.example.org",
        "SMTP_FROM": "no-reply@example.org",
        "EMAIL_BACKEND": "smtp",
        "DOCUMENT_STORAGE_DIR": "/data/documents",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    results = {check.name: check.status for check in configuration_checks()}

    for name in ("app_env", "jwt_secret", "encryption_key", "database_password", "cors", "app_url", "email_backend"):
        assert results[name] == "ok", name
    # The legal templates still have placeholders to complete.
    assert results["legal_terms"] == "fail"


def test_unsafe_production_settings_stop_the_server(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("BILLING_PROVIDER", "dev")
    monkeypatch.setenv("EMAIL_BACKEND", "console")

    with pytest.raises(config_guard.UnsafeProductionConfig) as error:
        config_guard.enforce()

    message = str(error.value)
    assert "CORS_ORIGINS" in message and "BILLING_PROVIDER" in message and "EMAIL_BACKEND" in message


def test_development_settings_do_not_stop_the_server(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("BILLING_PROVIDER", "dev")

    config_guard.enforce()


def test_email_backends(monkeypatch):
    monkeypatch.delenv("EMAIL_BACKEND", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    assert emailer.backend() == "console"
    monkeypatch.setenv("APP_ENV", "production")
    assert emailer.backend() == "disabled"
    monkeypatch.setenv("SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("SMTP_FROM", "no-reply@example.org")
    assert emailer.backend() == "smtp"


def test_private_webhook_addresses_only_in_development(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_WEBHOOK_URLS", "true")
    monkeypatch.setenv("APP_ENV", "development")
    net_safety.ensure_public_url("http://127.0.0.1:9000/hook")

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(net_safety.UnsafeURL):
        net_safety.ensure_public_url("http://127.0.0.1:9000/hook")


def test_worker_heartbeat(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://unused:6379/0")
    with SessionLocal() as db:
        ops.heartbeat(db, "worker", {"queue": "test"})
        assert ops.check_worker(db).status == "ok"
        row = db.scalar(select(models.OpsHeartbeat).where(models.OpsHeartbeat.name == "worker"))
        assert row is not None
        row.last_seen_at = models.utcnow() - timedelta(minutes=30)
        db.commit()
        assert ops.check_worker(db).status == "fail"


def test_daily_report_reaches_administrators(client, admin):
    with SessionLocal() as db:
        assert ops.daily_report(db) >= 1

    notifications = client.get("/api/notifications", headers=admin).json()["notifications"]
    assert any("Operations report" in notification["title"] for notification in notifications)


def test_expired_manual_subscriptions_end(client, make_user):
    _, headers = make_user()
    user_id = client.get("/api/auth/me", headers=headers).json()["id"]
    with SessionLocal() as db:
        team = db.scalar(select(models.Plan).where(models.Plan.code == "team"))
        assert team is not None
        subscription = models.Subscription(
            user_id=user_id,
            plan_id=team.id,
            status="active",
            provider="manual",
            current_period_end=models.utcnow() - timedelta(days=30),
        )
        db.add(subscription)
        db.commit()
        assert ops.reconcile_subscriptions(db) >= 1
        db.refresh(subscription)
        assert subscription.status == "canceled"


def test_costs_count_the_servers_keys(client, auth_headers, admin):
    project_id = create_project(client, auth_headers)
    with SessionLocal() as db:
        db.add(
            models.AIRun(
                project_id=project_id,
                task="screening",
                provider="gemini",
                model="cost-test-model",
                prompt_version="screening-v3",
                status="succeeded",
                key_source="platform",
                input_tokens=1000,
                output_tokens=500,
                cost_usd=Decimal("0.25"),
            )
        )
        db.commit()

    response = client.get("/api/admin/costs?days=1", headers=admin)

    assert response.status_code == 200
    by_model = {row["model"]: row for row in response.json()["by_model"]}
    assert by_model["cost-test-model"]["cost_usd"] >= 0.25
    assert client.get("/api/admin/failures", headers=admin).status_code == 200
