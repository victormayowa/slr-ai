# Screening

## Title and abstract screening

Each record gets an include, exclude, or undecided decision from a reviewer. **Review settings** choose one or two
reviewers per record. With two reviewers and blinding on, neither sees the other's decision or the AI suggestion until
they record their own. Disagreements are resolved by adjudication with a rationale.

## AI suggestions

The AI judges each accepted criterion and suggests a decision with its reasoning and a supporting quote, which is
checked against the record's text. Suggestions never count as decisions. Every AI run records the provider, model,
prompt version, and who started it.

If a project turns on **require AI calibration**, AI screening waits until an accepted calibration report shows the
model's sensitivity and specificity on records reviewers have already decided. See [AI governance](ai-governance.md).

## Prioritization and stopping

Screening order is ranked by a model trained on the team's decisions so far, retrained as decisions accumulate. A
statistical stopping rule (a hypergeometric test against the recall target) estimates when screening can stop. Stopping
only takes effect when a reviewer accepts it with a rationale. Quality-assurance samples of records the ranking
deprioritized are screened by people to check recall.

## Full-text screening

Full texts are retrieved from Europe PMC, Unpaywall, or uploads, and parsed into passages. Each report sought gets an
include decision, an exclusion with a reason, or a not-retrieved decision. AI suggestions at full text quote the passages
they rely on.

## Agreement and PRISMA

Cohen's kappa is reported for dual screening. The PRISMA 2020 flow diagram is computed from the records and decisions,
and exports as SVG or CSV (compatible with the PRISMA2020 Shiny app).
