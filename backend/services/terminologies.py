"""Terminology lookups for entity linking: MeSH (NLM E-utilities), RxNorm and ATC (NLM RxNav), and ICD-11 (WHO ICD API).

MeSH, RxNorm, and ATC are free to query. ICD-11 needs API credentials from https://icd.who.int/icdapi, set as
ICD11_CLIENT_ID and ICD11_CLIENT_SECRET. SNOMED CT and UMLS need licences and aren't queried.
"""

import logging
import os
import re
import time

import requests

from services.errors import SearchError
from services.mesh import lookup_mesh

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
RXNAV = "https://rxnav.nlm.nih.gov/REST"
ICD_TOKEN_URL = "https://icdaccessmanagement.who.int/connect/token"
ICD_SEARCH_URL = "https://id.who.int/icd/release/11/{release}/mms/search"
ONTOLOGY_LABELS = {"mesh": "MeSH", "rxnorm": "RxNorm", "atc": "ATC", "icd11": "ICD-11"}

_icd_token: tuple[str, float] | None = None


class TerminologyError(Exception):
    """A lookup failed. The message is safe to show users."""


def _get_json(label: str, url: str, params: dict[str, str], headers: dict[str, str] | None = None) -> dict:
    try:
        response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("%s lookup failed", label, exc_info=True)
        raise TerminologyError(f"The {label} lookup failed. Try again shortly.") from exc
    return data if isinstance(data, dict) else {}


def mesh_candidates(term: str, limit: int = 5) -> list[dict[str, str]]:
    try:
        headings = lookup_mesh(term, limit)
    except SearchError as exc:
        raise TerminologyError(str(exc)) from exc
    return [{"ontology": "mesh", "code": h["ui"], "label": h["heading"]} for h in headings if h["ui"]]


def rxnorm_candidates(term: str, limit: int = 5) -> list[dict[str, str]]:
    data = _get_json("RxNorm", f"{RXNAV}/approximateTerm.json", {"term": term, "maxEntries": str(limit * 3)})
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in (data.get("approximateGroup") or {}).get("candidate") or []:
        rxcui = str(item.get("rxcui") or "")
        if not rxcui or rxcui in seen:
            continue
        seen.add(rxcui)
        name = item.get("name")
        if not name:
            properties = _get_json("RxNorm", f"{RXNAV}/rxcui/{rxcui}/properties.json", {})
            name = (properties.get("properties") or {}).get("name") or ""
        if name:
            candidates.append({"ontology": "rxnorm", "code": rxcui, "label": str(name)})
        if len(candidates) >= limit:
            break
    return candidates


def atc_classes(rxcui: str) -> list[dict[str, str]]:
    data = _get_json("ATC", f"{RXNAV}/rxclass/class/byRxcui.json", {"rxcui": rxcui, "relaSource": "ATC"})
    classes: dict[str, str] = {}
    for info in (data.get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo") or []:
        concept = info.get("rxclassMinConceptItem") or {}
        if concept.get("classId"):
            classes[str(concept["classId"])] = str(concept.get("className") or "")
    return [{"ontology": "atc", "code": code, "label": label} for code, label in classes.items()]


def icd11_configured() -> bool:
    return bool(os.getenv("ICD11_CLIENT_ID") and os.getenv("ICD11_CLIENT_SECRET"))


def _icd_access_token() -> str:
    global _icd_token
    if _icd_token and _icd_token[1] > time.monotonic() + 60:
        return _icd_token[0]
    try:
        response = requests.post(
            ICD_TOKEN_URL,
            data={
                "client_id": os.getenv("ICD11_CLIENT_ID", ""),
                "client_secret": os.getenv("ICD11_CLIENT_SECRET", ""),
                "scope": "icdapi_access",
                "grant_type": "client_credentials",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("ICD-11 authentication failed", exc_info=True)
        raise TerminologyError("Signing in to the ICD-11 API failed. Check the server's ICD-11 credentials.") from exc
    _icd_token = (str(data["access_token"]), time.monotonic() + float(data.get("expires_in", 3600)))
    return _icd_token[0]


def icd11_candidates(term: str, limit: int = 5) -> list[dict[str, str]]:
    if not icd11_configured():
        return []
    release = os.getenv("ICD11_RELEASE", "2025-01")
    data = _get_json(
        "ICD-11",
        ICD_SEARCH_URL.format(release=release),
        {"q": term, "flatResults": "true", "useFlexisearch": "true"},
        {
            "Authorization": f"Bearer {_icd_access_token()}",
            "API-Version": "v2",
            "Accept-Language": "en",
            "Accept": "application/json",
        },
    )
    results = []
    for entity in data.get("destinationEntities") or []:
        code = str(entity.get("theCode") or "")
        if code:
            label = re.sub(r"<[^>]+>", "", str(entity.get("title") or ""))
            results.append({"ontology": "icd11", "code": code, "label": label})
        if len(results) >= limit:
            break
    return results


def lookup(ontology: str, term: str, limit: int = 5) -> list[dict[str, str]]:
    if ontology == "mesh":
        return mesh_candidates(term, limit)
    if ontology == "rxnorm":
        return rxnorm_candidates(term, limit)
    if ontology == "atc":
        drugs = rxnorm_candidates(term, 1)
        return atc_classes(drugs[0]["code"])[:limit] if drugs else []
    if ontology == "icd11":
        return icd11_candidates(term, limit)
    raise TerminologyError(f"Unknown terminology: {ontology}")
