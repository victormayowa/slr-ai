"""Exported analysis code: the R script that produced the results, which reruns as is, and Python (PyMARE) and Stata
equivalents of pairwise meta-analyses for teams working in those languages.

The Python and Stata versions reproduce the model choice, but estimators and small-sample adjustments differ between
packages, so their numbers can differ slightly from the R results, which are the ones reported.
"""

import json
from collections.abc import Mapping
from typing import Any

import models
from stats_engine import rows_to_csv

README = """# {title}

Exported from OmniReview on {exported_at}. Run {run_id} ({status}{final}).

- `analysis.R` is the exact script that produced `results.json`. Rerun it in this folder with
  `Rscript --vanilla analysis.R` (R {r_version}; packages: {packages}).
- `spec.json` holds the analysis specification and `data.json` / `data.csv` the analysis data set
  (SHA-256 {dataset_sha256}), from the {source} extraction data set.
- `session_info.txt` records the R session that ran the analysis.
{extras}
Studies left out of the data set, with reasons:
{excluded}
"""

PYTHON_MEASURES = {"RR", "OR", "RD", "MD", "SMD", "HR"}

PYTHON_TEMPLATE = '''"""Pairwise meta-analysis equivalent in Python (numpy, scipy, PyMARE).

pip install numpy scipy pandas pymare
Estimates can differ slightly from the R results (metafor), which are the reported ones.
"""

import json

import numpy as np
import pandas as pd
from scipy import stats
from pymare import Dataset
from pymare.estimators import DerSimonianLaird, VarianceBasedLikelihoodEstimator, WeightedLeastSquares

spec = json.load(open("spec.json"))
data = pd.read_csv("data.csv")
measure, data_type = spec["measure"], spec["data_type"]
add = spec.get("zero_correction", 0.5)

if data_type == "binary":
    a, n1, c, n2 = (data[k].astype(float).to_numpy() for k in ("ai", "n1i", "ci", "n2i"))
    zero = (a == 0) | (c == 0) | (a == n1) | (c == n2)
    a, c = np.where(zero, a + add, a), np.where(zero, c + add, c)
    n1, n2 = np.where(zero, n1 + 2 * add, n1), np.where(zero, n2 + 2 * add, n2)
    b, d = n1 - a, n2 - c
    if measure == "RR":
        yi, vi = np.log((a / n1) / (c / n2)), 1 / a - 1 / n1 + 1 / c - 1 / n2
    elif measure == "OR":
        yi, vi = np.log((a * d) / (b * c)), 1 / a + 1 / b + 1 / c + 1 / d
    else:
        p1, p2 = a / n1, c / n2
        yi, vi = p1 - p2, p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2
elif data_type == "continuous":
    m1, s1, n1, m2, s2, n2 = (data[k].astype(float).to_numpy() for k in ("m1i", "sd1i", "n1i", "m2i", "sd2i", "n2i"))
    if measure == "MD":
        yi, vi = m1 - m2, s1**2 / n1 + s2**2 / n2
    else:
        pooled = np.sqrt(((n1 - 1) * s1**2 + (n2 - 1) * s2**2) / (n1 + n2 - 2))
        correction = 1 - 3 / (4 * (n1 + n2 - 2) - 1)
        yi = correction * (m1 - m2) / pooled
        vi = (n1 + n2) / (n1 * n2) + yi**2 / (2 * (n1 + n2))
else:
    ratio = measure in ("RR", "OR", "HR")
    est, lower, upper = (data[k].astype(float).to_numpy() for k in ("estimate", "ci_lower", "ci_upper"))
    yi = np.log(est) if ratio else est
    width = (np.log(upper) - np.log(lower)) if ratio else (upper - lower)
    vi = (width / (2 * stats.norm.ppf(0.975))) ** 2

model = spec.get("model", "random")
if model == "fixed":
    estimator = WeightedLeastSquares()
elif spec.get("tau_method") == "DL":
    estimator = DerSimonianLaird()
else:
    estimator = VarianceBasedLikelihoodEstimator(method="REML")
estimator.fit_dataset(Dataset(y=yi, v=vi))
summary = estimator.summary()
print(summary.to_df())
print("tau^2:", summary.tau2)
if measure in ("RR", "OR", "HR"):
    print("Pooled", measure, np.exp(summary.fe_params.ravel()))
'''

STATA_BINARY = {"RR": "lnrratio", "OR": "lnoratio", "RD": "rdiff"}
STATA_CONTINUOUS = {"MD": "mdiff", "SMD": "hedgesg"}
STATA_MODELS = {
    "random": "random({tau})",
    "fixed": "fixed",
    "mantel_haenszel": "fixed(mhaenszel)",
    "peto": "fixed(peto)",
}


