# Import and export

## Records

Import search results as RIS, BibTeX, EndNote XML, MEDLINE (nbib), Web of Science (CIW), CSV, JSON (a list of records
with title, authors, year, journal, DOI, and abstract), or JATS XML article metadata.

Export records as RIS, BibTeX, EndNote XML, CSV, or JSON: all records, unique records, or included records.

## Screening decisions from other tools

- **Rayyan**: import a Rayyan CSV export. Decisions are read from the notes column (`RAYYAN-INCLUSION`) for the Rayyan
  reviewer you choose, and recorded as your decisions with a note saying where they came from.
- **Covidence**: import a Covidence CSV or RIS export of one stage's included or excluded studies, choosing the decision
  and stage the file represents.

Records are matched by DOI, PMID, or title and year. Unmatched rows are listed.

## Extraction and analysis data

- The extraction data set exports as CSV, JSON, Excel, or a bundle with R and Python loader scripts.
- A RevMan-style data table (CSV) lays out each dichotomous or continuous outcome by study, in the columns RevMan's data
  entry uses, for pasting into RevMan.
- Each analysis run exports as a zip with its R script, data, results, and plots.

## Certainty and reporting

- The summary of findings exports as CSV, Word, or a GRADEpro-style evidence profile CSV.
- The PRISMA flow exports as SVG or CSV (compatible with the PRISMA2020 Shiny app).
- Reporting checklists export as Word or CSV.

## Manuscript and references

The manuscript exports as Word, PDF, LaTeX, Markdown, or JATS XML. References export as BibTeX, RIS, or CSL JSON, and
sync with Zotero.

## Interoperability

The public API, webhooks, and the FHIR Evidence bundle are described in [Public API](api.md).
