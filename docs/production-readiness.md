# Production readiness

Everything that has to be true before OmniReview serves real users, and how to check each item. Much of it is
automated:

```bash
docker compose -f docker-compose.prod.yml exec api python -m scripts.production_check --strict
```

That prints PASS, WARN, or FAIL for each check named below (in brackets). The same checks are on the administration
console's **Readiness** tab. A FAIL blocks launch; a WARN is a decision to make on purpose. Items marked *manual* can't
be checked by software.

## 1. Decisions to make first

- [ ] **Payment provider.** The app supports Stripe out of the box and invoiced plans assigned by an administrator.
  Another provider (Paddle, Lemon Squeezy) needs an adapter in `backend/billing.py` implementing the same five methods.
- [ ] **Plans and prices.** The seeded Free, Researcher, Team, and Institution plans and their limits are
  placeholders. Set them in **Admin → Plans** and, for Stripe, link each paid plan's monthly and yearly Stripe price ids.
- [ ] **Operating company and jurisdiction**, which the legal documents and payment provider need.
- [ ] **Where you host**, and in which region (this drives data residency statements and sub-processors).
- [ ] **Email provider** for transactional email (for example Postmark, Amazon SES, or Mailgun), with SPF, DKIM, and
  DMARC set up for the sending domain.
- [ ] **Off-site backup storage** (for example Backblaze B2 or AWS S3) for restic.
- [ ] **Which AI providers the server pays for.** Set their keys; users can still bring their own keys for others.
- [ ] **Benchmark thresholds** with a methodologist (defaults: screening recall ≥ 0.95, extraction accuracy ≥ 0.85,
  risk of bias kappa ≥ 0.60).

## 2. Server

The reference deployment is one VPS running `docker-compose.prod.yml`: Caddy (HTTPS and the web app), the API, the
worker, PostgreSQL with pgvector, and Redis.

- [ ] *Manual.* A server with at least 4 vCPUs, 16 GB RAM, and 100 GB SSD (the R environment and document storage are
  the large parts); Ubuntu 22.04 or 24.04; Docker Engine with the compose plugin; full-disk encryption if the provider
  offers it.
- [ ] *Manual.* A DNS A/AAAA record for your domain pointing at the server (Caddy gets the TLS certificate
  automatically on first start).
- [ ] *Manual.* Harden the server: `sudo DEPLOY_USER=deploy ops/harden-server.sh` (firewall allowing only SSH, HTTP,
  HTTPS; SSH keys only; fail2ban; automatic security updates; bounded Docker logs).
- [ ] *Manual.* Clone the repository to `/srv/omnireview` as the deploy user.

## 3. Configuration (`backend/.env` on the server)

Start from `backend/.env.example`. Never commit `.env`.

- [ ] `APP_ENV=production` **[app_env]**
- [ ] `JWT_SECRET_KEY`: a new random value, at least 48 characters:
  `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` **[jwt_secret]**
- [ ] `DATA_ENCRYPTION_KEY`: 32 random bytes, base64:
  `python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`. **Store an offline copy**
  (a password manager): losing it makes saved API keys, two-factor secrets, and webhook secrets unreadable.
  **[encryption_key]**
- [ ] `POSTGRES_PASSWORD` (in the shell or a root-only env file when running compose): a strong random password.
  **[database_password]**
- [ ] `SITE_ADDRESS` set to your domain; the compose file derives `CORS_ORIGINS` and `APP_URL` from it. **[cors]
  [app_url]**
- [ ] `BCRYPT_ROUNDS` unset or ≥ 12. **[password_hashing]**
- [ ] Email: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` (an address on your verified
  domain). Password resets depend on it. **[email_backend] [email]**
- [ ] `REQUIRE_EMAIL_VERIFICATION=true`. **[email_verification]**
- [ ] `SENTRY_DSN` for error reports. **[error_reporting]**
- [ ] AI provider keys the server pays for (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, ...), and a default chat model in
  the catalog. **[default_model]**
- [ ] `REQUIRE_VALIDATED_MODELS=true` once the models you offer have passed the benchmarks (see section 7).
  **[validated_models]**
- [ ] Billing: `BILLING_ENABLED=true`, `BILLING_PROVIDER=stripe` (or `manual`), `STRIPE_SECRET_KEY` (a live `sk_live_`
  key), `STRIPE_WEBHOOK_SECRET`. **[billing_enabled] [billing_provider] [stripe] [billing]**
- [ ] In the Stripe dashboard: add a webhook endpoint `https://YOUR-DOMAIN/api/billing/webhooks/stripe` for
  `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`,
  `customer.subscription.deleted`, and `invoice.payment_failed`; turn on the customer portal (plan changes and
  cancellation); set up tax collection if you need it. *Manual.*
