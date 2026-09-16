"""The AI benchmark harness: data sets with known answers, metrics, runs against catalog models, and model validation.

Data sets are JSON files in BENCHMARK_DATASETS_DIR (default backend/benchmarks/datasets), uploaded by administrators
or built with scripts/fetch_synergy_dataset.py. The statistics benchmark checks the R engine against published values
(benchmarks/statistics_reference.json).

A model is validated when its latest completed runs for screening, extraction, and risk of bias, with the current
prompt versions, all pass their thresholds.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from agreement import cohens_kappa

logger = logging.getLogger(__name__)

BENCHMARK_DIR = Path(__file__).resolve().parent / "benchmarks"
DATASETS_DIR = Path(os.getenv("BENCHMARK_DATASETS_DIR", str(BENCHMARK_DIR / "datasets")))
STATISTICS_KEY = "statistics-reference"
TASKS = ("screening", "extraction", "appraisal", "statistics")
MODEL_TASKS = ("screening", "extraction", "appraisal")
DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    "screening": {"recall": 0.95, "specificity": 0.30},
    "extraction": {"accuracy": 0.85},
    "appraisal": {"kappa": 0.60},
    "statistics": {"within_tolerance": 1.0},
}
CHUNK = 5
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,99}$")


class BenchmarkError(ValueError):
    """A data set or run is invalid. The message is safe to show users."""


def thresholds() -> dict[str, dict[str, float]]:
    configured = os.getenv("BENCHMARK_THRESHOLDS", "").strip()
    merged = {task: dict(values) for task, values in DEFAULT_THRESHOLDS.items()}
    if configured:
        for task, values in json.loads(configured).items():
            merged.setdefault(task, {}).update({k: float(v) for k, v in values.items()})
    return merged


@dataclass
class Dataset:
    key: str
    name: str
    task: str
    description: str
    source: str
    license: str
    content: dict[str, Any]
    sha256: str

    @property
    def size(self) -> int:
        return len(self.content.get("cases" if self.task == "statistics" else "items", []))

    def out(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "task": self.task,
            "description": self.description,
            "source": self.source,
            "license": self.license,
            "items": self.size,
            "sha256": self.sha256,
        }


def validate_dataset(content: dict[str, Any]) -> None:
    key, task = content.get("key", ""), content.get("task", "")
    if not KEY_PATTERN.match(str(key)):
        raise BenchmarkError("A data set needs a key of lowercase letters, digits, hyphens, and underscores")
    if task not in TASKS:
        raise BenchmarkError(f"task must be one of {', '.join(TASKS)}")
    if not content.get("name"):
        raise BenchmarkError("A data set needs a name")
    if task == "statistics":
        if not content.get("cases"):
            raise BenchmarkError("A statistics data set needs cases")
        return
    items = content.get("items")
    if not isinstance(items, list) or len(items) < 2:
        raise BenchmarkError("A data set needs at least two items")
    if task == "screening":
        if not any(c.get("kind") == "inclusion" and c.get("text") for c in content.get("criteria", [])):
            raise BenchmarkError("A screening data set needs its inclusion criteria")
        for item in items:
            if not item.get("title") or not isinstance(item.get("label"), bool):
                raise BenchmarkError("Every screening item needs a title and a true or false label")
        if not any(i["label"] for i in items) or all(i["label"] for i in items):
            raise BenchmarkError("A screening data set needs both included and excluded items")
    elif task == "extraction":
        names = {f.get("name") for f in content.get("fields", [])}
        if not names:
            raise BenchmarkError("An extraction data set needs fields")
        for item in items:
            if not item.get("text") or not isinstance(item.get("expected"), dict) or set(item["expected"]) - names:
                raise BenchmarkError("Every extraction item needs text and expected values for known fields")
    else:
        from appraisal_tools import TOOLS

        tool = TOOLS.get(content.get("tool", ""))
        if tool is None:
            raise BenchmarkError("An appraisal data set needs a known tool")
        domains = {d.key for d in tool.domains}
        for item in items:
            if not item.get("text") or not isinstance(item.get("expected"), dict) or set(item["expected"]) - domains:
                raise BenchmarkError(f"Every appraisal item needs text and expected judgments for {tool.label} domains")


def _dataset(content: dict[str, Any], raw: bytes) -> Dataset:
    return Dataset(
        content["key"],
        content["name"],
        content["task"],
        content.get("description", ""),
        content.get("source", ""),
        content.get("license", ""),
        content,
        hashlib.sha256(raw).hexdigest(),
    )


def available_datasets() -> list[Dataset]:
    paths = [BENCHMARK_DIR / "statistics_reference.json"]
    if DATASETS_DIR.is_dir():
        paths += sorted(DATASETS_DIR.glob("*.json"))
    datasets = []
    for path in paths:
        raw = path.read_bytes()
        try:
            content = json.loads(raw)
            validate_dataset(content)
        except (ValueError, BenchmarkError):
            logger.warning("Skipping invalid benchmark data set %s", path.name)
            continue
        datasets.append(_dataset(content, raw))
    return datasets


def get_dataset(key: str) -> Dataset:
    dataset = next((d for d in available_datasets() if d.key == key), None)
    if dataset is None:
        raise BenchmarkError(f"No benchmark data set named {key}")
    return dataset


def save_dataset(raw: bytes) -> Dataset:
    try:
        content = json.loads(raw)
    except ValueError as exc:
        raise BenchmarkError("The data set must be a JSON file") from exc
    if not isinstance(content, dict):
        raise BenchmarkError("The data set must be a JSON object")
    validate_dataset(content)
    if content["key"] == STATISTICS_KEY:
        raise BenchmarkError("That key is reserved")
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    (DATASETS_DIR / f"{content['key']}.json").write_bytes(raw)
    return _dataset(content, raw)


# Metrics


def screening_metrics(labels: Sequence[bool], predictions: Sequence[bool], scores: Sequence[float]) -> dict[str, Any]:
    tp = sum(1 for label, pred in zip(labels, predictions, strict=True) if label and pred)
    fn = sum(1 for label, pred in zip(labels, predictions, strict=True) if label and not pred)
    tn = sum(1 for label, pred in zip(labels, predictions, strict=True) if not label and not pred)
    fp = sum(1 for label, pred in zip(labels, predictions, strict=True) if not label and pred)
    n = len(labels)
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    precision = tp / (tp + fp) if tp + fp else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    wss = None
    includes = tp + fn
    if includes and n:
        needed = math.ceil(0.95 * includes)
        ranked = sorted(zip(scores, labels, strict=True), key=lambda pair: pair[0], reverse=True)
        found = 0
        for position, (_, label) in enumerate(ranked, start=1):
            found += int(label)
            if found >= needed:
                wss = (n - position) / n - 0.05
                break
    return {
        "n": n,
        "true_positives": tp,
        "false_negatives": fn,
        "true_negatives": tn,
        "false_positives": fp,
        "recall": recall,
        "specificity": specificity,
        "precision": precision,
        "f1": f1,
        "wss_at_95": wss,
    }


def values_match(expected: Any, actual: Any, tolerance: float = 0.01) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, bool) or isinstance(actual, bool):
        return str(expected).lower() == str(actual).lower()
    try:
        e, a = float(expected), float(str(actual).replace(",", ""))
    except (TypeError, ValueError):
        return " ".join(str(expected).lower().split()) == " ".join(str(actual).lower().split())
    return abs(e - a) <= tolerance * max(abs(e), 1e-9) or e == a


def extraction_metrics(pairs: Sequence[tuple[Any, Any]]) -> dict[str, Any]:
    correct = sum(1 for expected, actual in pairs if values_match(expected, actual))
    return {"n": len(pairs), "correct": correct, "accuracy": correct / len(pairs) if pairs else None}


def agreement_metrics(pairs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    if not pairs:
        return {"n": 0, "agreement": None, "kappa": None}
    categories = sorted({c for pair in pairs for c in pair})
    result = cohens_kappa(pairs, categories)
    return {"n": len(pairs), "agreement": sum(1 for a, b in pairs if a == b) / len(pairs), "kappa": result.kappa}


def passes(task: str, metrics: dict[str, Any], limits: dict[str, float]) -> bool:
    return all(metrics.get(name) is not None and metrics[name] >= limit for name, limit in limits.items())


def _lookup(results: Any, path: str) -> Any:
    value = results
    for part in path.split("."):
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def statistics_benchmark(dataset: Dataset) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from stats_engine import ANALYSIS_TYPES, run_analysis

    details = []
    for case in dataset.content["cases"]:
        template, packages = ANALYSIS_TYPES[case["analysis_type"]]
        output = run_analysis(template, case["spec"], case["rows"], packages)
        for check in case["expected"]:
            actual = _lookup(output.results, check["path"])
            ok = isinstance(actual, int | float) and abs(actual - check["value"]) <= check["tolerance"]
            details.append(
                {"case": case["name"], "path": check["path"], "expected": check["value"], "actual": actual, "ok": ok}
            )
    within = sum(1 for d in details if d["ok"])
    return {
        "n": len(details),
        "within": within,
        "within_tolerance": within / len(details) if details else None,
    }, details


# Runs


async def _gather_chunks(calls: list[Any]) -> list[Any]:
    results: list[Any] = []
    for start in range(0, len(calls), CHUNK):
        results += await asyncio.gather(*calls[start : start + CHUNK], return_exceptions=True)
    return results


async def _run_model_task(
    db: Session, run: models.BenchmarkRun, dataset: Dataset
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from ai_access import resolve_ai
    from appraisal_tools import TOOLS
    from services.ai_appraisal import suggest_appraisal
    from services.ai_screening import CriterionPrompt, FieldPrompt, evaluate_eligibility, extract_study_data

    model = db.get(models.AIModel, run.ai_model_id) if run.ai_model_id else None
    starter = db.get(models.User, run.started_by_id) if run.started_by_id else None
    if model is None or starter is None:
        raise BenchmarkError("The model or the person who started the run no longer exists")
    try:
        ai = resolve_ai(db, model, starter)
    except Exception as exc:  # HTTPException from resolve_ai carries a safe message
        raise BenchmarkError(getattr(exc, "detail", "The model can't be used")) from exc
    items = dataset.content["items"]
    details: list[dict[str, Any]] = []
    if run.task == "screening":
        criteria = [CriterionPrompt(i + 1, c["kind"], c["text"]) for i, c in enumerate(dataset.content["criteria"])]
        calls = [
            evaluate_eligibility(
                ai, f"Title: {item['title']}\nAbstract: {item.get('abstract') or 'No abstract'}", criteria
            )
            for item in items
        ]
        results = await _gather_chunks(calls)
        labels, predictions, scores = [], [], []
        for item, result in zip(items, results, strict=True):
            if isinstance(result, BaseException):
                details.append({"id": item.get("id"), "error": type(result).__name__})
                prediction, score = True, 1.0
            else:
                decision = result.value.decision
                prediction = decision in ("Include", "Maybe")
                confidence = result.value.confidence if result.value.confidence is not None else 1.0
                score = confidence if prediction else 1 - confidence
                details.append(
                    {
                        "id": item.get("id"),
                        "label": item["label"],
                        "decision": decision,
                        "confidence": result.value.confidence,
                    }
                )
            labels.append(bool(item["label"]))
            predictions.append(prediction)
            scores.append(score)
        run.processed = len(items)
        return screening_metrics(labels, predictions, scores), details
    if run.task == "extraction":
        field_specs = dataset.content["fields"]
        fields = [
            FieldPrompt(i + 1, f["name"], f.get("field_type", "text"), False, f.get("unit", ""))
            for i, f in enumerate(field_specs)
        ]
        by_id = {field.id: field.name for field in fields}
        results = await _gather_chunks([extract_study_data(ai, [], item["text"], fields, []) for item in items])
        pairs: list[tuple[Any, Any]] = []
        for item, result in zip(items, results, strict=True):
            extracted = (
                {}
                if isinstance(result, BaseException)
                else {by_id.get(v.field_id): (None if v.not_reported else v.value) for v in result.value.values}
            )
            for name, expected in item["expected"].items():
                actual = extracted.get(name)
                pairs.append((expected, actual))
                details.append(
                    {
                        "id": item.get("id"),
                        "field": name,
                        "expected": expected,
                        "actual": actual,
                        "ok": values_match(expected, actual),
                    }
                )
        run.processed = len(items)
        return extraction_metrics(pairs), details
    tool = TOOLS[dataset.content["tool"]]
    results = await _gather_chunks(
        [suggest_appraisal(ai, tool, item.get("outcome", ""), [], item["text"]) for item in items]
    )
    judgment_pairs: list[tuple[str, str]] = []
    for item, result in zip(items, results, strict=True):
        suggested = {} if isinstance(result, BaseException) else {d.domain: d.judgment for d in result.value.domains}
        for domain, expected in item["expected"].items():
            actual = suggested.get(domain, "missing")
            judgment_pairs.append((expected, actual))
            details.append(
                {
                    "id": item.get("id"),
                    "domain": domain,
                    "expected": expected,
                    "actual": actual,
                    "ok": expected == actual,
                }
            )
    run.processed = len(items)
    return agreement_metrics(judgment_pairs), details


def prompt_version(task: str) -> str:
    from llm.prompts import APPRAISAL_PROMPT, EXTRACTION_PROMPT, SCREENING_PROMPT

    return {"screening": SCREENING_PROMPT.id, "extraction": EXTRACTION_PROMPT.id, "appraisal": APPRAISAL_PROMPT.id}.get(
        task, ""
    )


async def run_benchmark(db: Session, run_id: int) -> None:
    run = db.get(models.BenchmarkRun, run_id)
    if run is None or run.status not in ("queued", "running"):
        return
    run.status = "running"
    db.commit()
    try:
        dataset = get_dataset(run.dataset_key)
        if dataset.sha256 != run.dataset_sha256:
            raise BenchmarkError("The data set changed after the run was queued")
        if run.task == "statistics":
            metrics, details = await asyncio.to_thread(statistics_benchmark, dataset)
            run.processed = dataset.size
        else:
            metrics, details = await _run_model_task(db, run, dataset)
        run.metrics, run.details = metrics, details[:1000]
        run.passed = passes(run.task, metrics, run.thresholds)
        run.status = "completed"
    except BenchmarkError as exc:
        run.status, run.error = "failed", str(exc)
    except Exception:
        logger.exception("Benchmark run %s failed", run_id)
        run.status, run.error = "failed", "Unexpected error while running the benchmark"
    run.finished_at = models.utcnow()
    if run.ai_model_id:
        model = db.get(models.AIModel, run.ai_model_id)
        if model is not None:
            refresh_model_status(db, model)
    db.commit()


def latest_runs(db: Session, model: models.AIModel) -> dict[str, models.BenchmarkRun | None]:
    return {
        task: db.scalar(
            select(models.BenchmarkRun)
            .where(
                models.BenchmarkRun.ai_model_id == model.id,
                models.BenchmarkRun.task == task,
                models.BenchmarkRun.prompt_version == prompt_version(task),
                models.BenchmarkRun.status == "completed",
            )
            .order_by(models.BenchmarkRun.id.desc())
            .limit(1)
        )
        for task in MODEL_TASKS
    }


def refresh_model_status(db: Session, model: models.AIModel) -> None:
    if model.benchmark_status == "exempt" or model.purpose != "chat":
        return
    runs = latest_runs(db, model)
    if any(r is not None and r.passed is False for r in runs.values()):
        model.benchmark_status = "failed"
    elif all(r is not None and r.passed for r in runs.values()):
        model.benchmark_status = "passed"
    else:
        model.benchmark_status = "unvalidated"
    model.validated_prompts = sorted({r.prompt_version for r in runs.values() if r is not None and r.passed})


def validation_required() -> bool:
    return os.getenv("REQUIRE_VALIDATED_MODELS", "false").strip().lower() == "true"


async def enqueue_benchmark(run_id: int) -> None:
    from arq import create_pool

    from jobs import QUEUE_NAME, redis_settings

    pool = await create_pool(redis_settings())
    try:
        await pool.enqueue_job("run_benchmark_job", run_id, _job_id=f"benchmark-{run_id}", _queue_name=QUEUE_NAME)
    finally:
        await pool.aclose()
