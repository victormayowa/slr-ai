"""Create demo accounts, an organization, and projects for trying the app locally.

    uv run alembic upgrade head
    uv run python -m scripts.seed_dev

Safe to run repeatedly: demo records are updated in place, and demo passwords are reset. Refuses to run when
APP_ENV=production. Keep src/dev/demoAccounts.ts in sync with DEMO_USERS.
"""

import os
import sys
from dataclasses import dataclass

import bcrypt
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

import models
from database import SessionLocal
from permissions import ProjectRole

DEMO_PASSWORD = "Review-Dev-2026!"
DEMO_ORGANIZATION = "Demo University"
DEMO_PROJECT = "Aspirin for primary prevention of cardiovascular events"
DEMO_PROJECT_DESCRIPTION = (
    "Demo systematic review: does low-dose aspirin reduce major cardiovascular events in adults "
    "without established cardiovascular disease?"
)
OUTSIDER_PROJECT = "Private review (not shared with the demo team)"


@dataclass(frozen=True)
class DemoUser:
    email: str
    first_name: str
    last_name: str
    position: str
    project_role: ProjectRole | None
    orcid_id: str | None = None
    institutional_email: str | None = None


DEMO_USERS = [
    DemoUser("owner@omnireview.test", "Olivia", "Owner", "Professor", ProjectRole.OWNER),
    DemoUser("lead@omnireview.test", "Liam", "Lead", "Researcher", ProjectRole.LEAD_REVIEWER),
    DemoUser(
        "methodologist@omnireview.test",
        "Maya",
        "Methods",
        "Librarian",
        ProjectRole.METHODOLOGIST,
        orcid_id="0000-0002-1825-0097",
    ),
    DemoUser("statistician@omnireview.test", "Sam", "Stats", "Researcher", ProjectRole.STATISTICIAN),
    DemoUser(
        "clinician@omnireview.test",
        "Chloe",
        "Clinic",
        "Biomedical Researcher",
        ProjectRole.CLINICAL_EXPERT,
        institutional_email="c.clinic@demo-university.test",
    ),
    DemoUser("screener1@omnireview.test", "Sara", "Screen", "PhD Student", ProjectRole.SCREENER),
    DemoUser("screener2@omnireview.test", "Sean", "Screen", "Graduate Student", ProjectRole.SCREENER),
    DemoUser("extractor@omnireview.test", "Ezra", "Extract", "PhD Student", ProjectRole.EXTRACTOR),
    DemoUser("auditor@omnireview.test", "Ava", "Audit", "Researcher", ProjectRole.AUDITOR),
    DemoUser("viewer@omnireview.test", "Vic", "View", "Undergrad Student", ProjectRole.VIEWER),
    # Belongs to no demo project, to check that other people's projects stay hidden.
    DemoUser("outsider@omnireview.test", "Oscar", "Outsider", "Researcher", None),
]


def _upsert_user(db: Session, spec: DemoUser, password_hash: str) -> models.User:
    user = db.scalar(select(models.User).where(models.User.email == spec.email))
    if user is None:
        user = models.User(email=spec.email)
        db.add(user)
    user.first_name = spec.first_name
    user.last_name = spec.last_name
    user.hashed_password = password_hash
    user.position_role = spec.position
    user.reason_for_joining = "Demo account for local testing"
    user.institution = DEMO_ORGANIZATION
    user.orcid_id = spec.orcid_id
    user.institutional_email = spec.institutional_email
    user.is_active = True
    return user


def _get_or_create_organization(db: Session, name: str) -> models.Organization:
    organization = db.scalar(select(models.Organization).where(models.Organization.name == name))
    if organization is None:
        organization = models.Organization(name=name)
        db.add(organization)
    return organization


def _get_or_create_project(
    db: Session, title: str, creator: models.User, organization: models.Organization | None, description: str | None
) -> models.Project:
    project = db.scalar(select(models.Project).where(models.Project.title == title))
    if project is None:
        project = models.Project(title=title)
        db.add(project)
    project.description = description
    project.organization = organization
    project.owner_id = creator.id
    return project


def _set_project_role(project: models.Project, user: models.User, role: ProjectRole) -> None:
    member = next((m for m in project.members if m.user_id == user.id), None)
    if member is None:
        project.members.append(models.ProjectMember(user=user, role=role))
    else:
        member.role = role


def _set_organization_role(organization: models.Organization, user: models.User, role: str) -> None:
    member = next((m for m in organization.members if m.user_id == user.id), None)
    if member is None:
        organization.members.append(models.OrganizationMember(user=user, role=role))
    else:
        member.role = role


def seed(db: Session) -> None:
    password_hash = bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt()).decode()
    users = {spec.email: _upsert_user(db, spec, password_hash) for spec in DEMO_USERS}
    db.flush()

    organization = _get_or_create_organization(db, DEMO_ORGANIZATION)
    owner = users["owner@omnireview.test"]
    project = _get_or_create_project(db, DEMO_PROJECT, owner, organization, DEMO_PROJECT_DESCRIPTION)
    for spec in DEMO_USERS:
        if spec.project_role is None:
            continue
        user = users[spec.email]
        _set_project_role(project, user, spec.project_role)
        _set_organization_role(organization, user, "owner" if user is owner else "member")

    outsider = users["outsider@omnireview.test"]
    private_project = _get_or_create_project(db, OUTSIDER_PROJECT, outsider, None, None)
    _set_project_role(private_project, outsider, ProjectRole.OWNER)


def main() -> int:
    if os.getenv("APP_ENV", "development").lower() == "production":
        print("Refusing to create demo accounts because APP_ENV is production.", file=sys.stderr)
        return 1

    try:
        with SessionLocal() as db:
            seed(db)
            db.commit()
    except (OperationalError, ProgrammingError) as exc:
        print(f"Seeding failed ({exc.orig}). Run `uv run alembic upgrade head` first.", file=sys.stderr)
        return 1

    print(f"Demo data ready. Every account's password is: {DEMO_PASSWORD}\n")
    for spec in DEMO_USERS:
        role = spec.project_role.value if spec.project_role else "(no demo project)"
        print(f"  {spec.email:<32} {role}")
    print("\nThe methodologist can also sign in with ORCID iD 0000-0002-1825-0097.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