- [ ] Contact addresses the literature services ask for: `NCBI_EMAIL`, `UNPAYWALL_EMAIL`, `CROSSREF_MAILTO`.
  **[literature_contacts]**
- [ ] Not set, or false: `ALLOW_PRIVATE_WEBHOOK_URLS`, `EMAIL_BACKEND=console`, `BILLING_PROVIDER=dev`. The API
  refuses to start with these in production. **[webhook_urls]**

## 4. First start

```bash
cd /srv/omnireview
export SITE_ADDRESS=reviews.example.org POSTGRES_PASSWORD=...
docker compose -f docker-compose.prod.yml up -d --build      # builds R and Pandoc into the image (20-40 minutes)
docker compose -f docker-compose.prod.yml exec api python -m scripts.make_admin you@example.org   # after registering
docker compose -f docker-compose.prod.yml exec api python -m scripts.production_check --strict
```

- [ ] The site loads over HTTPS and `/docs` shows the API documentation. *Manual.*
- [ ] Migrations are at the latest version. **[migrations]**
- [ ] Document storage is on the persistent volume and writable, with disk space to spare. **[document_storage]
  [storage]**
- [ ] Redis is reachable and the worker reports in every minute. **[redis] [worker]**
- [ ] R and Pandoc are available in the image. **[statistics] [publishing]**
- [ ] At least one platform administrator exists (register, then `scripts.make_admin`). **[administrator]**
- [ ] No demo accounts (`*@omnireview.test`); never run `scripts.seed_dev` in production (it refuses to).
  **[demo_accounts]**
- [ ] The smoke test passes with a real account:
  `docker compose -f docker-compose.prod.yml exec api python -m scripts.smoke_test --base-url http://127.0.0.1:8000
  --email you@example.org --password ... --colleague colleague@example.org --colleague-password ...` *Manual.*

## 5. Backups and recovery

- [ ] *Manual.* Configure `ops/backup.env` (`BACKUP_DIR`, `RESTIC_REPOSITORY`, `RESTIC_PASSWORD_FILE`, and the storage
  credentials restic needs), then initialise the repository once: `restic init`.
- [ ] *Manual.* Install and enable the timers:
  `sudo cp ops/systemd/* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now
  omnireview-backup.timer omnireview-restore-drill.timer`
- [ ] A backup has run in the last 26 hours. **[backup]**
- [ ] A restore drill has succeeded in the last 35 days (run `ops/restore-drill.sh` once by hand now).
  **[restore_drill]**
- [ ] *Manual.* Keep the restic password and `DATA_ENCRYPTION_KEY` somewhere other than the server. Losing the
  encryption key also makes saved storage-bucket credentials unreadable.
- [ ] *Manual.* Note in your support material that files customers keep in their own storage buckets
  (`docs/your-keys-and-storage.md`) are outside these backups: they back those up themselves.
- [ ] *Manual.* Rehearse a full restore on a spare server with `ops/restore.sh` before launch, and write down how long
  it took (your recovery time).

## 6. Monitoring

- [ ] Sentry receives errors (trigger one on staging to confirm). **[error_reporting]**
- [ ] *Manual.* An external uptime monitor (for example UptimeRobot or Better Stack) checks
  `https://YOUR-DOMAIN/healthz` every minute and alerts you.
- [ ] *Manual.* Platform administrators receive the daily operations report (AI spend on the server's keys, failures,
  and health problems) in the app and by email; read it for the first weeks.
- [ ] *Manual.* Set spending limits and alerts in each AI provider's billing console; the report shows spend, but only
  the provider can cap it.

## 7. AI governance

- [ ] *Manual.* In **Admin → Models & benchmarks**, run the statistics benchmark (it checks R against published
  metafor values) and at least one screening benchmark per model you offer (build a data set from SYNERGY with
  `backend/scripts/fetch_synergy_dataset.py`).
- [ ] *Manual.* Mark models that pass as validated, or exempt with a written reason, then set
  `REQUIRE_VALIDATED_MODELS=true`.
