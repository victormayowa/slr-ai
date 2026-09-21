# Protocol and search

## Topic and feasibility

**Topic & Feasibility** searches PubMed, OpenAlex, ClinicalTrials.gov, and OSF Registries for the topic: publication
trends, existing reviews (flagging ones that may be outdated), registered reviews, and an estimate of screening
workload. PROSPERO has no public search API, so search it yourself before registering.

## Review question and protocol

Structure the question with PICO, PECO, SPIDER, PCC, or another framework, and rate it against FINER. The AI can
suggest a question, criteria, search strings, an analysis plan, and drafts of PRISMA-P sections; nothing is saved until
you accept it.
The analysis plan records pre-specified outcomes (primary, secondary, adverse), subgroup and sensitivity analyses, and
the synthesis approach. "Suggest with <model>" proposes outcomes with their timepoints and effect measures, subgroup
and sensitivity analyses with a reason for each, and a synthesis approach, all from the question and criteria you have
written. It is added to the form only when you press "Add to the plan", and saved only when you save, so what is
pre-specified stays the reviewers' decision.

## Registration

Export the locked protocol as Word or Markdown, copy the PROSPERO fields, or deposit it in a private OSF project. Record
the registration number, or waive registration with a reason.

## Searches

Start by agreeing which sources to search. "Suggest databases with \<model\>" asks the AI which sources suit your
question and says what each adds, marking those OmniReview can search itself and those you run on their own platform.
Tick the ones you agree with, add any of your own, and OmniReview drafts a search string for each in that database's
syntax, which you can then edit.

Search connectors run strategies on PubMed, Europe PMC, OpenAlex, Crossref, Semantic Scholar, and ClinicalTrials.gov.
Databases without an open API (such as Embase, CINAHL, or CENTRAL) are searched on their own platforms and imported as
export files; each of those says where to run the string and which export format to bring back.

Every strategy shows what has happened to it: "Not searched yet", or the date it was searched and how many records it
added, and a warning when the string has changed since the last run. A line above the strategies counts how many
databases are done, how many records they have added, and which sources are still to do. Every run records the
database, interface, date, search string, and record count for PRISMA-S.

## Imports

Import RIS, BibTeX, EndNote XML, MEDLINE (nbib), Web of Science, CSV, JSON, and JATS XML files. See
[Import and export](import-export.md).

## Search quality

Strategies are versioned. Recall checks test a strategy against known relevant articles. PRESS 2015 peer review is
recorded per strategy, or waived with a reason. MeSH lookup helps build controlled vocabulary.

## Other sources

Citation searching follows references and citing works of seed articles through OpenAlex. Grey literature sources are
recorded with their URL and search date.

## Deduplication

Duplicates are found by identifiers (DOI, PMID) and by matching titles, authors, and years. Possible duplicates are
confirmed or rejected by a reviewer, and similar records can be compared with embeddings.

How many pairs are offered at a time follows the plan: ten on Free, a hundred on Researcher and Team, and all of them
for Institution. Decide the ones shown and the next lot appears, with the number still waiting beside the heading.

Pairs can be decided one at a time, choosing which record to keep, or all at once: "Drop all as duplicates" merges
every waiting pair, keeping the earlier record of each, which is the record automatic deduplication keeps; "Keep all
as separate studies" marks them all as distinct. Either way each pair is recorded with who decided it, so the PRISMA
counts and the audit trail read the same as deciding them individually.
