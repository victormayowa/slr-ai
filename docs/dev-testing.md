# Testing the whole flow in development

How to run OmniReview on your own machine without Docker and try every part of it: the automated checks, a scripted
smoke test, and a walkthrough of each feature with the demo accounts.

## 1. One-time setup

From the repository root:

```bash
cd backend
cp .env.example .env
./scripts/setup_local_services.sh     # PostgreSQL + pgvector, Redis, database role, dev and test databases (asks for sudo)
uv sync
./scripts/setup_r_env.sh              # optional: R for analyses (prints RSCRIPT_PATH for .env)
./scripts/setup_publishing_tools.sh   # optional: Pandoc and Tectonic for Word/PDF export (prints paths for .env)
cd .. && npm ci
```

In `backend/.env`, set these for development testing:

```bash
JWT_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">
DATA_ENCRYPTION_KEY=<python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())">
APP_URL=http://localhost:5173

# Emails (password resets, verification links, notifications) are written to the log and to this folder.
EMAIL_BACKEND=console
EMAIL_OUTBOX_DIR=/tmp/omnireview-outbox

# Plan limits on, with simulated payments.
BILLING_ENABLED=true
BILLING_PROVIDER=dev

# Let webhooks reach the local receiver.
ALLOW_PRIVATE_WEBHOOK_URLS=true

# At least one AI provider key, or add your own key in Settings after signing in.
GEMINI_API_KEY=...
```

## 2. Start everything

Four terminals:

```bash
# 1. API (applies migrations first)
cd backend && uv run alembic upgrade head && uv run python -m scripts.seed_dev && uv run uvicorn main:app --reload

# 2. Worker: AI batches, analyses, emails, webhooks, surveillance, reminders, account deletions, daily reports
cd backend && uv run arq workers.ai_worker.WorkerSettings

# 3. Web app
npm run dev

# 4. Webhook receiver (optional, section 5)
cd backend && uv run python -m scripts.dev_webhook_receiver --secret <webhook secret>
```

Open http://localhost:5173. Every demo account's password is `Review-Dev-2026!`; the login page has a picker in
development builds. `owner@omnireview.test` is a platform administrator. The demo project belongs to Demo University,
which is on the Team plan; `outsider@omnireview.test` is on the Free plan.

## 3. Automated checks

```bash
cd backend
uv run ruff check . && uv run ruff format --check . && uv run mypy .
uv run pytest                                   # erases and rebuilds the test database
uv run alembic check                            # migrations match the models
uv run python -m scripts.smoke_test             # against the running API, as the demo owner
uv run python -m scripts.smoke_test --billing   # also a simulated checkout
uv run python -m scripts.production_check       # expect FAILs in development; read what production needs
cd .. && npm run lint && npx tsc -b && npm test && npm run build
```

## 4. Walk through a review

Sign in as the account named in each step (open a private window for a second account).

1. **Setup and question** (owner): open the demo project. In Project Setup, pick an AI model. Explore the topic, then
   structure the question (PICO) and ask the AI for a suggestion; accept or edit it.
2. **Protocol** (methodologist): review criteria, the analysis plan, and protocol sections; export the protocol
   document; sign off the protocol stage (the demo already did this once, so reopen it with a reason to try).
3. **Search** (lead): run a PubMed search from a strategy, import `.ris`/`.csv` files, check PRESS, deduplicate, and
   look at the PRISMA diagram.
4. **Screening** (screener1 and screener2): turn on dual screening in the review settings (lead), screen the same
   records differently in the two windows, then resolve conflicts as the lead. Try **Get AI suggestions** and
   **Prioritize the queue**. Open a record's 💬 button and mention `@lead@omnireview.test`; the lead's bell shows it.
5. **Full texts and extraction** (extractor): retrieve open-access full texts or upload a PDF, link reports to
   studies, run AI extraction, accept grounded values, reconcile, and sign off (locks the data set).
6. **Risk of bias** (extractor, then methodologist): RoB 2 per outcome, with the algorithm's suggestions.
7. **Synthesis** (statistician; needs R): create a pairwise analysis, run it, approve it, then in **AI Governance** enter
   the run's number under Reproducibility checks and choose **Rerun** to confirm it reproduces.
