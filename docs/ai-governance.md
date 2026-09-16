# AI governance and validation

## Provenance

Every AI call records the task, provider, model, prompt version, input and output tokens, cost, latency, who started it,
and whether it succeeded. AI outputs are stored as suggestions; only people's decisions count.

## Benchmarks

Platform administrators run models against benchmark data sets:

- **Screening**: records with known include or exclude labels (for example SYNERGY data sets, fetched with
  `backend/scripts/fetch_synergy_dataset.py`). Metrics: recall, specificity, precision, and work saved over sampling at
  95% recall (WSS@95).
- **Extraction**: texts with known field values. Metric: accuracy, with a relative tolerance for numbers.
- **Risk of bias**: texts with expert domain judgments. Metrics: agreement and Cohen's kappa.
- **Statistics**: the R engine against published reference values (metafor's BCG vaccine data set).

A run passes when every metric meets its threshold. A model is **validated** once its latest runs pass for screening,
extraction, and risk of bias with the current prompt versions; changing a prompt needs new runs. When the server sets
`REQUIRE_VALIDATED_MODELS=true`, projects can only pin validated (or exempted) models. Model performance reports list
every run.

## Calibration before relying on AI

A project can run a calibration: the AI screens a random sample of records reviewers have already decided, and the report
shows sensitivity, specificity, precision, agreement (kappa), and a calibration table of the AI's confidence against how
often records were included. A reviewer accepts the report. With **require AI calibration** turned on in review
settings, AI screening waits for an accepted report for the project's current model.

## Bias monitoring

The bias report compares retrieved and included records by publication year, source, and journal, and reports the AI's
error rate by subgroup and its calibration.

## Reports

- **Standard operating procedure**: the project's roles, review settings, gates, AI models, and validation, as Word.
- **Methodological validation**: benchmark results for the project's model and the project's calibration reports.
- **Reproducibility**: reruns a final analysis from its archived script and data and compares every result.
- **Data provenance**: from searches to records, studies, extraction values and their sources, analyses, GRADE, and
  the manuscript version.
- **Team audit**: see [Collaboration](collaboration.md).