def stata_script(spec: Mapping[str, Any]) -> str:
    measure, data_type = spec.get("measure", ""), spec.get("data_type", "")
    tau = {
        "REML": "reml",
        "DL": "dlaird",
        "PM": "pmandel",
        "SJ": "sjonkman",
        "ML": "mle",
        "EB": "ebayes",
        "HE": "hedges",
        "HS": "hschmidt",
    }
    model = STATA_MODELS.get(spec.get("model", "random"), "random(reml)").format(
        tau=tau.get(spec.get("tau_method", "REML"), "reml")
    )
    lines = [
        "* Pairwise meta-analysis equivalent in Stata 17 or later (meta suite).",
        "* Estimates can differ slightly from the R results (metafor), which are the reported ones.",
        'import delimited "data.csv", clear',
    ]
    if data_type == "binary" and measure in STATA_BINARY:
        lines += [
            "generate treat_non = n1i - ai",
            "generate control_non = n2i - ci",
            f"meta esize ai treat_non ci control_non, esize({STATA_BINARY[measure]}) {model} studylabel(study)",
        ]
    elif data_type == "continuous" and measure in STATA_CONTINUOUS:
        lines.append(
            f"meta esize n1i m1i sd1i n2i m2i sd2i, esize({STATA_CONTINUOUS[measure]}) {model} studylabel(study)"
        )
    elif data_type == "generic":
        ratio = measure in ("RR", "OR", "HR")
        lines += [
            f"generate yi = {'ln(estimate)' if ratio else 'estimate'}",
            f"generate sei = {'(ln(ci_upper) - ln(ci_lower))' if ratio else '(ci_upper - ci_lower)'} / (2 * "
            "invnormal(0.975))",
            f"meta set yi sei, {model} studylabel(study)",
        ]
    else:
        return ""
    se = " se(khartung)" if spec.get("hksj") and spec.get("model") == "random" else ""
    predict = " predinterval" if spec.get("prediction_interval") and spec.get("model") == "random" else ""
    lines += [f"meta summarize,{se}{predict}{' eform' if measure in ('RR', 'OR', 'HR') else ''}", "meta forestplot"]
    if spec.get("publication_bias", {}).get("funnel"):
        lines.append("meta funnelplot")
    if spec.get("publication_bias", {}).get("egger"):
        lines.append("meta bias, egger")
    if spec.get("publication_bias", {}).get("trim_fill"):
        lines.append("meta trimfill")
    return "\n".join(lines) + "\n"


def bundle_files(run: models.AnalysisRun, title: str) -> dict[str, bytes]:
    rows = run.dataset.get("rows", [])
    spec = run.dataset.get("r_spec", {})
    files: dict[str, bytes] = {
        "analysis.R": run.script.encode(),
        "spec.json": json.dumps(spec, indent=2).encode(),
        "data.json": json.dumps(rows, indent=2, default=str).encode(),
        "data.csv": rows_to_csv(rows).encode(),
        "session_info.txt": run.session_info.encode(),
    }
    if run.results is not None:
        files["results.json"] = json.dumps(run.results, indent=2).encode()
    extras = []
    if run.analysis.analysis_type == "pairwise" and spec.get("measure") in PYTHON_MEASURES:
        files["analysis_pymare.py"] = PYTHON_TEMPLATE.encode()
        extras.append("- `analysis_pymare.py` is a Python equivalent of the main model (PyMARE).")
        stata = stata_script(spec)
        if stata:
            files["analysis.do"] = stata.encode()
            extras.append("- `analysis.do` is a Stata equivalent of the main model (meta suite).")
    if run.analysis.analysis_type == "ipd":
        extras.append("- Participant data aren't included in exports; add `ipd.csv` to rerun.")
    excluded = run.dataset.get("excluded", [])
    files["README.md"] = README.format(
        title=title,
        exported_at=models.utcnow().date().isoformat(),
        run_id=run.id,
        status=run.status,
        final=", final" if run.is_final else ", exploratory",
        r_version=run.r_version or "unknown",
        packages=", ".join(f"{name} {version}" for name, version in sorted(run.packages.items())) or "unknown",
        dataset_sha256=run.dataset_sha256,
        source=run.dataset.get("source", "current"),
        extras="\n".join(extras) + ("\n" if extras else ""),
        excluded="\n".join(f"- {item['study']}: {item['reason']}" for item in excluded) or "- None",
    ).encode()
    return files
