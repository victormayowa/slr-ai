# Protocol and search

## Topic and feasibility

**Topic & Feasibility** searches PubMed, OpenAlex, ClinicalTrials.gov, and OSF Registries for the topic: publication
trends, existing reviews (flagging ones that may be outdated), registered reviews, and an estimate of screening
workload. PROSPERO has no public search API, so search it yourself before registering.

## Review question and protocol

Structure the question with PICO, PECO, SPIDER, PCC, or another framework, and rate it against FINER. The AI can
suggest a question, criteria, search strings, and drafts of PRISMA-P sections; nothing is saved until you accept it.
The analysis plan records pre-specified outcomes (primary, secondary, adverse), subgroup and sensitivity analyses, and
the synthesis approach.

## Registration

Export the locked protocol as Word or Markdown, copy the PROSPERO fields, or deposit it in a private OSF project. Record
the registration number, or waive registration with a reason.

## Searches

Search connectors run strategies on PubMed, Europe PMC, OpenAlex, Crossref, Semantic Scholar, and ClinicalTrials.gov.
Databases without an open API (such as Embase, CINAHL, or CENTRAL) are searched on their own platforms and imported as
export files. Every run records the database, interface, date, search string, and record count for PRISMA-S.

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
