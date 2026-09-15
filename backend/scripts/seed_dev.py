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
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

import models
from database import SessionLocal
from permissions import ProjectRole
from project_defaults import apply_project_defaults
from review_data import find_duplicates
from workflow import WorkflowError, complete_stage, require_stage_open

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
    apply_project_defaults(db, project)
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


DEMO_SUGGESTED_CRITERIA = (
    "Adults without established cardiovascular disease; randomized controlled trials; "
    "low-dose aspirin compared with placebo or no treatment."
)

# (kind, text, status, element)
DEMO_CRITERIA = [
    ("inclusion", "Randomized controlled trials", "accepted", "study_design"),
    ("inclusion", "Adults aged 18 or older without established cardiovascular disease", "accepted", "population"),
    ("inclusion", "Daily aspirin of 325 mg or less compared with placebo or no treatment", "accepted", "intervention"),
    ("inclusion", "Reports major adverse cardiovascular events", "accepted", "outcomes"),
    ("exclusion", "Participants with prior myocardial infarction or stroke", "accepted", "population"),
    ("exclusion", "Observational or non-randomized designs", "rejected", "study_design"),
]

DEMO_STRATEGIES = [
    (
        "PubMed",
        '("aspirin"[MeSH Terms] OR aspirin[tiab]) AND ("primary prevention"[MeSH Terms] OR "primary prevention"[tiab]) '
        "AND randomized controlled trial[pt]",
    ),
    ("Embase", "'acetylsalicylic acid'/exp AND 'primary prevention'/exp AND 'randomized controlled trial'/exp"),
]


@dataclass(frozen=True)
class DemoRecord:
    title: str
    year: str
    doi: str
    abstract: str
    screener_decision: str | None = None


_FICTIONAL = "Fictional abstract for testing OmniReview; not a real study. "

# Fictional records (DOIs use the 10.5555 test prefix). The second file repeats one record to exercise deduplication.
DEMO_IMPORTS = [
    (
        "demo-database-export.csv",
        [
            DemoRecord(
                "[Demo record] Low-dose aspirin versus placebo for primary prevention in adults aged 50 to 70",
                "2019",
                "10.5555/demo.0001",
                _FICTIONAL + "Adults without cardiovascular disease were randomized to aspirin 100 mg daily or placebo "
                "for 5 years. Major cardiovascular events: 4.1% vs 4.6%. Major bleeding: 1.9% vs 1.2%. n = 12,400.",
                screener_decision="include",
            ),
            DemoRecord(
                "[Demo record] Aspirin after myocardial infarction: a secondary prevention cohort",
                "2017",
                "10.5555/demo.0002",
                _FICTIONAL
                + "An observational cohort of 3,100 patients with prior myocardial infarction taking aspirin.",
                screener_decision="exclude",
            ),
            DemoRecord(
                "[Demo record] Aspirin in older adults without cardiovascular disease: a placebo-controlled trial",
                "2018",
                "10.5555/demo.0003",
                _FICTIONAL + "Adults aged 70 or older (n = 19,100) were randomized to aspirin 100 mg or placebo. "
                "Disability-free survival did not differ; major hemorrhage was higher with aspirin.",
                screener_decision="include",
            ),
            DemoRecord(
                "[Demo record] Daily aspirin and cancer incidence in women: long-term follow-up of a randomized trial",
                "2020",
                "10.5555/demo.0004",
                _FICTIONAL
                + "Women aged 45 or older were randomized to aspirin 100 mg on alternate days or placebo and "
                "followed for cancer outcomes; cardiovascular outcomes were secondary.",
            ),
            DemoRecord(
                "[Demo record] Aspirin for primary prevention in adults with diabetes: a randomized trial",
                "2018",
                "10.5555/demo.0005",
                _FICTIONAL
                + "Adults with diabetes and no cardiovascular disease (n = 15,500) were randomized to aspirin "
                "100 mg or placebo. Serious vascular events: 8.5% vs 9.6%.",
            ),
            DemoRecord(
                "[Demo record] Clopidogrel versus aspirin after ischaemic stroke",
                "2016",
                "",
                _FICTIONAL + "Patients with recent ischaemic stroke were randomized to clopidogrel or aspirin.",
            ),
        ],
    ),
    (
        "demo-second-database-export.csv",
        [
            DemoRecord(
                "[Demo record] Low-dose aspirin versus placebo for primary prevention in adults aged 50 to 70",
                "2019",
                "10.5555/DEMO.0001",
                _FICTIONAL + "Duplicate of a record from the first export, indexed by a second database.",
            ),
            DemoRecord(
                "[Demo record] Aspirin dosing by body weight: a pooled analysis of trials",
                "2021",
                "10.5555/demo.0006",
                _FICTIONAL
                + "Individual participant data from ten trials were pooled to examine aspirin dose by weight.",
            ),
        ],
    ),
]


