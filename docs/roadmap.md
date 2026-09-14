# OmniReview: Complete Evidence-Synthesis Platform Build Plan

## Context

**Where things stand.** The evaluation showed OmniReview is a single-page prototype:
- PRISMA numbers are made up, AI failures return simulated verdicts, and the AI screens placeholder abstracts.
- Auth is unenforced, nothing is saved, and the build fails.
- Most features are placeholders.

**Goal.** The user wants a **public, paid SaaS, self-hosted on a VPS**. It should work as a human-in-the-loop "scientific operating system" covering the whole review, from topic selection to publication and living updates. It must align with PRISMA 2020 / PRISMA-P / PRISMA-S / PRISMA-trAIce, Cochrane guidance, RAISE, PROSPERO, and GRADE.

**Decisions already made:**
- Public launch happens **only when every feature in the user's list is complete**.
- Support 8 LLM providers: Anthropic, Gemini, OpenAI, Qwen, Kimi, DeepSeek, GLM, Mistral.
- The payment provider is still open, so billing sits behind an interface.
- **No GxP / 21 CFR Part 11 formalities**, but audit and governance stay strong.

**Intended outcome.** Every number, claim, judgment, and figure the platform produces can be traced to source evidence, a human approval, and a reproducible computation.

---

## 1. Non-negotiable principles (enforced in code and schema, not just in docs)

1. **Structural human gates.** Final inclusion/exclusion, extraction values, RoB judgments, statistical model choice, GRADE ratings, interpretation, and manuscript export must be stored with a *human* actor and a rationale. AI rows are `kind='suggestion'` and can never be final. This is enforced by DB constraints plus service checks.
2. **Grounding.** Every AI output carries evidence pointers (document span IDs, analysis result IDs, record IDs). A validator rejects any output whose quotes or numbers can't be found in the cited source.
3. **Provenance.** Every AI call writes an `ai_runs` row: provider, exact model version, prompt template and version hash, input hash, output, tokens, cost, latency, timestamp, and the user who triggered it.
4. **Append-only audit log** with a hash chain. Edits are new versions and never overwrite.
5. **Protocol first.** Every stage references a locked protocol version. Any deviation is recorded as an amendment with a justification.
6. **No simulated output.** A failure shows as a visible error state.
7. **Reproducibility.** Every analysis stores the locked dataset snapshot, the generated script, the seed, the R/Python session info, and the container image digest.
8. **Blinding by default.** In dual screening, a reviewer doesn't see the other reviewer's decision or the AI label until they record their own. Projects can change this, and the change is logged.

---

## 2. Coverage: what exists today and where the plan builds it

