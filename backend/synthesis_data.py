"""Analysis specifications, and the data sets built for them from the extraction data set, risk of bias assessments,
and individual participant data.

Final analyses use the data set locked when extraction was signed off. Before that, analyses run on the current values
for exploration and are never marked final.
"""

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import crypto
import models
from extraction_data import dataset_content
from storage import document_storage

EFFECT_MEASURES = {
    "risk_ratio": "RR",
    "odds_ratio": "OR",
    "hazard_ratio": "HR",
    "risk_difference": "RD",
    "mean_difference": "MD",
    "standardized_mean_difference": "SMD",
}
DATA_TYPES = {"dichotomous": "binary", "continuous": "continuous", "effect_estimate": "generic", "dta_2x2": "dta"}
MEASURES_BY_DATA = {
    "binary": ("RR", "OR", "RD"),
    "continuous": ("MD", "SMD", "ROM"),
    "generic": ("RR", "OR", "HR", "RD", "MD", "SMD"),
}
RATIO_MEASURES = {"RR", "OR", "HR", "ROM"}
HIGH_RISK = {"high", "serious", "critical", "very_high"}
TAU_METHODS = Literal["REML", "DL", "PM", "SJ", "ML", "EB", "HE", "HS"]
PlotFormat = Literal["svg", "png", "pdf", "tiff"]


def _default_plot_formats() -> list[PlotFormat]:
    return ["svg", "png", "pdf"]


class DatasetError(ValueError):
    """An analysis can't be built as specified. The message is safe to show users."""


class ArmPair(BaseModel):
    study_id: int
    treatment_arm_id: int
    control_arm_id: int


class GroupingSpec(BaseModel):
    source: Literal["field", "risk_of_bias", "year"] = "field"
    field_id: int | None = None
    label: str = Field("", max_length=200)
    type: Literal["numeric", "categorical"] = "categorical"
    prespecified: bool = False


