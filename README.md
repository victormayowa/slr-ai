# OmniReview

An AI-assisted platform for systematic reviews and meta-analyses, built around human decisions: the AI suggests, reviewers decide, and every number should trace back to recorded data.

> **Status:** early prototype. Review data, workflow sign-offs, and the audit trail are stored in PostgreSQL, but full-text retrieval, statistical meta-analysis, background jobs, and email invitations are not built yet. See [docs/roadmap.md](docs/roadmap.md) for the full build plan.

## Stack

- **Frontend:** React 19, TypeScript, Vite (`src/`)
- **Backend:** FastAPI, SQLAlchemy, Python 3.12, managed with [uv](https://docs.astral.sh/uv/) (`backend/`)
- **AI providers:** Anthropic, Google Gemini, OpenAI, Alibaba Qwen, Moonshot Kimi, DeepSeek, Zhipu GLM, Mistral. Each project pins a model from the catalog; server keys and users' own encrypted keys are both supported
- **Literature sources:** PubMed (E-utilities), OpenAlex
- **Infrastructure:** PostgreSQL, Redis, Caddy, Docker Compose

## Quick start with Docker

```bash
docker compose up --build
```

- App: http://localhost:5173
- API docs: http://localhost:8000/docs

To enable AI features, copy `backend/.env.example` to `backend/.env` and add at least one provider key, or sign in and add your own key under Settings. The development compose file supplies throwaway `JWT_SECRET_KEY` and `DATA_ENCRYPTION_KEY` values; never use them in production.

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

What each role may do is defined in [`backend/permissions.py`](backend/permissions.py).

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
uv run arq workers.ai_worker.WorkerSettings  # in a second terminal: runs AI screening, extraction, appraisal, and embedding jobs
```

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
uv run pytest                 # erases and rebuilds TEST_DATABASE_URL; add `-m live` for PubMed/OpenAlex tests

# Frontend (from the repository root)
npm run lint
npx tsc -b
npm test
npm run build
```

CI runs all of these on every push and pull request, tests the backend against PostgreSQL, audits dependencies, and builds both Docker images.

## Production

`docker-compose.prod.yml` runs Caddy (static frontend, automatic HTTPS, `/api` proxy), the API, a background AI worker, PostgreSQL, and Redis on a single server:

```bash
SITE_ADDRESS=app.example.com POSTGRES_PASSWORD=change-me \
  docker compose -f docker-compose.prod.yml up -d --build
```

`backend/.env` must contain production secrets: a random `JWT_SECRET_KEY`, a random `DATA_ENCRYPTION_KEY` (it encrypts users' saved API keys, so changing or losing it makes those keys unreadable), any server provider API keys, and optionally `SENTRY_DSN`.

## Configuration

All backend settings are documented in [`backend/.env.example`](backend/.env.example). Never commit `backend/.env`.