| Feature group (user's list) | Current app | Built in |
|---|---|---|
| Topic ideation, gap detection, feasibility | Missing | W4 |
| PICO/PECO/SPIDER/FINER structuring | Dropdown label only | W4 |
| Protocol drafting (PRISMA-P), registration, versioning | LLM criteria list, not saved | W4 |
| Machine-readable eligibility engine | Free-text criteria | W4, W7 |
| Roles, team management, tasks, comments | Fake share modal | W1, W15 |
| Audit trail and provenance | Missing | W2, W3 |
| COI, funding, ethics, copyright handling | Missing | W4, W6, W19 |
| Search strategy generation, vocabularies, validation, PRESS | LLM boolean strings, edits discarded | W5 |
| Multi-database execution, grey literature, trial registries | PubMed plus mislabeled OpenAlex | W5 |
| Citation chasing | Missing | W5 |
| Import formats and deduplication with provenance | CSV only, exact-match dedup | W5, W17 |
| Title/abstract screening, active learning, stopping rules | AI-only, not saved | W7 |
| Dual screening, adjudication, κ | Missing | W7 |
| Full-text retrieval, OCR, layout, tables, supplements | Fake (uses the title) | W6 |
| Entity linking and ontologies | Missing | W6 |
| Study-vs-report linking | Missing | W6 |
| Extraction forms, evidence spans, stats conversion, units | Abstract-based, no spans | W8 |
| Dual extraction, author contact, locked dataset | Missing | W8 |
| RoB tool selection and signalling-question workflows | 4 tools, domain-level LLM guess | W9 |
| Effect sizes, FE/RE models, heterogeneity, forest/funnel plots | LLM-written "meta-analysis" text | W10 |
| Subgroup, sensitivity, meta-regression, RVE, publication bias | Missing | W10 |
| Network MA, DTA MA, IPD MA, complex designs, SWiM | Missing | W10 |
| GRADE, SoF tables, absolute effects, NNT, EtD | Missing | W11 |
| Prior-review comparison, limitations, plain language | Missing | W11 |
| Grounded manuscript drafting and claim verification | Missing | W12 |
| Reporting checklists (PRISMA family, trAIce, MOOSE, SWiM, AMSTAR 2) | Missing | W12 |
| Citation styles, DOI verification, retraction checks | Missing | W12 |
| Journal selection, submission package, readiness checks | Missing | W13 |
| Repository deposit (OSF/Zenodo/Figshare/Dryad/Git) | Missing | W13 |
| Peer-review response support | Missing | W13 |
| Living reviews, surveillance, versioned releases, evidence maps | Missing | W14 |
| AI validation, benchmarks, calibration, bias monitoring | Missing | W16 |
| QA reports, SOPs, model change management | Missing | W16 |
| SSO, encryption, API keys, public API, FHIR | Partial password login only | W1, W17 |
| PRISMA flow diagram | Hardcoded numbers | W7 |
| FAQ chatbot | Ungrounded LLM | W15 (answers grounded in the docs) |

---

## 3. Target architecture

```
                         Caddy (TLS, SPA, /api)
                                 │
  ┌──────────── APP NODE (≈8 vCPU / 32 GB) ──────────────────────────┐
  │ web (React SPA)   api (FastAPI, workflow engine, BFF auth)       │
  │ keycloak (identity: password, reset, MFA, ORCID, SAML/OIDC SSO)  │
  │ postgres 16 + pgvector   redis (queues, rate limits, cache)      │
  └───────────────────────────────┬──────────────────────────────────┘
                     WireGuard private network
  ┌──────────── COMPUTE NODE (≈16 vCPU / 64 GB) ─────────────────────┐
  │ workers (arq queues: ai, search, docs, embed, export, email, cron)│
  │ docling-worker (layout, tables, figures) + OCRmyPDF/Tesseract     │
  │ grobid (references, header metadata, citation parsing)           │
  │ stats (R 4.x + Plumber, renv-locked: metafor, meta, netmeta,     │
  │        multinma/Stan, bayesmeta, mada, diagmeta, clubSandwich,    │
  │        robvis, estmeansd, lme4/glmmTMB, dmetar-style helpers)     │
  │ export (Pandoc + citeproc, LaTeX) · embeddings (sentence-transformers)│
  └──────────────────────────────────────────────────────────────────┘
External: S3-compatible object storage (PDFs, snapshots, exports; versioned),
          transactional email, Sentry, uptime monitor, payment adapter, LLM APIs,
          literature/registry APIs, repository APIs (OSF/Zenodo/Figshare/Dryad/GitHub)
```

### Key technology choices
| Need | Choice | Why / licence note |
|---|---|---|
| Identity, SSO, reset, MFA | **Keycloak** (self-hosted) + API as backend-for-frontend (server-side session, httpOnly cookie) | Covers institutional SAML/OIDC, ORCID login and MFA. Replaces the custom JWT code. Apache-2.0 |
| Jobs and scheduling | arq on Redis | Async-native, fits the async LLM calls. Handles cron for living reviews |
| Workflow engine | Stage state machine in Postgres, enforced by the service layer | Approval gates stay transactional with the data. A separate engine isn't needed at this scale |
| Evidence and citation graph | Postgres edge tables + recursive CTEs; pgvector for similarity | One datastore, easy backups |
| Statistics | **R service** (Plumber) with a renv lockfile, run as a separate container | The required methods (HKSJ, REML, selection models, netmeta, HSROC, RVE, IPD) only exist together in R. GPL packages are used as a network service, not distributed |
| PDF parsing | Docling (MIT) + OCRmyPDF (MPL-2.0) + GROBID (Apache-2.0); JATS XML preferred when available | Layout, tables, figures, OCR, references. **Avoid PyMuPDF (AGPL)** |
| Active learning | ASReview-style models (Apache-2.0) over local embeddings + TF-IDF | Proven approach, runs on CPU |
| Manuscript editor | TipTap/ProseMirror with evidence-link marks, comments, track changes | Claim-level grounding in the UI |
| Citations and exports | Pandoc citeproc with CSL styles; DOCX/LaTeX/PDF | Pandoc is GPL but invoked as a CLI. **Avoid citeproc-js (CPAL/AGPL)** |
| Frontend | React 19 + React Router + TanStack Query + pdf.js viewer + SVG charts rendered from R's JSON | Interactive plots; publication versions rendered by R |

### Backend layout (`backend/app/`)
```
api/routes/        one router per domain below; api/deps.py (session, project role, gate, entitlement)
domain/            workflow (stages, gates, amendments), audit (hash chain), versioning
modules/           topic, protocol, search (connectors/, query_ast/, vocab/), records, dedup,
                   studies, documents (ingest, parse, spans, entities), screening (active_learning,
                   stopping), extraction (forms, stats_convert, units), appraisal (tools/*),
                   synthesis (stats client, swim), certainty (grade), manuscript (drafting,
                   verifier, citations), publication (journals, package, deposit, peer_review),
                   living, collaboration, governance (benchmarks, calibration, reports), billing
llm/               providers (anthropic, gemini, openai_compat), registry, prompts/, grounding validator
workers/           arq task modules and cron
stats-service/     (separate container) R Plumber API + script templates + tests
```

### Data model domains (Postgres, Alembic)
- **Identity and tenancy:** users (Keycloak subject), organizations, org_members, projects, project_members (role), invitations, api_tokens, user_api_keys (AES-GCM), sessions
- **Workflow:** project_stages, gate_approvals (stage, approver, rationale, timestamp), amendments, audit_log (append-only, `prev_hash`)
- **Topic and protocol:** topic_explorations, questions (framework + structured elements), finer_assessments, feasibility_reports, protocols (versioned, locked_at, sha256), protocol_sections, criteria (machine-readable: element, operator, value, type), analysis_plans (prespecified outcomes, subgroups, sensitivity), declarations (COI, funding, ethics), registrations (target, external_id, DOI, status)
- **Search:** search_strategies (versioned query AST + rendered strings per database), vocab_terms, seed_articles, press_reviews, search_runs (connector, interface, executed_at, filters, count, raw file), import_files
- **Records and studies:** records, record_sources, record_identifiers (doi/pmid/pmcid/nct/…), duplicate_groups (with merge provenance), studies, study_reports, citation_edges
- **Documents:** documents (type, origin, storage_key, sha256, licence/OA status), doc_versions, doc_spans (page, bbox, section, table/figure refs), doc_tables, entities, entity_links (ontology, code)
- **Screening:** screening_suggestions (AI), screening_decisions (human, stage, reason code), conflicts, adjudications, al_models (version, metrics), stopping_evaluations, qa_samples
- **Extraction:** form_templates, form_fields, extraction_suggestions, extraction_values (value, unit, span_ids, confidence, flags), discrepancies, reconciliations, author_contacts, dataset_snapshots (locked)
- **Appraisal:** appraisal_tools (versioned question banks), appraisal_answers, domain_judgments, appraisal_signoffs, reporting_checklists
- **Synthesis:** outcomes (harmonized sets), effect_inputs, analyses (spec, model, justification, statistician_signoff), analysis_runs (script, results JSON, plots, sessionInfo, image digest, seed), swim_syntheses
- **Certainty:** grade_assessments (outcome, domain ratings + rationale + linked analyses), sof_tables, etd_frameworks
- **Manuscript:** manuscripts, manuscript_versions, sections, claims (sentence → evidence links, verification status), citations, checklist_items, approvals
- **Publication:** journal_candidates, submission_packages, readiness_checks, deposits, reviewer_comments, responses
- **Living:** surveillance_schedules, surveillance_runs, alerts, impact_assessments, review_releases (DOI, changelog)
- **Collaboration:** tasks, comments (polymorphic anchors: record, span, cell, sentence), notifications
- **AI governance:** ai_runs, model_catalog (pinned versions, benchmark status), prompt_templates, benchmark_runs, calibration_reports, bias_reports
- **Billing:** plans, entitlements, subscriptions, usage_events, billing_events

### Workflow stages and mandatory gates
| Stage | Can only start when | Human sign-off required (role) |
|---|---|---|
| Protocol | Question structured and feasibility reviewed | Lead reviewer + methodologist lock the protocol |
| Search | Protocol locked (registered or explicitly waived with a reason) | PRESS peer review completed; methodologist approves strategies |
| Deduplication | All planned searches run or waived | Uncertain duplicate pairs resolved by a human |
| Title/abstract screening | Dedup done | Every record has final human decision(s); conflicts adjudicated; stopping rule accepted with justification |
| Full-text screening | T/A complete | Human decision plus exclusion reason per report |
| Extraction | Full-text includes finalized | Dual verification; discrepancies reconciled; **dataset locked** |
| Appraisal | Dataset locked | Every domain judgment signed off with rationale |
| Synthesis | Appraisal signed off | Statistician approves model choice; post-hoc analyses flagged |
| GRADE | Synthesis runs final | Methodologist signs off each outcome |
| Manuscript | **Evidence base locked** (snapshot) | All claims verified or acknowledged; every author approves |
| Submission / deposit | Readiness checks pass | Corresponding author confirms (the platform never auto-submits) |
| Living update | Release published | Same gates re-run for the delta |

---

## 4. Workstreams

### W0: Foundations and emergency fixes
- `git init` with a correct `.gitignore`. Rotate the Gemini key in `backend/.env` and add `.env.example`.
- Fix the TypeScript `author`/`authors` errors and the OpenAI `{{…}}` bug. Delete the simulated fallbacks.
- Tooling: uv + `pyproject.toml`, ruff, mypy, pytest; oxlint, tsc, vitest, Playwright.
- Compose files for both nodes, GitHub Actions CI, Sentry, JSON logs with request IDs.

### W1: Identity, security, tenancy
- **Keycloak realm:** email/password with verification and reset, MFA, ORCID login (OIDC), institutional SSO (SAML/OIDC brokering), brute-force protection.
- **API as BFF:** OIDC code flow, server-side sessions, httpOnly/Secure/SameSite cookie, CSRF token.
- **Organizations and project roles:** owner, lead reviewer, methodologist, statistician, clinical expert, screener, extractor, auditor (read-only plus audit exports), viewer. There's a permission matrix per action, and `require_project_role` / `require_gate` run on every route. Authz test matrix.
- **Encryption:** TLS everywhere, including over WireGuard; LUKS disk encryption on both nodes; field-level AES-GCM for BYOK keys and flagged sensitive text.
- **Hardening:** Redis rate limits, CORS allowlist, CSP/HSTS, request and list size caps, config that fails fast when secrets are missing.
- **Personal access tokens** (scoped) for the public API.

### W2: Core platform and frontend shell
- The full schema above with Alembic migrations, plus the workflow engine (stages, gates, amendments).
- **Audit service:** hash-chained, append-only, with a nightly chain-verification job.
- **Versioning:** a snapshot service for protocols, datasets, analyses, manuscripts and releases.
- **Frontend rebuild:** break up `App.tsx` into routes/features; a stage navigator that shows gate status; project dashboard; audit viewer. Keep the tokens in [index.css](src/index.css), `ProgressBar`, and PapaParse import.

### W3: AI platform (8 providers, provenance, grounding)
- **Adapters:**
  - Anthropic SDK, using tool-use for structured output
  - google-genai async client with `response_schema`
  - OpenAI-compatible adapter with `base_url` for OpenAI, Qwen/DashScope, Kimi/Moonshot, DeepSeek, GLM/Zhipu and Mistral, with capability flags per provider
- **Reliability:** Pydantic validation, one repair retry, backoff on 429/5xx, timeouts, per-provider semaphores, `ai_runs` logging.
- **`model_catalog`:** models are admin-managed and only become selectable after passing the W16 benchmark suite. **Projects pin model versions**, and migrating a project to a new model is an explicit owner action.
- **Prompt registry:** versioned templates. Untrusted content is delimited, with an instruction to ignore instructions embedded in documents.
- **Grounding validator:** checks that quotes match `doc_spans` (fuzzy, above a threshold), that numbers match extracted or analysis values, and that citations resolve.
- **BYOK:** encrypted, never returned to the client, with a test-key endpoint. Key resolution is BYOK first, then the platform key if the tier allows it.
- **Embeddings:** local sentence-transformers (general and biomedical models) stored in pgvector.
- **Data residency:** a notice per provider, plus a record of which provider processed which project's data.

### W4: Topic formulation and protocol
- **Ideation and gap detection:** OpenAlex publication and citation trends by concept; retrieve existing systematic reviews (PubMed SR filter, Epistemonikos by agreement, Cochrane Library links); find reviews that are outdated or conflicting by publication date and later trials; a registry duplication check via the OSF Registries API plus a guided PROSPERO search. The LLM suggests questions grounded in the retrieved records.
- **Question structuring:** PICO/PECO/SPIDER/FINER editors. The LLM turns a vague topic into structured elements, and a consistency checker flags criteria that contradict each other or the question.
- **Feasibility:** scoping-search counts per connector; estimated studies by design; workload estimate (records × minutes × reviewers, with and without active learning); a meta-analysis feasibility flag (too few comparable studies suggests SWiM); a timeline and resource estimate.
- **Protocol editor:** PRISMA-P sections (background, objectives, eligibility, search, screening, extraction, RoB, synthesis, subgroups, sensitivity), with AI drafting grounded in the structured inputs. Includes the machine-readable criteria builder, a pre-specified analysis plan, and COI/funding/ethics declarations with statement generation.
- **Lock and register:** lock with SHA-256 and a timestamp. Deposit through the OSF Registries API or to Zenodo/Figshare, which returns a DOI with its own timestamp. PROSPERO has no submission API, so provide an export mapped to its form fields with a guided copy workflow. Also exports to DOCX/PDF, journal supplements and institutional repositories. Amendments after locking are versioned and require a justification.

### W5: Search
- **Connector framework** with three tiers: (a) open APIs, (b) institution- or user-supplied keys, (c) file import. Every run records database, interface, date, filters, exact string and count (PRISMA-S).
  - **Open:** PubMed/MEDLINE (with efetch for abstracts), Europe PMC, OpenAlex, Crossref, Semantic Scholar, CORE, ClinicalTrials.gov v2, medRxiv/bioRxiv, OSF Preprints, ERIC, Unpaywall.
  - **Key-based:** Scopus, Web of Science, IEEE Xplore, Embase, Epistemonikos.
  - **Import only:** Cochrane CENTRAL, CINAHL, PsycINFO (EBSCO/Ovid exports), ACM DL, WHO ICTRP.
- **Query AST:** parse strategies into a database-neutral tree, then render per database (PubMed, Ovid MEDLINE/Embase, Embase.com, Scopus, WoS, EBSCO, Cochrane, CINAHL, IEEE, OpenAlex), with a syntax validator for each. Translations are checked against Polyglot-style reference outputs.
- **Controlled vocabulary:** MeSH through the NLM APIs (headings, entry terms, tree explosion). Emtree, CINAHL Headings and the APA Thesaurus are only available with licensed access; otherwise the UI suggests the user look terms up. Synonyms are expanded from MeSH entry terms, UMLS (where licensed) and the LLM.
- **Validation and optimization:** seed-article recall check, precision estimated by sampling, noisy-term analysis (hit contribution per term), missing-synonym suggestions, and a PRESS 2015 checklist workflow with a peer-reviewer role.
- **Grey literature:** theses and dissertations, conference abstracts, preprints, government and regulatory documents, clinical study reports (manual upload with URL and date capture), trial registries, policy documents. Each source has its own parser.
- **Citation chasing:** backward and forward via OpenAlex, Semantic Scholar, OpenCitations and Crossref. Co-citation analysis finds seminal papers. Missing-study detection flags references cited by included studies that aren't in the record set.
- **Import:** RIS, BibTeX, EndNote XML, MEDLINE/nbib, WoS CIW, CSV, JSON.
- **Dedup:** identifiers first, then normalized title + author + year fuzzy matching (rapidfuzz), then a human review queue. Merging keeps all source provenance.
- **Reruns:** saved and versioned strategies can be rerun (used by W14).

### W6: Documents, studies and knowledge linking
- **Full-text retrieval cascade:** PMC/Europe PMC JATS XML, then Unpaywall/CORE open-access PDFs, then user upload. EZProxy links open in the user's browser only. Supported formats: PDF, XML, HTML, DOCX, TXT and supplementary files.
- **Parsing:** OCRmyPDF for scanned files, then Docling for sections, two-column layout, tables (cell grid) and figures with captions and bounding boxes, then GROBID for references and header metadata. The result is a canonical document model with stable `doc_spans` (page, bbox, section, table/figure/cell).
- **Viewer:** pdf.js with span highlighting, jump-to-evidence, and comment anchors.
- **Semantic parsing:** study characteristics, outcome definitions, and statistical results sentences and tables, mapped to the protocol's outcomes (human-confirmed).
- **Entity linking:** scispaCy NER normalized to MeSH, RxNorm (RxNav), LOINC, ICD-11 (WHO API) and ATC. SNOMED CT and UMLS are enabled per organization with a licence (feature flag). OMOP concept mapping is optional and licence-dependent.
- **Study vs report linking:** extract registry IDs (NCT, ISRCTN, EudraCT…), score author, sample size and design overlap, then a human confirms. Multiple reports collapse into one study.
- **Copyright:** documents stay private to their project and are never reused across tenants. Open-access status and licence are stored per document.

### W7: Screening
- **Title/abstract:** AI gives a judgment per criterion (from the machine-readable criteria), an overall suggestion, confidence, a rationale, and highlighted spans. **A human makes the final decision**; blinding follows principle 8.
- **Active learning:** models are retrained every N decisions and the queue is ordered by relevance. Each model version is stored with its metrics.
- **Stopping rules:** a statistical stopping test (hypergeometric, Callaghan & Müller-Hansen 2020) with an estimated recall and confidence level, plus heuristic options. Accepting a stopping rule is a signed gate decision.
- **Calibration and QA:** a random sample of AI-deprioritized records is screened by humans, giving a recall estimate and CI. Alerts fire if sensitivity drops below the project threshold.
- **Dual screening:** two independent reviewers, disagreement detection, consensus, third-reviewer adjudication, and Cohen's κ / PABAK per stage and per reviewer pair.
- **Full text:** criterion-level assessment on the parsed full text. Standard exclusion reasons: wrong population/intervention/comparator/outcome/design, duplicate, not primary research, insufficient data, plus custom reasons. Human override with annotations.
- **PRISMA 2020 flow:** computed live from the database. Covers databases, registers and other methods (citation chasing, grey literature), with exclusion reasons at each gate. Exports as SVG/PNG/PDF and in PRISMA2020-tool-compatible CSV.

### W8: Data extraction
- **Form builder:** templates for intervention, DTA, prognostic, prediction model, qualitative, and NMA (arm-level) reviews. Fields for everything in section 6.1 of the list, plus custom fields and custom ontology bindings.
- **AI extraction:** each field gets a value, unit, `span_ids` (page/paragraph/table/figure), confidence and an ambiguity flag. Values that fail the grounding check are rejected. Suggestions go to the reviewer and are never written as final.
- **Statistical extraction:** arm-level means/SD, medians/IQR/range converted to mean/SD (Wan, Luo, Shi methods via R `estmeansd`), SE/CI to SD, binary events/totals, OR/RR with CI, HR with CI to log HR/SE, cluster trials (ICC design effect), crossover, repeated measures, and multi-arm structures. Sources can be text, tables or supplements.
- **Normalization:** unit conversion (pint), scale harmonization, direction alignment, and timepoint windows mapped to the protocol's timepoints.
- **Missing data:** flag missing statistics; suggest imputation only when methodologically valid, always labelled and requiring approval; an author-contact workflow with templates, correspondence log, status and reminders.
- **Dual extraction:** two humans, or a human and the AI as a second extractor. Discrepancy detection with numeric tolerance, a reconciliation queue and sign-off.
- **Audit and lock:** cell-level change history with reasons, then a **locked dataset snapshot**. Exports to CSV, XLSX, JSON, R (`.rds` + loader script) and Python (parquet + loader).

### W9: Risk of bias and quality appraisal
- **Tool selection engine:** maps study design to the recommended tool (RoB 2, ROBINS-I, ROBINS-E, NOS, JBI checklists, QUADAS-2, QUIPS, PROBAST/PROBAST+AI, CASP, MMAT, AMSTAR 2). The recommendation is shown with its reasoning and the user confirms it.
- **Guided workflows:** full signalling-question banks, versioned, with each tool's official algorithms mapping answers to domain and overall judgments. AI suggests an answer per question with evidence spans and an explanation. **A human signs off each domain with a rationale** before it locks.
- **Visualization:** traffic-light and summary plots (R robvis), plus interactive versions in the UI.
- **Reporting quality:** CONSORT, STROBE, STARD and TRIPOD checklists for included studies, with AI-flagged reporting concerns.
- **Licensing:** before embedding question texts in a paid product, check each tool's licence and get permission where needed. For example, CASP is CC BY-NC-SA, and RoB 2/ROBINS terms need confirming.

### W10: Statistical engine
- **R service contract:** the API sends `{locked dataset snapshot, analysis spec}`. R renders a script from a vetted template, runs it with a seed, and returns results JSON, SVG/PDF/TIFF plots (300–600 dpi), the script, `sessionInfo()` and the image digest. Everything is stored in `analysis_runs`. Scripts can be exported to rerun outside the platform.
- **Effect sizes (`escalc`):** OR, RR, RD, MD, SMD (Hedges' g), HR, IRR, correlations (Fisher z), conversions between measures where valid, and variances/SEs.
- **Models:** fixed-effect inverse variance, Mantel-Haenszel, Peto; random effects with DL, REML and Paule-Mandel; Hartung-Knapp-Sidik-Jonkman; prediction intervals, Q, I², and τ² with CI. The chosen model and its justification are recorded and require **statistician sign-off**.
- **Outcome harmonization:** common outcome sets; primary, secondary and adverse outcomes; timepoints; rules for picking one outcome per study or handling several.
- **Forest plots:** interactive in the UI (weights, subgroups) and publication-ready from R `meta`.
- **Subgroups:** pre-specified vs post-hoc flag and interaction tests. **Sensitivity:** leave-one-out, influence diagnostics, excluding high-RoB studies, alternative models, missing-data assumptions, outlier detection.
- **Meta-regression:** continuous and categorical moderators, multivariable, Knapp-Hartung. **Dependent effects:** multilevel `rma.mv` plus robust variance estimation (clubSandwich) with small-sample corrections.
- **Publication bias:** funnel and contour-enhanced plots, Egger, Begg, Peters, trim-and-fill, selection models (`selmodel`), PET-PEESE, with interpretation guidance on small-study effects.
- **Network meta-analysis:** netmeta (frequentist; P-scores, league tables, node-splitting, design-by-treatment inconsistency) and multinma (Bayesian with Stan; SUCRA, convergence diagnostics), plus network plots. Stan jobs get quotas.
- **DTA:** bivariate random effects (`mada::reitsma`), HSROC, SROC curves, threshold-effect assessment, and `diagmeta` for multiple thresholds.
- **IPD:** data dictionary harmonization, per-study validation checks, two-stage and one-stage (lme4/glmmTMB) models, and restricted, encrypted storage with access logging.
- **Complex data:** cluster, crossover, and multi-arm trials (split or combine arms); zero and rare events (MH without correction, GLMM, beta-binomial); missing variances; correlated outcomes.
- **Bayesian pairwise:** bayesmeta.
- **SWiM path:** a pooling-appropriateness checker recommends SWiM when pooling is invalid. SWiM covers vote counting by direction of effect, effect-direction plots and structured tables.
- **Code export:** R always. Python equivalents for core pairwise models. Stata (`meta`/`metan`) templates for core pairwise models only.

### W11: Certainty and interpretation
- **GRADE per outcome:** inputs are filled in automatically: RoB distribution (W9), inconsistency (I², PI, subgroup results), imprecision (CI vs decision thresholds and optimal information size), indirectness (PICO mismatch flags from extraction), and publication bias (W10). Upgrade domains for observational evidence. Every rating needs a justification that links to its analyses, plus methodologist sign-off.
- **Outputs:** Summary of Findings tables and evidence profiles (DOCX/CSV/PDF, GRADEpro-compatible where the format allows). **GRADE Evidence-to-Decision frameworks** and guideline-development workspaces.
- **Clinical interpretation:** absolute effects from user-supplied baseline risks, NNT/NNH, benefit-harm summaries, and plain-language summaries. All generated text is grounded and needs clinical-expert approval.
- **Prior evidence:** compare against existing reviews (W4 retrieval), including an overlap matrix with corrected covered area, and flag changed conclusions.
- **Limitations:** generated from structured flags (heterogeneity, imprecision, indirectness, RoB, sparse data, outcome reporting bias). No free-form invention.

### W12: Manuscript and reporting
- **Gate:** drafting requires a locked evidence-base snapshot.
- **Editor and drafting:** a TipTap editor with PRISMA 2020 sections (title, abstract, introduction, methods, results, discussion, conclusion). Each section is drafted from the snapshot, and **every sentence stores claim links** to records, extraction values, analysis results or citations.
- **Verifier pass:** numbers must match analysis results exactly, citations must resolve to real records, and statements need supporting evidence. Unsupported claims are flagged and must be fixed or explicitly acknowledged. Confidence labels are shown.
- **Methods section** is generated from the pipeline's own logs, so it records what was actually done.
- **AI-use disclosure** is generated from `ai_runs` and gate records, following PRISMA-trAIce and RAISE.
- **Auto-inserted, live-linked tables and figures:** study characteristics, RoB, SoF, GRADE, forest, funnel, network, PRISMA flow, and supplementary appendices.
- **Checklists** auto-populated with page and section locations: PRISMA 2020 (+ abstracts), PRISMA-P, PRISMA-S, PRISMA-trAIce, PRISMA-NMA, PRISMA-DTA, MOOSE, SWiM, and an AMSTAR 2 self-check.
- **Citations:** CSL styles (Vancouver, APA, AMA, Harvard, and journal-specific styles from the CSL repository); DOI and metadata verification through Crossref; retraction detection through Crossref's Retraction Watch data and PubMed publication types, re-checked before export; Zotero Web API sync; BibTeX/RIS export.
- **Language tools:** academic tone, grammar, journal style adaptation, plain-language summary, graphical abstract text. Edits are tracked.
- **Export:** **all authors must approve** before DOCX/LaTeX/PDF export through Pandoc.

### W13: Publication and submission
- **Journal finder:** matches the manuscript and included-study venues against OpenAlex sources and topics. Pulls open-access status, APCs and indexing from DOAJ and OpenAlex, and citation metrics from OpenAlex (licensed metrics like JCR and Cabells only if the user supplies access). Review times are shown only when published. **Predatory-journal warnings** are heuristic and labelled as such: not in DOAJ, not in MEDLINE, not a COPE member, and Think.Check.Submit items.
- **Author-guideline parser:** takes a URL or PDF and produces structured requirements (word limits, abstract format, reference style, figure specs, required statements).
- **Submission package:** manuscript (DOCX/LaTeX/PDF), title page, abstract, keywords, highlights, cover letter, graphical abstract draft, tables, figures, supplements, and statements (data availability, CRediT author contributions, COI, funding, ethics, registration).
- **Readiness checks:** word counts, reference style, figure resolution and format, table formatting, checklist completeness, and required statements present.
- **Deposit:** OSF, Zenodo (DOI + versioning), Figshare, Dryad and GitHub/GitLab (analysis code). Packages follow FAIR (README, data dictionary, licence, DataCite metadata). Preprints go to OSF Preprints by API, or to medRxiv through a guided package.
- **Journal submission:** there is no universal submission API, so the platform produces the package and a guided checklist. **It never auto-submits.**
- **Peer review:** import reviewer comments, track point-by-point responses linked to manuscript changes, show version diffs, trigger re-analysis for revisions, and draft the response letter.

### W14: Living reviews
- **Schedules** per saved strategy (arq cron). New records are deduplicated against the corpus, ranked by the project's active-learning model, and given AI suggestions that wait for human decisions.
- **Alert triggers:** new eligible studies, large or high-impact trials, retraction of an included study, changes on watched guideline or regulatory feeds, and user-defined thresholds.
- **Impact assessment:** a provisional re-analysis with candidate studies shows the shift in pooled estimate, CI and GRADE before anyone commits.
- **Versioned releases:** snapshots of protocol, dataset, analyses and manuscript, with a changelog and a new Zenodo DOI version.
- **Evidence and gap maps:** intervention × outcome bubble maps, a coverage heatmap, and a trend dashboard.

### W15: Collaboration and project management
- **Work management:** tasks with assignees and deadlines, a stage workload dashboard, and progress per reviewer.
- **Discussion:** comments and annotations anchored to records, PDF spans, extraction cells or manuscript sentences; @mentions; in-app and email notifications.
- **Metrics and reports:** reviewer metrics (agreement, throughput, override rates) and team audit reports.
- **Membership:** invites, per-member COI declarations, and ownership transfer.
- **FAQ assistant:** answers grounded in the product documentation, with citations.

### W16: AI governance, validation and QA (no GxP)
- **Benchmark harness in CI:**
  - Screening: SYNERGY and CLEF TAR datasets; recall, WSS@95 and specificity
  - Extraction: EBM-NLP PICO spans plus a curated gold set from open-access reviews; exact or tolerance match
  - RoB: a curated gold set; agreement with expert judgments
  - Statistics: metadat reference datasets and package test values
- **Model change management:** any model or prompt change runs the benchmark as a regression suite. A drop below threshold blocks it from the catalog, and results are published as model performance reports.
- **Per-project pre-trust calibration:** AI runs on a human-screened seed subset, producing a project performance report before AI suggestions are switched on. Ongoing QA sampling continues during the project.
- **Bias monitoring:** language, year, geography and publication-type distributions compared between retrieved and included records; AI error rates by subgroup; calibration curves.
- **Reports:** SOP templates, methodological validation, reproducibility (reruns an archived analysis in its pinned image and diffs the output), data provenance, and team audit.

### W17: Interoperability and public API
- **Import/export:** RIS, BibTeX, EndNote XML, CSV, JSON, JATS XML, PRISMA formats, RevMan-compatible data (documented formats only), Covidence/Rayyan decision files, Zotero API, GRADEpro-compatible SoF, R and Python data packages.
- **Public REST API** (OpenAPI docs) with scoped tokens and webhooks for stage and gate events.
- **FHIR module:** EBM-on-FHIR resources (Evidence, EvidenceVariable, Citation), exported for clinical-system integration.

### W18: Billing and entitlements
- **Payment interface:** `BillingProvider` (checkout, portal, webhook parsing, cancel) with idempotent webhooks. One adapter, chosen before launch.
- **Entitlements in the database:** projects, seats, records per month, AI credits, allowed providers and models, OCR pages, Stan/NMA compute minutes, living-review schedules, storage, SSO (institutional plans). All enforced server-side. BYOK calls don't count against AI credits.
- **Admin:** pricing page, usage meter, and an admin panel (users, orgs, subscriptions, model catalog, job failures, cost dashboard).

### W19: Production, compliance, launch
- **Infrastructure:** two-node VPS with WireGuard; hardening (non-root, SSH keys, ufw, fail2ban, unattended upgrades, log rotation); tagged images on GHCR; SSH deploy with `alembic upgrade`, health gates and rollback.
- **Backups and monitoring:** nightly `pg_dump` + restic off-site; versioned object storage; quarterly restore drills. Sentry, `/healthz` and `/readyz` (DB, Redis, workers, R, GROBID, Docling, Keycloak), resource alerts, daily LLM spend report.
- **Legal:** Terms, Privacy Policy, DPA template for institutions, sub-processor list (LLM vendors with residency), GDPR export and deletion, cookie notice, copyright policy, and a check of each connector's terms of use and appraisal-tool licence.
- **Quality gates before launch:** WCAG 2.2 AA audit, external penetration test, and a k6 load test (concurrent AI batches, OCR queue, Stan jobs).
- **Private design-partner validation** (real review teams, not a public launch) runs several full reviews end to end before the public launch.

---

## 5. External access and licensing constraints

Typical access as of planning; confirm each provider's current terms during W5 and W6.

| Resource | Constraint | Approach |
|---|---|---|
| Embase, Scopus, Web of Science | Licensed APIs | Institution/user key connector; otherwise export import |
| CINAHL, PsycINFO, Cochrane CENTRAL, ACM DL | No general open API | RIS/CIW import with PRISMA-S metadata capture |
| PROSPERO | No public submission API | Field-mapped export + guided submission; OSF Registries by API |
| WHO ICTRP | No stable open API | Import of exports |
| Emtree, CINAHL Headings, APA Thesaurus | Licensed vocabularies | Enabled only with licence; otherwise manual lookup prompts |
| UMLS, SNOMED CT | Licence (SNOMED varies by country) | Org-level feature flag; MeSH/RxNorm/LOINC/ICD-11 by default |
| Epistemonikos | API by agreement | Connector enabled once the agreement exists |
| JCR impact factor, Cabells | Paid | Only with user-supplied access; OpenAlex/DOAJ by default |
| Journal submission systems | No universal API | Package + guided checklist |
| Appraisal tool texts (CASP, RoB 2, JBI…) | Individual licences | Legal review / permission before embedding |
| Covidence | Restricted API | File-based import/export |

---

## 6. Build sequence (single program; public launch only at the end)

Milestones are internal checkpoints, not releases. Each one ends with a private design-partner test.

| Milestone | Workstreams | Exit criterion |
|---|---|---|
| **M1: Platform core** | W0, W1, W2, W3, W15 (basics), W16 harness skeleton | Secure multi-tenant projects with gates, audit chain, 8 LLM adapters passing contract tests |
| **M2: Question to screening** | W4, W5, W7, W17 (imports) | Topic → registered protocol → multi-source search → dedup → dual/AL screening → live PRISMA on a real review |
| **M3: Documents to appraisal** | W6, W8, W9 | Full texts parsed with spans; dual extraction to a locked dataset; signed-off RoB on a real review |
| **M4: Synthesis and certainty** | W10, W11 | R service reproduces reference results for pairwise, NMA, DTA, IPD, RVE and publication bias; GRADE SoF produced |
| **M5: Reporting to living** | W12, W13, W14, remaining W17 | Grounded manuscript with zero unverified claims; submission package; deposit with DOI; a simulated living update |
| **M6: Governance and launch** | W16 (full), W18, W19 | Benchmarks at threshold, billing live in sandbox, pentest/accessibility/load/restore passed → **public launch** |

**Resourcing.** This is a multi-quarter program. It realistically needs backend, frontend, NLP/ML, an R biostatistician, a systematic-review methodologist (for the gate and tool design), and DevOps. A team of about 6 should expect roughly 15–24 months to reach M6. A smaller team extends that proportionally.

---

## 7. Existing code: what happens to each file
- **Reuse, then fix and move:**
  - [pubmed.py](backend/services/pubmed.py): add efetch, async, errors
  - [openalex.py](backend/services/openalex.py): rebuild abstracts, paging
  - [unpaywall.py](backend/services/unpaywall.py): timeout, encoding
  - [ezproxy.py](backend/services/ezproxy.py): links opened in the browser only
  - RoB domain lists in [ai_screening.py:131](backend/services/ai_screening.py#L131): seed data for W9 tool banks (fix NOS; move PRESS to W5)
  - Prompts in `ai_screening.py` / `ai_protocol.py`: seed prompt templates
  - Frontend tokens in [index.css](src/index.css), `ProgressBar`, PapaParse import, CSV template
- **Replace:** [main.py](backend/main.py), [auth_routes.py](backend/auth_routes.py) (replaced by Keycloak BFF), [database.py](backend/database.py), [models.py](backend/models.py), [requirements.txt](backend/requirements.txt), [App.tsx](src/App.tsx) (split into routes/features), [README.md](README.md), [mkdocs.yml](mkdocs.yml) (fix the missing pages)
- **Delete:** [ai_extraction.py](backend/services/ai_extraction.py) (duplicate), `backend/omnireview.db` (after exporting its 2 users, if they're wanted)

## 8. Decisions still open
- Payment provider (before W18), plus tier limits and prices.
- Object storage and email vendors (defaults: Backblaze B2, Postmark).
- Default pinned model per provider (confirm current IDs from vendor docs in W3; they must pass W16).
- Which licensed connectors and vocabularies to pursue commercially (Embase, Scopus, WoS, UMLS/SNOMED).
- Benchmark acceptance thresholds (for example, screening recall target), set with the methodologist.

## 9. Verification
- **CI on every change:** ruff, mypy, pytest (Postgres/Redis services), R testthat for the stats service, tsc, oxlint, vitest, Playwright, pip-audit/npm audit, W16 benchmark regression on model/prompt changes.
- **Gate enforcement tests:** finalizing a decision, extraction value, RoB judgment, model choice, GRADE rating or export without the required human role and rationale must fail at both API and DB level.
- **Grounding tests:** 100% of stored AI extraction values and manuscript claims resolve to existing spans or results. Deliberately fabricated quotes or numbers are rejected.
- **Statistical validation:** R service outputs match metadat and package reference values within tolerance for every model family in W10. Archived analyses rerun in their pinned image produce identical result JSON.
- **Pipeline golden project:** a seeded corpus with known duplicates, decisions, PDFs and data must give exact PRISMA counts, dedup groups, locked dataset, pooled estimates, GRADE ratings and a checklist-complete manuscript.
- **E2E (Playwright):** SSO login → topic → feasibility → protocol lock and OSF deposit (sandbox) → search + imports → PRESS → dedup → blinded dual screening with AL and stopping rule → full-text retrieval and parse → dual extraction and lock → RoB sign-off → pairwise + NMA → GRADE SoF → grounded manuscript → author approvals → submission package + Zenodo sandbox DOI → living rerun alert → versioned release.
- **Ops:** restore drill into a fresh database, k6 load test, external pentest, WCAG 2.2 AA audit, design-partner reviews completed with methodologist sign-off on the outputs.