class ExclusionSpec(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    study_ids: list[int] = Field(max_length=500)


class SensitivitySpec(BaseModel):
    leave_one_out: bool = False
    influence: bool = False
    exclude_high_risk_of_bias: bool = False
    alternative_estimators: list[TAU_METHODS] = Field(default_factory=list, max_length=8)
    exclusions: list[ExclusionSpec] = Field(default_factory=list, max_length=20)


class PublicationBiasSpec(BaseModel):
    funnel: bool = False
    contour: bool = False
    egger: bool = False
    begg: bool = False
    trim_fill: bool = False
    pet_peese: bool = False
    selection_model: bool = False


class SwimRow(BaseModel):
    study_id: int
    direction: Literal["benefit", "harm", "no_clear_difference", "conflicting"]
    outcome_domain: str = Field("Primary outcome", min_length=1, max_length=200)
    note: str = Field("", max_length=1000)


class IPDSpec(BaseModel):
    outcome_type: Literal["binary", "continuous"] = "binary"
    adjust_for: list[str] = Field(default_factory=list, max_length=20)


class AnalysisSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: int | None = None
    # Robust variance estimation: every field contributing effects.
    field_ids: list[int] = Field(default_factory=list, max_length=20)
    measure: str = Field("RR", max_length=10)
    model: Literal["random", "fixed", "mantel_haenszel", "peto", "glmm"] = "random"
    tau_method: TAU_METHODS = "REML"
    hksj: bool = True
    prediction_interval: bool = True
    level: float = Field(95, ge=50, le=99.9)
    zero_correction: float = Field(0.5, ge=0, le=1)
    arms: list[ArmPair] = Field(default_factory=list, max_length=500)
    # Network meta-analysis: arm id -> treatment node.
    treatments: dict[str, str] = Field(default_factory=dict)
    reference_treatment: str = Field("", max_length=200)
    small_values: Literal["desirable", "undesirable"] = "desirable"
    subgroup: GroupingSpec | None = None
    moderators: list[GroupingSpec] = Field(default_factory=list, max_length=5)
    sensitivity: SensitivitySpec = SensitivitySpec()
    publication_bias: PublicationBiasSpec = PublicationBiasSpec()
    tau_prior_scale: float = Field(0.5, gt=0, le=10)
    mu_prior_sd: float | None = Field(None, gt=0)
    ipd: IPDSpec = IPDSpec()
    swim: list[SwimRow] = Field(default_factory=list, max_length=500)
    plot_formats: list[PlotFormat] = Field(default_factory=_default_plot_formats)
    seed: int = Field(20260915, ge=1, le=2_147_483_647)


@dataclass
class BuiltDataset:
    rows: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    r_spec: dict[str, Any]
    source: str
    snapshot_id: int | None
    extra_files: dict[str, bytes] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        digest = hashlib.sha256(json.dumps(self.rows, sort_keys=True, default=str).encode())
        for name in sorted(self.extra_files):
            digest.update(name.encode())
            digest.update(self.extra_files[name])
        return digest.hexdigest()

    @property
    def studies(self) -> int:
        return len({row["study_id"] for row in self.rows if "study_id" in row})


def extraction_source(db: Session, project_id: int) -> tuple[dict[str, Any], str, int | None]:
    """The locked extraction data set when extraction is signed off, otherwise the current values."""
    stage = db.scalar(
        select(models.ProjectStage).where(
            models.ProjectStage.project_id == project_id, models.ProjectStage.stage == "extraction"
        )
    )
    snapshot = db.scalar(
        select(models.StageSnapshot)
        .where(models.StageSnapshot.project_id == project_id, models.StageSnapshot.stage == "extraction")
        .order_by(models.StageSnapshot.version.desc())
        .limit(1)
    )
    if stage is not None and stage.completed_at is not None and snapshot is not None:
        return snapshot.content, "locked", snapshot.id
    return dataset_content(db, project_id), "current", None


def risk_of_bias_by_study(db: Session, project_id: int, outcome: str) -> dict[int, str]:
    """Each study's signed-off overall risk of bias, preferring the assessment of this outcome."""
    chosen: dict[int, models.AppraisalAssessment] = {}
    for assessment in db.scalars(
        select(models.AppraisalAssessment)
        .where(models.AppraisalAssessment.project_id == project_id, models.AppraisalAssessment.status == "signed_off")
        .order_by(models.AppraisalAssessment.id)
    ):
        current = chosen.get(assessment.study_id)
        matches = assessment.outcome.casefold() == outcome.casefold()
        if current is None or (matches and current.outcome.casefold() != outcome.casefold()):
            chosen[assessment.study_id] = assessment
    return {study_id: a.overall_judgment or "" for study_id, a in chosen.items()}


def _number(value: dict[str, Any] | None, key: str) -> float | None:
    raw = ((value or {}).get("value") or {}).get(key)
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None


def _display(value: dict[str, Any] | None) -> str:
    content = (value or {}).get("value") or {}
    for key in ("choice", "text", "number", "date"):
        if key in content:
            return str(content[key])
    if "bool" in content:
        return "Yes" if content["bool"] else "No"
    return ""


def _study_years(db: Session, content: dict[str, Any]) -> dict[int, float]:
    record_to_study = {
        report["record_id"]: study["study_id"]
        for study in content["studies"]
        for report in study["reports"]
        if report.get("is_primary")
    }
    years: dict[int, float] = {}
    for record in db.scalars(select(models.Record).where(models.Record.id.in_(list(record_to_study)))):
        if record.year[:4].isdigit():
            years[record_to_study[record.id]] = float(record.year[:4])
    return years


def build_dataset(db: Session, project_id: int, analysis_type: str, spec: AnalysisSpec, outcome: str) -> BuiltDataset:
    content, source, snapshot_id = extraction_source(db, project_id)
    fields = {item["id"]: item for item in content["fields"]}
    studies = {study["study_id"]: study for study in content["studies"]}
    values = {
        (study["study_id"], value["field_id"], value["arm_id"]): value
        for study in content["studies"]
        for value in study["values"]
    }
    arm_labels = {arm["arm_id"]: arm["label"] for study in content["studies"] for arm in study["arms"]}
    rob = risk_of_bias_by_study(db, project_id, outcome)
    years = _study_years(db, content)
    excluded: list[dict[str, Any]] = []

    def exclude(study_id: int, reason: str) -> None:
        excluded.append(
            {"study_id": study_id, "study": studies.get(study_id, {}).get("label", str(study_id)), "reason": reason}
        )

    def grouping_value(grouping: GroupingSpec, study_id: int) -> Any:
        if grouping.source == "risk_of_bias":
            return rob.get(study_id) or None
        if grouping.source == "year":
            return years.get(study_id)
        value = values.get((study_id, grouping.field_id, None))
        if grouping.type == "numeric":
            return _number(value, "number")
        return _display(value) or None

    def base(study_id: int) -> dict[str, Any]:
        row: dict[str, Any] = {"study_id": study_id, "study": studies[study_id]["label"], "rob": rob.get(study_id, "")}
        if spec.subgroup is not None:
            row["subgroup"] = grouping_value(spec.subgroup, study_id)
        for index, moderator in enumerate(spec.moderators, start=1):
            row[f"mod_{index}"] = grouping_value(moderator, study_id)
        return row

    def field_of(field_id: int | None) -> dict[str, Any]:
        item = fields.get(field_id) if field_id is not None else None
        if item is None:
            raise DatasetError("Choose the extraction field that holds this analysis's data")
        return item

    def effect_rows(item: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        data_type = DATA_TYPES.get(item["field_type"])
        if data_type not in MEASURES_BY_DATA:
            raise DatasetError(
                f"{item['name']} doesn't hold effect data (use a dichotomous, continuous, or effect estimate field)"
            )
        if spec.measure not in MEASURES_BY_DATA[data_type]:
            raise DatasetError(
                f"{spec.measure} can't be computed from {item['name']}; choose one of "
                f"{', '.join(MEASURES_BY_DATA[data_type])}"
            )
        rows = []
        if data_type == "generic":
            for study_id in studies:
                value = values.get((study_id, item["id"], None))
                content_value = (value or {}).get("value") or {}
                if not content_value:
                    exclude(study_id, f"{item['name']} not extracted or not reported")
                    continue
                if EFFECT_MEASURES.get(str(content_value.get("measure")), "") != spec.measure:
                    exclude(
                        study_id, f"{item['name']} is reported as {content_value.get('measure')}, not {spec.measure}"
                    )
                    continue
                estimate, lower, upper = (_number(value, k) for k in ("estimate", "ci_lower", "ci_upper"))
                if None in (estimate, lower, upper):
                    exclude(study_id, f"{item['name']} lacks an estimate with its confidence interval")
                    continue
                rows.append(
                    {
                        **base(study_id),
                        "effect": item["name"],
                        "estimate": estimate,
                        "ci_lower": lower,
                        "ci_upper": upper,
                    }
                )
            return rows, data_type
        if not item["per_arm"]:
            raise DatasetError(f"{item['name']} must be extracted per arm to compare arms")
        pairs = {pair.study_id: pair for pair in spec.arms}
        for study_id in studies:
            pair = pairs.get(study_id)
            if pair is None:
                exclude(study_id, "No treatment and control arms chosen")
                continue
            treatment, control = (
                values.get((study_id, item["id"], pair.treatment_arm_id)),
                values.get((study_id, item["id"], pair.control_arm_id)),
            )
            if data_type == "binary":
                numbers = [
                    _number(treatment, "events"),
                    _number(treatment, "total"),
                    _number(control, "events"),
                    _number(control, "total"),
                ]
                names: tuple[str, ...] = ("ai", "n1i", "ci", "n2i")
            else:
                numbers = [_number(treatment, k) for k in ("mean", "sd", "n")] + [
                    _number(control, k) for k in ("mean", "sd", "n")
                ]
                names = ("m1i", "sd1i", "n1i", "m2i", "sd2i", "n2i")
            if any(number is None for number in numbers):
                exclude(
                    study_id,
                    f"{item['name']}: an arm is missing "
                    f"{'events or totals' if data_type == 'binary' else 'a mean, SD, or n'}",
                )
                continue
            rows.append(
                {
                    **base(study_id),
                    "effect": item["name"],
                    "treatment_arm": arm_labels.get(pair.treatment_arm_id, ""),
                    "control_arm": arm_labels.get(pair.control_arm_id, ""),
                    **dict(zip(names, numbers, strict=True)),
                }
            )
        return rows, data_type

    extra_files: dict[str, bytes] = {}
    data_type = ""
    if analysis_type in ("pairwise", "bayesian"):
        rows, data_type = effect_rows(field_of(spec.field_id))
        if spec.model in ("mantel_haenszel", "peto", "glmm") and data_type != "binary":
            raise DatasetError("Mantel-Haenszel, Peto, and GLMM models need arm-level event counts")
        if spec.model == "peto" and spec.measure != "OR":
            raise DatasetError("The Peto method estimates odds ratios")
    elif analysis_type == "rve":
        if len(spec.field_ids) < 1:
            raise DatasetError("Choose the fields that contribute effects")
        rows = []
        types = set()
        for field_id in spec.field_ids:
            field_rows, field_type = effect_rows(field_of(field_id))
            rows += field_rows
            types.add(field_type)
        if len(types) > 1:
            raise DatasetError("Every field in a robust variance analysis must hold the same kind of data")
        data_type = types.pop()
    elif analysis_type == "nma":
        item = field_of(spec.field_id)
        data_type = DATA_TYPES.get(item["field_type"], "")
        if data_type not in ("binary", "continuous") or not item["per_arm"]:
            raise DatasetError("Network meta-analysis needs an arm-level dichotomous or continuous field")
        if spec.measure not in MEASURES_BY_DATA[data_type]:
            raise DatasetError(f"{spec.measure} can't be computed from {item['name']}")
        rows = []
        for study_id, study in studies.items():
            arm_rows = []
            for arm in study["arms"]:
                value = values.get((study_id, item["id"], arm["arm_id"]))
                treatment = spec.treatments.get(str(arm["arm_id"]), "").strip() or arm["label"]
                if data_type == "binary":
                    numbers = {"events": _number(value, "events"), "n": _number(value, "total")}
                else:
                    numbers = {"mean": _number(value, "mean"), "sd": _number(value, "sd"), "n": _number(value, "n")}
                if all(number is not None for number in numbers.values()):
                    arm_rows.append({**base(study_id), "treatment": treatment, **numbers})
            if len(arm_rows) < 2:
                exclude(study_id, "Fewer than two arms with complete data")
                continue
            rows += arm_rows
    elif analysis_type == "dta":
        item = field_of(spec.field_id)
        if item["field_type"] != "dta_2x2":
            raise DatasetError("Diagnostic accuracy analyses need a 2x2 table field")
        rows = []
        for study_id in studies:
            value = values.get((study_id, item["id"], None))
            cells = {key.upper(): _number(value, key) for key in ("tp", "fp", "fn", "tn")}
            if any(cell is None for cell in cells.values()):
                exclude(study_id, "Incomplete 2x2 table")
                continue
            rows.append({**base(study_id), **cells})
        data_type = "dta"
    elif analysis_type == "swim":
        rows = []
        for entry in spec.swim:
            if entry.study_id not in studies:
                raise DatasetError(f"Study {entry.study_id} isn't included in the review")
            rows.append(
                {
                    **base(entry.study_id),
                    "direction": entry.direction,
                    "outcome_domain": entry.outcome_domain,
                    "note": entry.note,
                }
            )
        data_type = "swim"
    elif analysis_type == "ipd":
        rows, extra_files = _ipd_rows(db, project_id, studies, spec)
        data_type = "ipd"
    else:
        raise DatasetError(f"Unknown analysis type: {analysis_type}")

    r_spec = {
        "measure": spec.measure,
        "data_type": data_type,
        "ratio": spec.measure in RATIO_MEASURES,
        "model": spec.model,
        "tau_method": spec.tau_method,
        "hksj": spec.hksj,
        "prediction_interval": spec.prediction_interval,
        "level": spec.level,
        "zero_correction": spec.zero_correction,
        "subgroup": {"label": spec.subgroup.label, "prespecified": spec.subgroup.prespecified}
        if spec.subgroup
        else None,
        "moderators": [
            {"column": f"mod_{i}", "label": m.label, "type": m.type} for i, m in enumerate(spec.moderators, start=1)
        ],
        "sensitivity": spec.sensitivity.model_dump(),
        "publication_bias": spec.publication_bias.model_dump(),
        "reference_treatment": spec.reference_treatment,
        "small_values": spec.small_values,
        "tau_prior_scale": spec.tau_prior_scale,
        "mu_prior_sd": spec.mu_prior_sd,
        "outcome_type": spec.ipd.outcome_type,
        "adjust_for": spec.ipd.adjust_for,
        "plot_formats": spec.plot_formats,
        "seed": spec.seed,
    }
    return BuiltDataset(rows, excluded, r_spec, source, snapshot_id, extra_files)


def _ipd_rows(
    db: Session, project_id: int, studies: dict[int, dict[str, Any]], spec: AnalysisSpec
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    """Participant rows combined from each study's mapped IPD file, as ipd.csv; the returned rows only count them."""
    datasets = db.scalars(
        select(models.IPDDataset).where(
            models.IPDDataset.project_id == project_id, models.IPDDataset.study_id.in_(list(studies))
        )
    ).all()
    if not datasets:
        raise DatasetError("Upload individual participant data for at least two included studies")
    storage = document_storage()
    columns = ["study", "treatment", "outcome", *spec.ipd.adjust_for]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    summary = []
    for dataset in datasets:
        mapping = dataset.mapping or {}
        needed = ["treatment", "outcome", *spec.ipd.adjust_for]
        missing = [name for name in needed if not mapping.get(name)]
        if missing:
            raise DatasetError(f"Map these variables for {studies[dataset.study_id]['label']}: {', '.join(missing)}")
        text = crypto.decrypt_bytes(storage.read(dataset.storage_key), ipd_context(dataset)).decode("utf-8-sig")
        count = 0
        for raw in csv.DictReader(io.StringIO(text)):
            row = {"study": studies[dataset.study_id]["label"]}
            for name in needed:
                row[name] = (raw.get(mapping[name]) or "").strip()
            if row["treatment"] == "" or row["outcome"] == "":
                continue
            writer.writerow(row)
            count += 1
        summary.append(
            {"study_id": dataset.study_id, "study": studies[dataset.study_id]["label"], "participants": count}
        )
    return summary, {"ipd.csv": buffer.getvalue().encode()}


def ipd_context(dataset: models.IPDDataset) -> str:
    return f"ipd:{dataset.project_id}:{dataset.study_id}"


def pooling_assessment(analysis_type: str, built: BuiltDataset, designs: dict[int, str]) -> dict[str, Any]:
    """Whether pooling looks appropriate, with reasons; SWiM is recommended when it doesn't."""
    reasons: list[str] = []
    studies = built.studies
    if analysis_type == "swim":
        return {
            "recommendation": "swim",
            "studies": studies,
            "reasons": ["Synthesis without meta-analysis was chosen."],
        }
    if studies < 2:
        return {
            "recommendation": "not_possible",
            "studies": studies,
            "reasons": [
                "Fewer than two studies have usable data, so results can't be pooled; describe them narratively or "
                "with SWiM."
            ],
        }
    recommendation = "meta_analysis"
    measure_mismatch = [e for e in built.excluded if "is reported as" in e["reason"]]
    if measure_mismatch:
        reasons.append(
            f"{len(measure_mismatch)} studies report a different effect measure; convert them where valid or use SWiM."
        )
    if len(built.excluded) >= studies:
        recommendation = "swim"
        reasons.append(
            "At least as many studies lack usable data as have it: pooling would misrepresent the evidence; consider "
            "SWiM."
        )
    kinds = {
        ("randomized" if "random" in design.casefold() else "non-randomized")
        for sid, design in designs.items()
        if design and sid in {r["study_id"] for r in built.rows}
    }
    if len(kinds) > 1:
        reasons.append("Randomized and non-randomized studies are mixed: analyse them separately or as subgroups.")
    if (
        analysis_type == "pairwise"
        and built.r_spec.get("model") == "random"
        and studies < 5
        and not built.r_spec.get("hksj")
    ):
        reasons.append(
            "With fewer than five studies, use the Hartung-Knapp-Sidik-Jonkman adjustment for random effects."
        )
    return {"recommendation": recommendation, "studies": studies, "reasons": reasons}
