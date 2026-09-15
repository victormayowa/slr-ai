"""Unit normalization for extracted values: a curated table of units common in clinical studies.

Converting between mass and molar concentrations needs the analyte's molar mass, so those conversions are only offered
for the analytes listed in ANALYTES. Anything not listed is refused rather than guessed.
"""

from dataclasses import dataclass


class UnitError(ValueError):
    """A unit isn't known or can't be converted. The message is safe to show users."""


# Each group: units with their factor to the group's base unit.
_GROUPS: dict[str, dict[str, float]] = {
    "mass": {"kg": 1.0, "g": 1e-3, "mg": 1e-6, "mcg": 1e-9, "ng": 1e-12, "lb": 0.45359237, "oz": 0.028349523125},
    "length": {"m": 1.0, "cm": 0.01, "mm": 0.001, "km": 1000.0, "in": 0.0254, "ft": 0.3048},
    "time": {
        "day": 1.0,
        "s": 1 / 86400,
        "min": 1 / 1440,
        "h": 1 / 24,
        "week": 7.0,
        "month": 30.4375,
        "year": 365.25,
    },
    "volume": {"l": 1.0, "dl": 0.1, "ml": 1e-3, "mcl": 1e-6},
    "pressure": {"mmhg": 1.0, "kpa": 7.500616827, "cmh2o": 0.735559},
    "mass_concentration": {"mg/dl": 1.0, "g/l": 100.0, "mg/l": 0.1, "g/dl": 1000.0, "mcg/ml": 0.1, "ng/ml": 1e-4},
    "molar_concentration": {"mmol/l": 1.0, "mcmol/l": 1e-3, "nmol/l": 1e-6, "mol/l": 1000.0},
    "energy": {"kcal": 1.0, "kj": 1 / 4.184},
    "rate": {"/min": 1.0, "bpm": 1.0},
}

_ALIASES = {
    "µg": "mcg",
    "μg": "mcg",
    "ug": "mcg",
    "microgram": "mcg",
    "micrograms": "mcg",
    "gram": "g",
    "grams": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "milligram": "mg",
    "milligrams": "mg",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "sec": "s",
    "second": "s",
    "seconds": "s",
    "mins": "min",
    "minute": "min",
    "minutes": "min",
    "hr": "h",
    "hrs": "h",
    "hour": "h",
    "hours": "h",
    "d": "day",
    "days": "day",
    "wk": "week",
    "wks": "week",
    "weeks": "week",
    "mo": "month",
    "months": "month",
    "yr": "year",
    "yrs": "year",
    "years": "year",
    "litre": "l",
    "liter": "l",
    "litres": "l",
    "liters": "l",
    "µl": "mcl",
    "μl": "mcl",
    "mm hg": "mmhg",
    "µmol/l": "mcmol/l",
    "μmol/l": "mcmol/l",
    "umol/l": "mcmol/l",
    "µg/ml": "mcg/ml",
    "μg/ml": "mcg/ml",
    "ug/ml": "mcg/ml",
    "beats/min": "bpm",
    "%": "percent",
    "percent": "percent",
}

# Milligrams per decilitre equal to 1 mmol/L, from each analyte's molar mass.
ANALYTES: dict[str, tuple[str, float]] = {
    "glucose": ("Glucose", 18.016),
    "total_cholesterol": ("Total cholesterol", 38.67),
    "ldl_cholesterol": ("LDL cholesterol", 38.67),
    "hdl_cholesterol": ("HDL cholesterol", 38.67),
    "triglycerides": ("Triglycerides", 88.57),
    "creatinine": ("Creatinine", 11.312),
    "urea_nitrogen": ("Urea nitrogen (BUN)", 2.801),
    "uric_acid": ("Uric acid", 16.81),
    "calcium": ("Calcium", 4.008),
}


def normalize_unit(unit: str) -> str:
    cleaned = " ".join(unit.strip().lower().split())
    cleaned = _ALIASES.get(cleaned, cleaned)
    return cleaned.replace(" ", "")


def _group(unit: str) -> str | None:
    return next((name for name, units in _GROUPS.items() if unit in units), None)


@dataclass
class UnitConversion:
    value: float
    from_unit: str
    to_unit: str
    method: str


def convert(value: float, from_unit: str, to_unit: str, analyte: str | None = None) -> UnitConversion:
    source, target = normalize_unit(from_unit), normalize_unit(to_unit)
    if source == target:
        return UnitConversion(value, from_unit, to_unit, "same unit")
    temperatures = {"c", "°c", "f", "°f", "k"}
    if source in temperatures and target in temperatures:
        celsius = {"c": value, "°c": value, "f": (value - 32) * 5 / 9, "°f": (value - 32) * 5 / 9, "k": value - 273.15}
        result = {"c": celsius[source], "°c": celsius[source], "f": celsius[source] * 9 / 5 + 32}
        result["°f"], result["k"] = result["f"], celsius[source] + 273.15
        return UnitConversion(result[target], from_unit, to_unit, "temperature scale")
    if {source, target} == {"percent", "mmol/mol"} and analyte == "hba1c":
        converted = (value - 2.15) * 10.929 if source == "percent" else value / 10.929 + 2.15
        return UnitConversion(converted, from_unit, to_unit, "HbA1c NGSP % ↔ IFCC mmol/mol")

    source_group, target_group = _group(source), _group(target)
    if source_group is None or target_group is None:
        unknown = from_unit if source_group is None else to_unit
        raise UnitError(f"Unknown unit: {unknown}")
    if source_group == target_group:
        factor = _GROUPS[source_group][source] / _GROUPS[target_group][target]
        return UnitConversion(value * factor, from_unit, to_unit, f"{source_group.replace('_', ' ')} conversion")
    if {source_group, target_group} == {"mass_concentration", "molar_concentration"}:
        if analyte not in ANALYTES:
            raise UnitError("Converting between mass and molar concentration needs a listed analyte")
        label, mg_dl_per_mmol_l = ANALYTES[analyte]
        mg_dl = value * _GROUPS["mass_concentration"][source] if source_group == "mass_concentration" else None
        if mg_dl is None:
            mmol_l = value * _GROUPS["molar_concentration"][source]
            converted = mmol_l * mg_dl_per_mmol_l / _GROUPS["mass_concentration"][target]
        else:
            converted = mg_dl / mg_dl_per_mmol_l / _GROUPS["molar_concentration"][target]
        return UnitConversion(converted, from_unit, to_unit, f"{label}: 1 mmol/L = {mg_dl_per_mmol_l} mg/dL")
    raise UnitError(f"{from_unit} can't be converted to {to_unit}")


def catalog() -> dict:
    return {
        "groups": {name: sorted(units) for name, units in _GROUPS.items()},
        "analytes": [{"key": key, "label": label} for key, (label, _) in ANALYTES.items()]
        + [{"key": "hba1c", "label": "HbA1c (% ↔ mmol/mol)"}],
    }
