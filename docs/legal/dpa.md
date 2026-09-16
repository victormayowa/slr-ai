# Data Processing Agreement (template for institutions)

Version: 2026-09-16

> Template. This is a starting point for institutional customers who need a DPA under the GDPR or UK GDPR. It must be
> reviewed by a lawyer and adapted to each contract. Complete every "[TO COMPLETE" item.

This agreement is between [TO COMPLETE: customer] (the "Controller") and [TO COMPLETE: legal name of the operating
company] (the "Processor"), and forms part of the agreement for the OmniReview service.

## 1. Subject and duration

The Processor processes personal data contained in Customer Content for the duration of the service agreement, only to
provide the OmniReview service.

## 2. Nature and purpose, types of data, and data subjects

- **Purpose:** hosting, organising, analysing, and sharing systematic review projects, including AI-assisted tasks.
- **Types of data:** names, email addresses, institutional affiliations, and the contents of review projects, which may
  include personal data about study authors and, if the Controller uploads it, research participants (for example
  individual participant data).
- **Data subjects:** the Controller's users, study authors, and research participants.

## 3. Processor obligations

The Processor will: process only on documented instructions; ensure people authorised to process the data are bound by
confidentiality; implement the security measures in Annex 1; use sub-processors only as listed on the Sub-processors
page, with prior notice of changes and the right to object; assist with data subject requests and data protection
impact assessments; notify the Controller without undue delay (and within [TO COMPLETE: hours]) of a personal data
breach; delete or return data at the end of the service; and make available the information needed to demonstrate
compliance, including audits on reasonable notice.

## 4. International transfers

[TO COMPLETE: transfer mechanism, e.g. Standard Contractual Clauses module 2/3, and a transfer risk assessment.]

## Annex 1: Security measures

- TLS for all connections; encryption at rest for the disk holding the database and documents (full-disk encryption on
  the servers); field-level AES-256-GCM encryption for saved AI provider keys, two-factor secrets, and webhook secrets.
- Passwords hashed with bcrypt; optional two-factor sign-in; sessions invalidated when a password changes.
- Role-based access control per project; project data is never shared across customers.
- A hash-chained, append-only audit trail of changes in each project.
- Daily encrypted off-site backups, with restore drills; [TO COMPLETE: backup retention].
- Security headers, request size limits, rate limiting, and dependency vulnerability scanning in continuous integration.
- [TO COMPLETE: staff access controls, penetration testing cadence, incident response plan.]
