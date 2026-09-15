"""The statistics engine: every analysis runs in R, with validated packages (metafor, meta, netmeta, mada,
clubSandwich, lme4, bayesmeta, robvis).

Each run writes a working folder holding the analysis specification (spec.json), its data (data.json and data.csv),
and a standalone script (analysis.R: the shared helpers followed by the analysis template from backend/stats/r). R is
started with Rscript --vanilla in that folder and writes results.json, plots, and session_info.txt. The same folder,
exported, reruns outside OmniReview. Specifications and data are passed as JSON files and never written into code.

R is located from RSCRIPT_PATH, or Rscript on PATH. backend/scripts/setup_r_env.sh installs it without sudo.
"""

import asyncio
import csv
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from appraisal_tools import Tool

logger = logging.getLogger(__name__)

R_DIR = Path(__file__).resolve().parent / "stats" / "r"
REQUIRED_PACKAGES = (
    "jsonlite",
    "metafor",
    "meta",
    "netmeta",
    "mada",
    "clubSandwich",
    "lme4",
    "bayesmeta",
    "ggplot2",
    "svglite",
    "robvis",
)
# The R template for each analysis type, and the packages it needs.
ANALYSIS_TYPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "pairwise": ("pairwise.R", ("jsonlite", "metafor", "svglite")),
    "nma": ("nma.R", ("jsonlite", "meta", "netmeta", "svglite")),
    "dta": ("dta.R", ("jsonlite", "mada", "svglite")),
    "bayesian": ("bayesian.R", ("jsonlite", "metafor", "bayesmeta", "svglite")),
    "rve": ("rve.R", ("jsonlite", "metafor", "clubSandwich", "svglite")),
    "ipd": ("ipd.R", ("jsonlite", "metafor", "lme4", "svglite")),
    "swim": ("swim.R", ("jsonlite", "ggplot2", "svglite")),
}
MEDIA_TYPES = {"svg": "image/svg+xml", "png": "image/png", "pdf": "application/pdf", "tiff": "image/tiff"}
TIMEOUT_SECONDS = int(os.getenv("STATS_TIMEOUT_SECONDS", "900"))
_STATUS_TTL_SECONDS = 300


class StatsEngineUnavailable(Exception):
    """R or a package it needs isn't installed. The message is safe to show users."""


class StatsRunError(Exception):
    """An analysis failed in R. The message is safe to show users; the log holds R's output."""

    def __init__(self, message: str, log: str = "") -> None:
        super().__init__(message)
        self.log = log


@dataclass
class EngineStatus:
    available: bool
    rscript: str | None
    r_version: str
    packages: dict[str, str | None]
    message: str

    def missing(self, needed: Sequence[str] = REQUIRED_PACKAGES) -> list[str]:
        return [name for name in needed if not self.packages.get(name)]


_status_cache: tuple[float, EngineStatus] | None = None


def rscript_path() -> str | None:
    configured = os.getenv("RSCRIPT_PATH", "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    return shutil.which("Rscript")


def _r_environment(home: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "R_LIBS_USER": "",
        "OMP_NUM_THREADS": "1",
    }


def engine_status(refresh: bool = False) -> EngineStatus:
    """Whether R is installed, and the versions of the packages analyses use (cached for a few minutes)."""
    global _status_cache
    if not refresh and _status_cache and time.monotonic() - _status_cache[0] < _STATUS_TTL_SECONDS:
        return _status_cache[1]
    rscript = rscript_path()
    if rscript is None:
        status = EngineStatus(
            False,
            None,
            "",
            {},
            "R isn't installed on the server. Run backend/scripts/setup_r_env.sh and set RSCRIPT_PATH.",
        )
    else:
        names = ", ".join(f'"{name}"' for name in REQUIRED_PACKAGES)
        code = (
            f"p <- c({names}); "
            "v <- vapply(p, function(x) if (requireNamespace(x, quietly = TRUE)) "
            'as.character(utils::packageVersion(x)) else "", ""); '
            'cat(R.version.string, "\\n"); cat(paste(p, v, sep = "="), sep = "\\n")'
        )
        with tempfile.TemporaryDirectory(prefix="omnireview-r-") as home:
            try:
                completed = subprocess.run(
                    [rscript, "--vanilla", "-e", code],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=_r_environment(home),
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                logger.warning("Checking the R installation failed", exc_info=True)
                completed = None
        if completed is None or completed.returncode != 0:
            status = EngineStatus(False, rscript, "", {}, "R is installed but couldn't be started. Check RSCRIPT_PATH.")
        else:
            lines = completed.stdout.strip().splitlines()
            packages = {}
            for line in lines[1:]:
                name, _, version = line.partition("=")
                packages[name.strip()] = version.strip() or None
            status = EngineStatus(True, rscript, lines[0].strip() if lines else "", packages, "")
            missing = status.missing()
            if missing:
                status.message = (
                    f"These R packages are missing: {', '.join(missing)}. Run backend/scripts/setup_r_env.sh."
                )
    _status_cache = (time.monotonic(), status)
    return status


def capabilities(status: EngineStatus | None = None) -> dict[str, Any]:
    status = status or engine_status()
    return {
        "available": status.available,
        "r_version": status.r_version,
        "packages": status.packages,
        "message": status.message,
        "analysis_types": {
            key: {
                "available": status.available and not status.missing(needed),
                "missing_packages": status.missing(needed),
            }
            for key, (_, needed) in ANALYSIS_TYPES.items()
        },
    }


@dataclass
class PlotFile:
    name: str
    format: str
    content: bytes

    @property
    def media_type(self) -> str:
        return MEDIA_TYPES.get(self.format, "application/octet-stream")


@dataclass
class RunOutput:
    results: dict[str, Any]
    plots: list[PlotFile]
    script: str
    session_info: str
    r_version: str
    packages: dict[str, str]
    log: str
    duration_ms: int
    files: dict[str, bytes] = field(default_factory=dict)


def render_script(template: str) -> str:
    """The standalone script for a template: a header, the shared helpers, and the analysis."""
    header = (
        "# OmniReview analysis script. It reads spec.json and data.json from its folder and writes results.json,\n"
        "# plots/, and session_info.txt. Rerun it with: Rscript --vanilla analysis.R\n\n"
    )
    return header + (R_DIR / "common.R").read_text() + "\n\n" + (R_DIR / template).read_text()


def rows_to_csv(rows: Sequence[Mapping[str, Any]]) -> str:
    columns: list[str] = []
    for row in rows:
        columns += [key for key in row if key not in columns]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in columns})
    return buffer.getvalue()


