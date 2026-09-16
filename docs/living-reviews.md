# Living reviews

## Surveillance

Schedule a saved search strategy to rerun every so many days. New records are deduplicated against the review, ranked by
the project's screening model, and checked for large sample sizes. The AI can add screening suggestions. Scheduled runs
happen in the background worker, hourly.

## Candidates and living updates

New records wait on the **Living Review** screen until a reviewer promotes or dismisses them. Starting a living update
imports the promoted records and reopens the search stage and every later stage, so the new records pass through the
same screening, extraction, appraisal, synthesis, certainty, and manuscript gates.

## Alerts

Alerts report new records, records that look relevant, large studies, retractions of included studies (checked weekly
through Crossref and PubMed), and new items in watched RSS or Atom feeds such as guideline or regulatory updates.
Thresholds are set per schedule.

## Impact assessment

Before committing to an update, rerun a final analysis provisionally with candidate study data. The result shows the
change in the pooled estimate and confidence interval, and how suggested GRADE ratings might change. Nothing in the
review changes.

## Releases

Once every stage is signed off, create a release. It records the hash of every stage snapshot, the approved manuscript
version, and the final analyses, with a changelog against the previous release. Deposit a release on Zenodo; later
releases become new versions of the same record.

## Evidence and gap maps

The evidence map shows interventions by outcomes (bubble size for the number of studies, colour for GRADE certainty,
and gaps where no study reports an outcome), data coverage by study and field, and trends over time.