DEMO_QUESTION = (
    "In adults without established cardiovascular disease, does daily low-dose aspirin, compared with placebo or no "
    "treatment, reduce major adverse cardiovascular events?"
)
DEMO_QUESTION_ELEMENTS = {
    "population": "Adults aged 18 or older without established cardiovascular disease",
    "intervention": "Daily aspirin of 325 mg or less",
    "comparator": "Placebo or no treatment",
    "outcomes": "Major adverse cardiovascular events, all-cause mortality, and major bleeding",
}
DEMO_FINER = {
    "feasible": {"rating": "yes", "note": "Several large placebo-controlled trials are published."},
    "interesting": {"rating": "yes", "note": ""},
    "novel": {
        "rating": "partly",
        "note": "Earlier reviews exist; recent trials in older adults may change conclusions.",
    },
    "ethical": {"rating": "yes", "note": ""},
    "relevant": {"rating": "yes", "note": "Guideline recommendations on aspirin for primary prevention are changing."},
}
DEMO_ANALYSIS_PLAN = {
    "synthesis_approach": "meta_analysis",
    "outcomes": [
        {
            "name": "Major adverse cardiovascular events",
            "priority": "primary",
            "timepoint": "End of follow-up",
            "measure": "Risk ratio",
        },
        {
            "name": "All-cause mortality",
            "priority": "secondary",
            "timepoint": "End of follow-up",
            "measure": "Risk ratio",
        },
        {"name": "Major bleeding", "priority": "adverse", "timepoint": "End of follow-up", "measure": "Risk ratio"},
    ],
    "subgroups": [
        {"name": "Age 70 or older", "rationale": "Bleeding risk rises with age."},
        {"name": "Diabetes", "rationale": "Higher baseline cardiovascular risk."},
    ],
    "sensitivity_analyses": [{"name": "Excluding trials at high risk of bias", "rationale": ""}],
    "heterogeneity": "Assessed with I² and prediction intervals; random-effects models by default.",
}
DEMO_SECTIONS = {
    "rationale": (
        "Demo text: aspirin reduces cardiovascular events after a heart attack or stroke, but its benefit for people "
        "without cardiovascular disease is uncertain because it also increases bleeding."
    ),
    "objectives": (
        "Demo text: to assess the effects of daily low-dose aspirin, compared with placebo or no treatment, on major "
        "adverse cardiovascular events, all-cause mortality, and major bleeding in adults without established "
        "cardiovascular disease."
    ),
    "synthesis": (
        "Demo text: risk ratios will be pooled with random-effects meta-analysis. Heterogeneity will be assessed with "
        "I² and prediction intervals, with subgroup analyses by age and diabetes."
    ),
}


def _seed_demo_protocol_design(db: Session, project: models.Project) -> None:
    """Give the demo protocol a structured question, FINER assessment, analysis plan, and required sections, once."""
    protocol = project.protocol
    if protocol is None:
        return
    if not protocol.question:
        protocol.question = DEMO_QUESTION
        protocol.question_elements = DEMO_QUESTION_ELEMENTS
        protocol.finer = DEMO_FINER
        protocol.analysis_plan = DEMO_ANALYSIS_PLAN
    existing = set(
        db.scalars(select(models.ProtocolSection.key).where(models.ProtocolSection.project_id == project.id))
    )
    for key, content in DEMO_SECTIONS.items():
        if key not in existing:
            db.add(models.ProtocolSection(project_id=project.id, key=key, content=content))