def working_files(template: str, spec: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, bytes]:
    return {
        "analysis.R": render_script(template).encode(),
        "spec.json": json.dumps(spec, indent=2, default=str).encode(),
        "data.json": json.dumps(list(rows), indent=2, default=str).encode(),
        "data.csv": rows_to_csv(rows).encode(),
    }


def run_analysis(
    template: str,
    spec: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    needed: Sequence[str] = ("jsonlite",),
    extra_files: Mapping[str, bytes] | None = None,
) -> RunOutput:
    """Run an R template on the specification and data. Raises StatsEngineUnavailable or StatsRunError."""
    status = engine_status()
    if not status.available or status.rscript is None:
        raise StatsEngineUnavailable(status.message)
    missing = status.missing(needed)
    if missing:
        raise StatsEngineUnavailable(f"This analysis needs R packages that aren't installed: {', '.join(missing)}")
    files = working_files(template, spec, rows)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="omnireview-stats-") as folder:
        root = Path(folder)
        for name, content in {**files, **(extra_files or {})}.items():
            (root / name).write_bytes(content)
        try:
            completed = subprocess.run(
                [status.rscript, "--vanilla", "analysis.R"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                env=_r_environment(folder),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise StatsRunError(f"The analysis took longer than {TIMEOUT_SECONDS} seconds and was stopped") from exc
        log = (completed.stdout + "\n" + completed.stderr)[-20_000:]
        error_file = root / "error.txt"
        results_file = root / "results.json"
        if completed.returncode != 0 or not results_file.exists():
            message = (
                error_file.read_text().strip() if error_file.exists() else "The analysis failed in R; see the log."
            )
            raise StatsRunError(message[:2000], log)
        results = json.loads(results_file.read_text())
        plots = []
        plot_dir = root / "plots"
        if plot_dir.exists():
            for path in sorted(plot_dir.iterdir()):
                if path.suffix.lstrip(".") in MEDIA_TYPES:
                    plots.append(PlotFile(path.stem, path.suffix.lstrip("."), path.read_bytes()))
        session_file = root / "session_info.txt"
        session_info = session_file.read_text() if session_file.exists() else ""
    return RunOutput(
        results=results,
        plots=plots,
        script=files["analysis.R"].decode(),
        session_info=session_info,
        r_version=status.r_version,
        packages={name: version for name, version in status.packages.items() if version},
        log=log,
        duration_ms=int((time.monotonic() - started) * 1000),
        files=files,
    )


def export_bundle(files: Mapping[str, bytes], extra: Mapping[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in {**files, **(extra or {})}.items():
            archive.writestr(name, content)
    return buffer.getvalue()


# robvis judgment labels for OmniReview's judgment keys.
ROBVIS_LABELS = {
    "low": "Low",
    "some_concerns": "Some concerns",
    "high": "High",
    "moderate": "Moderate",
    "serious": "Serious",
    "critical": "Critical",
    "very_high": "Very high",
    "no_information": "No information",
    "unclear": "Unclear",
}


async def render_robvis(
    tool: Tool, domains: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], kind: str, fmt: str
) -> tuple[bytes, str]:
    """A traffic-light or summary risk of bias plot for signed-off assessments."""
    bias_domains = [d for d in domains if d.get("kind") == "bias"]
    data = []
    for row in rows:
        label = f"{row['study']} ({row['outcome']})" if row.get("outcome") else str(row["study"])
        entry: dict[str, Any] = {"Study": label}
        for index, domain in enumerate(bias_domains, start=1):
            entry[f"D{index}"] = ROBVIS_LABELS.get(row["domains"].get(domain["key"], ""), "No information")
        entry["Overall"] = ROBVIS_LABELS.get(row.get("overall") or "", "No information")
        entry["Weight"] = 1
        data.append(entry)
    spec = {
        "robvis_tool": tool.robvis_tool,
        "kind": kind,
        "plot_formats": [fmt],
        "domain_labels": [d["label"] for d in bias_domains],
        "seed": 1,
    }
    output = await asyncio.to_thread(run_analysis, "robvis.R", spec, data, ("jsonlite", "ggplot2", "svglite"))
    plot = next((p for p in output.plots if p.name == kind and p.format == fmt), None)
    if plot is None:
        raise StatsRunError("The plot wasn't produced", output.log)
    return plot.content, plot.media_type