- [ ] *Manual.* Confirm each AI provider's API data terms: no training on your data, and their retention period.
  Record them in the Privacy Policy.

## 8. Legal and privacy

The documents in `docs/legal/` are templates written to match how OmniReview works. They are not legal advice.

- [ ] Every `[TO COMPLETE` placeholder filled in: Terms, Privacy Policy, Cookie Notice, Sub-processors, Data Processing
  Agreement, Acceptable Use Policy, Copyright Policy. **[legal_terms] [legal_privacy] [legal_cookies]
  [legal_subprocessors] [legal_dpa] [legal_acceptable-use] [legal_copyright]**
- [ ] *Manual.* A lawyer (and, if you process health data at scale, a data protection officer) has reviewed them for
  your jurisdiction and customers.
- [ ] *Manual.* If you serve EU/UK users: a record of processing activities, a transfer mechanism for AI providers
  outside the region, and a data protection impact assessment if customers upload individual participant data.
- [ ] *Manual.* Publish the Terms and Privacy Policy links on the sign-up form (built in) and your marketing site.
- [ ] *Manual.* Check the terms of use of each literature API for commercial use (PubMed E-utilities, OpenAlex,
  Crossref, Semantic Scholar, CORE, Unpaywall, Europe PMC) and get the API keys or agreements they require.
- [ ] *Manual.* Confirm licences for the appraisal tools you embed (RoB 2 and ROBINS-I under CC BY-NC-ND restrict
  commercial reuse of their texts; OmniReview uses its own short labels, but confirm this is acceptable) and for
  CSL citation styles.

## 9. Security

Built in: bcrypt passwords, optional two-factor sign-in, sessions that end when a password changes, email
verification, rate limits, security headers and a strict Content-Security-Policy, encrypted secrets, SSRF protection
for webhooks and feeds, scoped API tokens, a hash-chained audit trail, and dependency audits in CI.

- [ ] *Manual.* Turn on two-factor sign-in for every administrator account (Settings → Security).
- [ ] *Manual.* An external penetration test of staging, with findings fixed. Include: authorization between
  projects and accounts, API tokens, webhooks (SSRF), file upload and parsing, billing webhooks, and account recovery.
- [ ] *Manual.* A responsible disclosure contact (for example `security.txt` at `/.well-known/security.txt`).
- [ ] *Manual.* CI is green on the commit you deploy (lint, types, tests, migration drift, dependency audits, image
  builds, shellcheck).
- [ ] Institutional single sign-on (SAML/OIDC) and ORCID login are **not built yet** (planned with Keycloak, roadmap
  W1). Institutions that require SSO must wait, or sign in with passwords and two-factor sign-in.

## 10. Quality gates

- [ ] *Manual.* Accessibility: an audit against WCAG 2.2 AA of sign-up, sign-in, the dashboard, screening, extraction,
  and billing, with keyboard-only and screen reader passes; fix blocking issues.
- [ ] *Manual.* Load test staging with `ops/k6/smoke-load.js` at your expected peak (the defaults ramp to 50 concurrent
  users); p95 latency under 800 ms and under 1% errors. Separately, submit AI batches and analyses while watching the
  worker queue drain.
- [ ] *Manual.* A full review run end to end on staging by a methodologist, from protocol to submission package (see
  `docs/dev-testing.md` for the walkthrough), with the outputs checked.
- [ ] *Manual.* A billing run in Stripe test mode on staging: subscribe, change plan in the portal, fail a payment
  with a test card, cancel; check the plan and limits follow each step.

## 11. Launch day

- [ ] `ops/deploy.sh <tag>` from a tagged release (takes a backup, builds, waits for health, runs the readiness check,
  and rolls the code back if anything fails).
- [ ] Smoke test passes (section 4).
- [ ] Switch Stripe to live mode keys and confirm a real low-value purchase and refund.
- [ ] Watch Sentry, the uptime monitor, and the worker for the first hours.

## Known limits of this release

- One server: the database, worker, and web app share it. Scale by moving PostgreSQL to a managed service and running
  more workers (`--scale worker=N`) before adding servers.
- Documents are on the server's disk (backed up nightly); an S3-compatible store needs an implementation of
  `DocumentStorage` in `backend/storage.py`.
- No single sign-on or ORCID login yet (see section 9).
- The PROSPERO registry has no submission API; registration there stays a guided copy-and-paste.
