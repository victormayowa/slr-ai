# Extraction and risk of bias

## Studies and reports

Each included report starts as its own study. Reports of the same study (for example a protocol and a results paper)
are linked; OmniReview suggests links by trial registration numbers and matching details. Studies can have arms.

## Extraction form

Build the form from templates (intervention, diagnostic accuracy, prognostic, prediction model, qualitative, network)
or field by field. Field types include text, numbers, choices, dates, dichotomous outcomes (events and totals),
continuous outcomes (mean, SD, n), effect estimates with confidence intervals, and 2×2 tables. Fields can be extracted
per arm.

## Extracting data

**Review settings** choose single extraction, dual extraction with reconciliation, or one reviewer checked against the
AI as a second extractor. AI suggestions quote the passage they come from and are marked grounded only when the quote is
found. Calculators convert medians and ranges, standard errors, and confidence intervals, and imputed values need a
second reviewer's approval. Units are normalized. Author contacts record requests for missing data.

Signing off extraction locks the data set. Export it as CSV, JSON, Excel, or a bundle with R and Python loaders.

## Risk of bias

OmniReview recommends a tool from each study's design: RoB 2, ROBINS-I, ROBINS-E, QUADAS-2, PROBAST, the Newcastle-Ottawa
Scale, JBI checklists, CASP, MMAT, QUIPS, or AMSTAR 2. Choosing a different tool needs a reason.

Answer the tool's signalling questions; its published algorithm suggests each domain's judgment. A reviewer signs off
every domain and the overall judgment with a written rationale, and changing an answer withdraws that domain's
sign-off. The AI can suggest answers with quotes, but never signs anything off. The tools' own texts are licensed by
their authors, so OmniReview shows short labels with links to the official guidance.

Traffic-light and summary plots are drawn with robvis. Reporting checklists (CONSORT, STROBE, STARD, TRIPOD) record how
completely each study is reported.