8. **Certainty** (methodologist): GRADE each outcome and read the summary of findings.
9. **Manuscript and publication** (lead): start the manuscript, draft and verify sections, add authors, create a
   version, approve as each author, export; build a submission package and try a Zenodo sandbox deposit.
10. **Living review** (lead): schedule surveillance and check alerts.

## 5. Collaboration, governance, and integrations

- **Team & Tasks** (owner): invite a new address. The invitation link is shown and also written to
  `/tmp/omnireview-outbox`. Register with that address in a private window and open the link. Assign a task with a due
  date of today; the worker's daily reminder runs at 07:00, or call it now:
  `uv run python -c "from database import SessionLocal; import notifications as n; n.remind_due_tasks(SessionLocal())"`.
- **AI Governance** (owner): run a calibration on already-screened records; require calibration in review settings and
  see AI screening refuse to run until a report is accepted.
- **Export & Integrations** (owner): download records as RIS/BibTeX/CSV, a Rayyan file, and the FHIR bundle; import the
  Rayyan file back as decisions.
- **Webhooks**: add `http://127.0.0.1:9000/hook` for `task.*`, copy the secret, start the receiver with it, add a task,
  and within a minute the receiver prints the delivery with "signature OK".
- **API tokens** (Settings): create a read token, then
  `curl -H "Authorization: Bearer omr_..." http://localhost:8000/api/projects`; a POST with it answers 403. The full API
  is at http://localhost:8000/docs.
- **Help assistant**: ask "How do I invite someone?"; the answer cites the documentation.

## 6. Billing and plan limits

- **Hit a limit**: sign in as `outsider@omnireview.test` (Free plan, one project already) and create a second project:
  the app explains the limit and links to Billing.
- **Upgrade**: open **Billing**, choose Researcher, and on the simulated checkout page choose **Simulate successful
  payment**. The plan changes through the same webhook path Stripe uses; the second project can now be created.
- **Failed payment**: start a checkout again and choose **Simulate failed payment**: the subscription becomes past due
  and a notification appears.
- **Cancel**: Billing → Cancel returns the account to Free.
- **Administrator** (owner): Admin → Plans to edit limits; Subscriptions to assign a plan to an organization; Users to
  deactivate an account or reset its two-factor sign-in; System for health; Costs for AI spend.
- **Stripe test mode** (optional): set `BILLING_PROVIDER=stripe` with `sk_test_` keys, add Stripe price ids to a plan in
  Admin → Plans, run `stripe listen --forward-to localhost:8000/api/billing/webhooks/stripe` (Stripe CLI) and use its
  `whsec_` secret as `STRIPE_WEBHOOK_SECRET`, then check out with card `4242 4242 4242 4242`.

## 7. Accounts and privacy

- **Password reset**: sign out, choose **Forgot password**, enter a demo address, open the link from
  `/tmp/omnireview-outbox`, set a new password, and sign in. (Rerun `scripts.seed_dev` to reset demo passwords.)
- **Email verification**: set `REQUIRE_EMAIL_VERIFICATION=true`, restart the API, register a new account: sign-in is
  refused until the link from the outbox is opened.
- **Two-factor sign-in**: Settings → Security → set up with an authenticator app (paste the secret or the otpauth
  link), save the recovery codes, sign out and back in with a code; try a recovery code once.
- **Your data**: Settings → Privacy → download your data.
- **Account deletion**: register a throwaway account, create a project, request deletion. To see it carried out now,
  set `ACCOUNT_DELETION_GRACE_DAYS=0`, restart the worker, and wait for minute 20 of the hour (or run
  `uv run python -c "from database import SessionLocal; from account_routes import run_due_deletions as r; print(r(SessionLocal()))"`);
  the account can no longer sign in and its private project is gone.

## 8. Operations, locally

- **Health**: http://localhost:8000/readyz, and Admin → System (the worker check turns green once the worker runs).
- **Daily report**: `uv run python -c "from database import SessionLocal; import ops; ops.daily_report(SessionLocal())"`
  and look at the owner's notifications.
- **Load test** (needs k6): `k6 run -e BASE_URL=http://localhost:8000 -e EMAIL=owner@omnireview.test -e
  PASSWORD=Review-Dev-2026! ops/k6/smoke-load.js` (expect higher latency than production on a laptop).
- Backups, restores, and deploys (`ops/*.sh`) use Docker Compose on the server; rehearse them on a staging VPS.
