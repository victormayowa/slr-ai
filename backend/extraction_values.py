"""Extraction field types: the shape of their values, validation, agreement between extractors, and display.

Values are stored as JSON objects whose keys depend on the field type, for example {"mean": 5.1, "sd": 1.2, "n": 60}
for a continuous outcome. Components can be missing (for example an unreported SD); missing_components reports them so
the reviewer can calculate, impute (with approval), or ask the authors.
"""

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import models


class InvalidValue(ValueError):
    """A value doesn't fit its field. The message is safe to show users."""


@dataclass(frozen=True)
class FieldType:
    label: str
    # Numeric parts of the value, for structured types.
    components: tuple[str, ...] = ()
    # Parts that must be whole, non-negative numbers.
    counts: tuple[str, ...] = ()
    # Parts expected for the value to be usable in synthesis.
    expected: tuple[str, ...] = ()


FIELD_TYPES: dict[str, FieldType] = {
    "text": FieldType("Short text"),
    "long_text": FieldType("Long text"),
    "number": FieldType("Number", ("number",)),
    "integer": FieldType("Whole number", ("number",), ("number",)),
    "categorical": FieldType("One choice"),
    "multi_select": FieldType("Several choices"),
    "boolean": FieldType("Yes or no"),
    "date": FieldType("Date"),
    "continuous": FieldType("Continuous outcome (mean, SD, n)", ("mean", "sd", "n"), ("n",), ("mean", "sd", "n")),
    "median_iqr": FieldType(
        "Skewed outcome (median, IQR, range, n)",
        ("median", "q1", "q3", "minimum", "maximum", "n"),
        ("n",),
        ("median", "n"),
    ),
    "dichotomous": FieldType(
        "Dichotomous outcome (events, total)", ("events", "total"), ("events", "total"), ("events", "total")
    ),
    "effect_estimate": FieldType(
        "Effect estimate with 95% CI",
        ("estimate", "ci_lower", "ci_upper", "p_value"),
        (),
        ("estimate", "ci_lower", "ci_upper"),
    ),
    "dta_2x2": FieldType(
        "Diagnostic 2×2 table", ("tp", "fp", "fn", "tn"), ("tp", "fp", "fn", "tn"), ("tp", "fp", "fn", "tn")
    ),
}
EFFECT_MEASURES = (
    "risk_ratio",
    "odds_ratio",
    "risk_difference",
    "hazard_ratio",
    "mean_difference",
    "standardized_mean_difference",
    "other",
)


def _number(name: str, raw: Any) -> float:
    if isinstance(raw, bool) or raw is None:
        raise InvalidValue(f"{name} must be a number")
    if isinstance(raw, str):
        cleaned = raw.strip().replace(",", "").replace("−", "-")
        try:
            raw = float(cleaned)
        except ValueError as exc:
            raise InvalidValue(f"{name} must be a number") from exc
    if not isinstance(raw, int | float) or not math.isfinite(raw):
        raise InvalidValue(f"{name} must be a number")
    return float(raw)


def _as_int_if_whole(value: float) -> float | int:
    return int(value) if value == int(value) else value


