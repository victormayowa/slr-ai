# Workflow and sign-offs

## Stages

A project has ten stages, completed in order:

1. **Protocol**: the review question, eligibility criteria, search strategies, extraction fields, analysis plan, and
   PRISMA-P sections.
2. **Search and deduplication**: searches and imports, protocol registration (or a recorded waiver), PRESS peer review
   (or a waiver), and reviewed duplicates.
3. **Title and abstract screening**: an include or exclude decision for every record, unless an accepted stopping rule
   covers the rest.
4. **Full-text screening**: a decision and an exclusion reason for every report sought.
5. **Data extraction**: final values for every required field, with discrepancies reconciled. Signing off locks the
   extraction data set.
6. **Risk of bias assessment**: a signed-off assessment for every included study.
7. **Synthesis**: approved analyses with final results for every primary outcome.
8. **Certainty of evidence**: signed-off GRADE assessments, Evidence to Decision frameworks, and approved interpretation.
9. **Manuscript**: every sentence verified or acknowledged, references checked, the PRISMA 2020 checklist complete, and
   every author's approval of the current version.
10. **Submission and deposit**: a submission package built from the approved manuscript, passing its readiness checks,
    and confirmed by the corresponding author.

## Requirements and sign-off

A stage accepts changes only while it is open: every earlier stage is signed off and it isn't. Each screen shows the
stage's requirements. When they are all met, someone whose role allows it signs the stage off with a note. Signing off
stores a hashed snapshot of the stage's content.

## Reopening a stage

A signed-off stage is reopened with a written rationale. Every later stage reopens too, because its work may depend on
what changes. Living updates and peer review re-analysis use the same mechanism.

## Audit trail

Every change, decision, AI run, and sign-off is recorded in an append-only audit trail. Each event's hash covers the
previous event, so tampering with history is detectable. People with the auditor role can read the trail and export
reports.
