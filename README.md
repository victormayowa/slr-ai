# OmniReview

An AI-assisted platform for systematic reviews and meta-analyses, built around human decisions: the AI suggests, reviewers decide, and every number should trace back to recorded data.

> **Status:** feature-complete for launch preparation (roadmap W0–W19): the full review workflow from protocol to living updates, collaboration, AI governance, the public API, billing with plan limits, account security (password resets, email verification, two-factor sign-in), privacy tools, and production operations (health checks, backups, deploys). Before launch, work through [docs/production-readiness.md](docs/production-readiness.md): several items (legal review, penetration test, accessibility audit, payment provider setup) can only be done by people. Institutional single sign-on is not built yet. To try everything locally, follow [docs/dev-testing.md](docs/dev-testing.md).

## Stack

- **Frontend:** React 19, TypeScript, Vite (`src/`)
- **Backend:** FastAPI, SQLAlchemy, Python 3.12, managed with [uv](https://docs.astral.sh/uv/) (`backend/`)
- **AI providers:** Anthropic, Google Gemini, OpenAI, Alibaba Qwen, Moonshot Kimi, DeepSeek, Zhipu GLM, Mistral. Each project pins a model from the catalog; server keys and users' own encrypted keys are both supported
- **Literature sources:** PubMed (E-utilities), OpenAlex
- **Statistics:** R 4.4 with metafor, meta, netmeta, mada, clubSandwich, lme4, bayesmeta, and robvis, run as scripts the API generates; every run stores its script, data, seed, and R session
- **Infrastructure:** PostgreSQL, Redis, Caddy, Docker Compose

## Quick start with Docker

```bash
docker compose up --build
```

- App: http://localhost:5173
- API docs: http://localhost:8000/docs

To enable AI features, copy `backend/.env.example` to `backend/.env` and add at least one provider key, or sign in and add your own key under Settings. The development compose file supplies throwaway `JWT_SECRET_KEY` and `DATA_ENCRYPTION_KEY` values; never use them in production.

## Billing, accounts, and administration

Plans and their limits (projects, members, monthly records, AI credits, storage, surveillance schedules, analysis time, API access, webhooks) are enforced when `BILLING_ENABLED=true`. Payments go through a provider adapter in `backend/billing.py`: `manual` (plans assigned by an administrator), `stripe`, or `dev` (a simulated checkout for local testing). Platform administrators manage plans, subscriptions, organizations, users, the model catalog, system health, readiness, and AI costs at `/admin`. Accounts have password resets, email verification, two-factor sign-in, a data download, and self-service deletion. See [docs/billing-and-accounts.md](docs/billing-and-accounts.md).

## Demo accounts

For local testing, `uv run python -m scripts.seed_dev` (run automatically by `docker compose up`) creates a demo team on the project *Aspirin for primary prevention of cardiovascular events*. Every account uses the password `Review-Dev-2026!`, and development builds of the login page offer a picker that fills them in. The script refuses to run when `APP_ENV=production`.

| Account | Project role |
|---|---|
| owner@omnireview.test | owner |
| lead@omnireview.test | lead_reviewer |
| methodologist@omnireview.test (or ORCID `0000-0002-1825-0097`) | methodologist |
| statistician@omnireview.test | statistician |
| clinician@omnireview.test (or `c.clinic@demo-university.test`) | clinical_expert |
| screener1@omnireview.test, screener2@omnireview.test | screener |
| extractor@omnireview.test | extractor |
| auditor@omnireview.test | auditor |
| viewer@omnireview.test | viewer |
| outsider@omnireview.test | none (owns a separate private project) |

What each role may do is defined in [`backend/permissions.py`](backend/permissions.py). `owner@omnireview.test` is also a platform administrator; Demo University is on the Team plan and the outsider stays on Free, so plan limits can be tried with `BILLING_ENABLED=true BILLING_PROVIDER=dev`.

## Collaboration, governance, and the API

- **Team:** invitations by email (valid 14 days, accepted only by the invited address), ownership transfer, per-member competing-interest declarations, tasks with deadline reminders, comments anchored to records, passages, extraction cells, and manuscript sentences, with @mentions and notifications. The help assistant answers from `docs/` and cites the sections it used.
- **AI governance:** benchmark data sets and runs decide whether a model may be offered (`REQUIRE_VALIDATED_MODELS=true` enforces it), each project can require its own calibration before AI screening runs, and the bias, SOP, provenance, validation, and reproducibility reports show how the review was actually done. Grant administrator rights with `uv run python -m scripts.make_admin someone@example.org`.
- **Interoperability:** records export as RIS, BibTeX, EndNote XML, CSV, and JSON; decisions move to and from Rayyan and Covidence; results export as RevMan-style and GRADEpro-style sheets, JATS XML, and an EBMonFHIR bundle.
- **Public API:** documented at `/docs`, with personal access tokens (`omr_…`, read or write, optionally limited to projects and given an expiry) and webhooks signed with `X-OmniReview-Signature: sha256=…`.

## Local development without Docker

**Backend**

```bash
cd backend
cp .env.example .env    # then set JWT_SECRET_KEY, DATA_ENCRYPTION_KEY, and any server provider keys
./scripts/setup_local_services.sh  # one time: pgvector, Redis, database role, dev and test databases
uv sync
uv run alembic upgrade head        # create or update the database schema
uv run python -m scripts.seed_dev  # optional: demo accounts and projects
uv run uvicorn main:app --reload
uv run arq workers.ai_worker.WorkerSettings  # in a second terminal: runs AI, analysis, and embedding jobs, and hourly surveillance
```

Statistical analyses need R. `./scripts/setup_r_env.sh` installs R and its packages into `~/.local/share/omnireview/r` without sudo (it downloads micromamba) and prints the `RSCRIPT_PATH` line to add to `backend/.env`.

Manuscript export uses Pandoc (Word, LaTeX) and Tectonic (PDF, which downloads TeX packages on first use). `./scripts/setup_publishing_tools.sh` installs both, with rsvg-convert, into `~/.local/share/omnireview/tools` without sudo and prints the lines for `backend/.env`. Without Pandoc, Word export falls back to a simpler document.

OmniReview requires PostgreSQL 16 with the pgvector extension in every environment; there is no SQLite fallback. The setup script expects PostgreSQL 16 to be installed already (`sudo apt install postgresql`) and asks for your sudo password.

**Frontend**

```bash
npm ci
npm run dev
```

The frontend calls `http://localhost:8000` unless `VITE_API_URL` is set.

## Checks

```bash
# Backend (from backend/)
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run pytest                 # erases and rebuilds TEST_DATABASE_URL; add `-m live` for PubMed/OpenAlex tests; R tests skip without RSCRIPT_PATH

# Frontend (from the repository root)
npm run lint
npx tsc -b
npm test
npm run build
```

CI runs all of these on every push and pull request, tests the backend against PostgreSQL, audits dependencies, and builds both Docker images.

## Production

`docker-compose.prod.yml` runs Caddy (the web app, automatic HTTPS, and the `/api` and `/docs` proxy), the API, the background worker, PostgreSQL, and Redis on a single server, with persistent volumes for the database and documents. The API image includes R and Pandoc for analyses and exports.

```bash
SITE_ADDRESS=reviews.example.org POSTGRES_PASSWORD=... docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec api python -m scripts.production_check --strict
```

**Read [docs/production-readiness.md](docs/production-readiness.md) first.** It lists every setting, decision, and check before launch. The API refuses to start in production with unsafe settings, and `scripts.production_check` reports everything else. Operations scripts live in `ops/`: `harden-server.sh`, `deploy.sh` (backup, health gate, rollback), `backup.sh` and `restore.sh` (with restic off-site copies), `restore-drill.sh`, systemd timers, and a k6 load test.

## Configuration

All backend settings are documented in [`backend/.env.example`](backend/.env.example). Never commit `backend/.env`.
