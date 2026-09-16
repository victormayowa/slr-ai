# Statistical synthesis

## The R engine

Every analysis runs in R with validated packages: metafor, meta, netmeta, mada, clubSandwich, lme4, bayesmeta, and
robvis. The server installs R with `backend/scripts/setup_r_env.sh`.

## Analysis types

- **Pairwise meta-analysis**: risk ratios, odds ratios, risk differences, mean differences, standardized mean
  differences, or generic estimates; fixed effect, random effects (REML, DerSimonian-Laird, Paule-Mandel, and others,
  with the Hartung-Knapp-Sidik-Jonkman adjustment), Mantel-Haenszel, Peto, or GLMM; prediction intervals; subgroups;
  meta-regression; leave-one-out and influence diagnostics; and small-study effects (funnel plots, Egger, Begg, trim and
  fill, PET-PEESE, selection models).
- **Network meta-analysis** with league tables, P-scores, node-splitting, and design-by-treatment inconsistency.
- **Diagnostic test accuracy** with the bivariate model and SROC curves.
- **Bayesian meta-analysis** with a half-normal prior on heterogeneity.
- **Dependent effects** with multilevel models and robust variance estimation.
- **Individual participant data**, one-stage and two-stage. Participant files are encrypted and every access is audited.
- **Synthesis without meta-analysis (SWiM)**: vote counting on the direction of effect and effect direction plots.

## Pre-specified and post hoc analyses

An analysis either implements an item of the protocol's analysis plan or is marked post hoc with a justification. The
data set preview shows which studies contribute and why others don't, and whether pooling looks appropriate.

## Approval and final results

A statistician approves the model choice. Runs of an approved analysis on the locked extraction data set are final;
other runs are exploratory. Revising an approved analysis returns it to draft.

## Reproducibility

Each run stores its script, specification, data, seed, R version, package versions, results, and plots. Export a run as
a zip that reruns with `Rscript --vanilla analysis.R`; pairwise bundles also include Python and Stata versions. A
reproducibility check reruns an archived run and compares the results.