def validate(field: models.ExtractionField, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Check and normalize a value for the field. Raises InvalidValue."""
    if not isinstance(raw, dict):
        raise InvalidValue("The value must be an object")
    field_type = FIELD_TYPES.get(field.field_type)
    if field_type is None:
        raise InvalidValue(f"Unknown field type: {field.field_type}")
    kind = field.field_type

    if kind in ("text", "long_text"):
        text = str(raw.get("text") or "").strip()
        limit = 500 if kind == "text" else 20_000
        if not text:
            raise InvalidValue("Enter a value, or mark it as not reported")
        if len(text) > limit:
            raise InvalidValue(f"The text can be at most {limit} characters")
        return {"text": text}
    if kind == "categorical":
        choice = str(raw.get("choice") or "").strip()
        if choice not in field.options:
            raise InvalidValue(f"Choose one of: {', '.join(field.options)}")
        return {"choice": choice}
    if kind == "multi_select":
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or any(c not in field.options for c in choices):
            raise InvalidValue(f"Choose one or more of: {', '.join(field.options)}")
        return {"choices": [option for option in field.options if option in choices]}
    if kind == "boolean":
        if not isinstance(raw.get("bool"), bool):
            raise InvalidValue("Choose yes or no")
        return {"bool": raw["bool"]}
    if kind == "date":
        try:
            return {"date": date.fromisoformat(str(raw.get("date") or "")).isoformat()}
        except ValueError as exc:
            raise InvalidValue("Enter a date as YYYY-MM-DD") from exc

    value: dict[str, Any] = {}
    for name in field_type.components:
        if raw.get(name) in (None, ""):
            continue
        number = _number(name, raw[name])
        if name in field_type.counts and (number < 0 or number != int(number)):
            raise InvalidValue(f"{name} must be a whole number of at least 0")
        value[name] = _as_int_if_whole(number)
    if not value:
        raise InvalidValue("Enter at least one number, or mark the value as not reported")
    if kind == "effect_estimate":
        measure = str(raw.get("measure") or "other")
        if measure not in EFFECT_MEASURES:
            raise InvalidValue(f"The measure must be one of: {', '.join(EFFECT_MEASURES)}")
        value["measure"] = measure
        if {"estimate", "ci_lower", "ci_upper"} <= value.keys() and not (
            value["ci_lower"] <= value["estimate"] <= value["ci_upper"]
        ):
            raise InvalidValue("The estimate must lie within its confidence interval")
        if "p_value" in value and not 0 <= value["p_value"] <= 1:
            raise InvalidValue("The p-value must be between 0 and 1")
    if kind == "continuous" and value.get("sd", 1) < 0:
        raise InvalidValue("The SD can't be negative")
    if kind == "dichotomous" and "events" in value and "total" in value and value["events"] > value["total"]:
        raise InvalidValue("Events can't exceed the total")
    if kind == "median_iqr":
        ordered = [value[name] for name in ("minimum", "q1", "median", "q3", "maximum") if name in value]
        if ordered != sorted(ordered):
            raise InvalidValue("The values must be ordered: minimum ≤ q1 ≤ median ≤ q3 ≤ maximum")
    return value


def missing_components(field: models.ExtractionField, value: dict[str, Any] | None) -> list[str]:
    field_type = FIELD_TYPES.get(field.field_type)
    if field_type is None or value is None:
        return []
    return [name for name in field_type.expected if name not in value]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _numbers_agree(a: float, b: float, absolute: float, relative: float) -> bool:
    return abs(a - b) <= max(absolute, relative * max(abs(a), abs(b))) + 1e-12


def values_agree(
    field: models.ExtractionField,
    first: tuple[dict[str, Any] | None, bool],
    second: tuple[dict[str, Any] | None, bool],
    absolute_tolerance: float = 0.0,
    relative_tolerance: float = 0.0,
) -> bool:
    """Whether two (value, not_reported) entries agree, allowing the numeric tolerance for numbers."""
    (a, a_missing), (b, b_missing) = first, second
    if a_missing or b_missing:
        return a_missing and b_missing
    if a is None or b is None:
        return a is b
    components = FIELD_TYPES[field.field_type].components if field.field_type in FIELD_TYPES else ()
    if components:
        if set(a) != set(b):
            return False
        for key in a:
            if key in components:
                if not _numbers_agree(float(a[key]), float(b[key]), absolute_tolerance, relative_tolerance):
                    return False
            elif a[key] != b[key]:
                return False
        return True
    if "text" in a and "text" in b:
        return _normalize_text(a["text"]) == _normalize_text(b["text"])
    return a == b


def display(field: models.ExtractionField, value: dict[str, Any] | None, not_reported: bool = False) -> str:
    if not_reported:
        return "Not reported"
    if value is None:
        return ""
    if "text" in value:
        return str(value["text"])
    if "choice" in value:
        return str(value["choice"])
    if "choices" in value:
        return "; ".join(value["choices"])
    if "bool" in value:
        return "Yes" if value["bool"] else "No"
    if "date" in value:
        return str(value["date"])
    if "number" in value:
        return f"{value['number']}"
    return ", ".join(f"{key}={item}" for key, item in value.items())


def parse_suggestion(
    field: models.ExtractionField, text: str, components: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Read an AI-suggested value into the field's shape; None when it doesn't fit, so a reviewer enters it by hand."""
    kind = field.field_type
    raw: dict[str, Any]
    if kind in ("text", "long_text"):
        raw = {"text": text}
    elif kind == "categorical":
        match = next((option for option in field.options if option.casefold() == text.strip().casefold()), None)
        raw = {"choice": match}
    elif kind == "boolean":
        lowered = text.strip().casefold()
        raw = {"bool": True if lowered in ("yes", "true") else False if lowered in ("no", "false") else None}
    elif kind == "date":
        raw = {"date": text.strip()}
    elif kind in ("number", "integer"):
        found = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
        raw = {"number": found.group(0) if found else None}
    elif kind == "multi_select":
        raw = {"choices": [option for option in field.options if option.casefold() in text.casefold()]}
    else:
        raw = dict(components or {})
    try:
        return validate(field, raw)
    except InvalidValue:
        return None
