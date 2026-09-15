"""Search connectors: paging, record fields, rate limits, and matching databases to connectors."""

import pytest
import requests

from search_sources import connector_for, import_only_source
from services import literature_sources
from services.errors import SearchError


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def serve(monkeypatch, *pages):
    remaining = list(pages)
    calls: list[dict] = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(params or {})
        return remaining.pop(0)

    monkeypatch.setattr(literature_sources.requests, "get", get)
    return calls


def test_europe_pmc_pages_with_its_cursor(monkeypatch):
    item = {
        "id": "1",
        "source": "MED",
        "pmid": "1",
        "pmcid": "PMC9",
        "title": "<i>Aspirin</i> trial",
        "pubYear": "2020",
    }
    calls = serve(
        monkeypatch,
        FakeResponse(
            {
                "hitCount": 3,
                "nextCursorMark": "AoE1",
                "resultList": {"result": [item, {**item, "id": "2", "pmid": "2"}]},
            }
        ),
        FakeResponse(
            {"hitCount": 3, "nextCursorMark": "AoE1", "resultList": {"result": [{**item, "id": "3", "pmid": "3"}]}}
        ),
    )

    records, total = literature_sources.search_europepmc("aspirin", limit=10)

    assert (len(records), total) == (3, 3)
    assert records[0]["title"] == "Aspirin trial"
    assert records[0]["identifiers"] == {"pmid": "1", "pmcid": "PMC9", "europepmc": "MED:1"}
    assert calls[1]["cursorMark"] == "AoE1"


def test_crossref_records_strip_jats_markup(monkeypatch):
    item = {
        "DOI": "10.1/abc",
        "title": ["Aspirin <sub>81</sub> mg"],
        "author": [{"family": "Smith", "given": "J"}],
        "issued": {"date-parts": [[2019, 5]]},
        "container-title": ["Stroke"],
        "abstract": "<jats:p>Adults were randomized.</jats:p>",
    }
    serve(monkeypatch, FakeResponse({"message": {"total-results": 40, "items": [item], "next-cursor": "c2"}}))

    records, total = literature_sources.search_crossref("aspirin", limit=1)

    assert total == 40
    assert records == [
        {
            "id": "10.1/abc",
            "title": "Aspirin 81 mg",
            "authors": "Smith J",
            "year": "2019",
            "venue": "Stroke",
            "doi": "10.1/abc",
            "abstract": "Adults were randomized.",
            "identifiers": {},
            "url": "",
        }
    ]


def test_clinicaltrials_gov_studies_become_register_records(monkeypatch):
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT01234567", "briefTitle": "Aspirin study"},
            "descriptionModule": {"briefSummary": "A trial of aspirin."},
            "statusModule": {"startDateStruct": {"date": "2015-03"}},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Monash University"}},
        }
    }
    serve(monkeypatch, FakeResponse({"totalCount": 12, "studies": [study]}))

    [record], total = literature_sources.search_clinical_trials("aspirin", limit=5)

    assert total == 12
    assert (record["title"], record["year"], record["authors"]) == ("Aspirin study", "2015", "Monash University")
    assert record["identifiers"] == {"nct": "NCT01234567"}
    assert record["url"] == "https://clinicaltrials.gov/study/NCT01234567"


def test_semantic_scholar_rate_limits_explain_how_to_raise_them(monkeypatch):
    serve(monkeypatch, FakeResponse({}, status_code=429))

    with pytest.raises(SearchError) as error:
        literature_sources.search_semantic_scholar("aspirin", limit=10)

    assert "SEMANTIC_SCHOLAR_API_KEY" in str(error.value)


def test_database_names_match_connectors_or_import_instructions():
    assert connector_for("MEDLINE (PubMed)").key == "pubmed"
    assert connector_for("ClinicalTrials.gov").kind == "register"
    assert connector_for("Embase") is None
    assert import_only_source("Embase").export_hint == "RIS"
    assert import_only_source("Cochrane Library").label == "Cochrane CENTRAL"
    assert connector_for("Some local database") is None and import_only_source("Some local database") is None