def _seed_demo_review(db: Session, project: models.Project, users: dict[str, models.User]) -> None:
    """Give the demo project protocol settings, criteria, search strings, and fictional records, once."""
    db.flush()
    protocol = project.protocol
    if protocol is not None and not protocol.description:
        protocol.description = DEMO_PROJECT_DESCRIPTION
        protocol.suggested_criteria = DEMO_SUGGESTED_CRITERIA
        protocol.extraction_outline = "Country\nFollow-up (years)"

    _seed_demo_protocol_design(db, project)

    def project_has(model) -> bool:
        return db.scalar(select(model.id).where(model.project_id == project.id).limit(1)) is not None

    if not project_has(models.Criterion):
        db.add_all(
            models.Criterion(project_id=project.id, kind=kind, text=text, status=status, element=element)
            for kind, text, status, element in DEMO_CRITERIA
        )
    if not project_has(models.SearchStrategy):
        db.add_all(
            models.SearchStrategy(project_id=project.id, database=database, query=query)
            for database, query in DEMO_STRATEGIES
        )
    if not project_has(models.SearchRun):
        screener = users["screener1@omnireview.test"]
        for file_name, specs in DEMO_IMPORTS:
            run = models.SearchRun(
                project_id=project.id,
                kind="import",
                database=file_name,
                source_label=f"Demo sample records (fictional): {file_name}",
                result_count=len(specs),
                executed_by_id=users["owner@omnireview.test"].id,
            )
            for spec in specs:
                record = models.Record(
                    project_id=project.id,
                    title=spec.title,
                    authors="Demo A, Example B, Sample C",
                    year=spec.year,
                    doi=spec.doi,
                    abstract=spec.abstract,
                )
                if spec.screener_decision:
                    record.decisions.append(
                        models.ScreeningDecision(
                            stage=models.TITLE_ABSTRACT, reviewer_id=screener.id, decision=spec.screener_decision
                        )
                    )
                run.records.append(record)
            db.add(run)


def _waive_demo_registration(db: Session, project: models.Project, lead: models.User) -> None:
    exists = db.scalar(
        select(models.ProtocolRegistration.id).where(models.ProtocolRegistration.project_id == project.id).limit(1)
    )
    if exists is not None:
        return
    version = db.scalar(
        select(func.max(models.StageSnapshot.version)).where(
            models.StageSnapshot.project_id == project.id, models.StageSnapshot.stage == "protocol"
        )
    )
    db.add(
        models.ProtocolRegistration(
            project_id=project.id,
            registry_name="none",
            status="waived",
            protocol_version=version or 1,
            waiver_reason="Demo project for local testing; not a real review.",
            created_by_id=lead.id,
        )
    )
    db.flush()


def _advance_demo_workflow(db: Session, project: models.Project, lead: models.User) -> None:
    """Sign off the demo protocol and search (after deduplicating) so the demo opens at screening.

    Stages that are already signed off, or not yet reachable, are left as they are.
    """
    db.flush()
    for stage, note in (
        ("protocol", "Demo protocol approved for local testing."),
        ("search", "Demo records imported and deduplicated for local testing."),
    ):
        try:
            require_stage_open(db, project.id, stage)
        except WorkflowError:
            continue
        if stage == "search":
            _waive_demo_registration(db, project, lead)
            records = db.scalars(select(models.Record).where(models.Record.project_id == project.id)).all()
            duplicates = find_duplicates(records)
            for record in records:
                if record.id in duplicates:
                    record.duplicate_of_id = duplicates[record.id]
            db.flush()
        try:
            complete_stage(db, project, stage, lead, note)
        except WorkflowError as exc:
            print(f"Left the demo {stage} stage open: {exc}", file=sys.stderr)
            return


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

    _seed_demo_review(db, project, users)
    _advance_demo_workflow(db, project, users["lead@omnireview.test"])

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
