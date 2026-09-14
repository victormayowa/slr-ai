from sqlalchemy import func, select

import models
from database import SessionLocal
from scripts import seed_dev


def run_seed():
    with SessionLocal() as db:
        seed_dev.seed(db)
        db.commit()


def test_seeding_twice_does_not_duplicate_anything():
    run_seed()
    run_seed()

    with SessionLocal() as db:
        demo_users = db.scalar(
            select(func.count()).select_from(models.User).where(models.User.email.like("%@omnireview.test"))
        )
        project = db.scalar(select(models.Project).where(models.Project.title == seed_dev.DEMO_PROJECT))
        project_member_count = len(project.members) if project else 0
        organizations = db.scalar(
            select(func.count())
            .select_from(models.Organization)
            .where(models.Organization.name == seed_dev.DEMO_ORGANIZATION)
        )

    assert demo_users == len(seed_dev.DEMO_USERS)
    assert organizations == 1
    assert project_member_count == sum(1 for spec in seed_dev.DEMO_USERS if spec.project_role)


def test_demo_accounts_sign_in_and_see_only_their_projects(client, login):
    run_seed()

    screener = login("screener1@omnireview.test", seed_dev.DEMO_PASSWORD)
    outsider = login("outsider@omnireview.test", seed_dev.DEMO_PASSWORD)

    screener_projects = client.get("/api/projects", headers=screener).json()
    assert [(p["title"], p["role"]) for p in screener_projects] == [(seed_dev.DEMO_PROJECT, "screener")]
    assert [p["title"] for p in client.get("/api/projects", headers=outsider).json()] == [seed_dev.OUTSIDER_PROJECT]


def test_demo_accounts_support_orcid_and_institutional_login(client, login):
    run_seed()

    login("0000-0002-1825-0097", seed_dev.DEMO_PASSWORD)
    me = client.get("/api/auth/me", headers=login("c.clinic@demo-university.test", seed_dev.DEMO_PASSWORD)).json()

    assert me["organizations"] == [
        {"id": me["organizations"][0]["id"], "name": seed_dev.DEMO_ORGANIZATION, "role": "member"}
    ]


def test_seed_refuses_to_run_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    assert seed_dev.main() == 1
