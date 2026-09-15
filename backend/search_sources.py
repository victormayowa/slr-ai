"""Where searches can run: databases with a search connector, and databases that are searched on their own platform
and imported as export files. A strategy written for one database is never silently run on another."""

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Literal

from services.literature_sources import (
    search_clinical_trials,
    search_crossref,
    search_europepmc,
    search_semantic_scholar,
)
from services.openalex import openalex_search
from services.pubmed import pubmed_search
from services.record_import import SUPPORTED_FORMATS

MAX_RESULTS = 2000


@dataclass(frozen=True)
class Connector:
    key: str
    label: str
    interface: str
    # PRISMA 2020 separates records from databases and from trial registers.
    kind: Literal["database", "register"]
    search: Callable[[str, int], tuple[list[dict], int]] = field(repr=False, compare=False)
    aliases: tuple[str, ...]
    syntax_note: str


@dataclass(frozen=True)
class ImportOnlySource:
    label: str
    interface: str
    export_hint: str
    aliases: tuple[str, ...]


CONNECTORS: dict[str, Connector] = {
    connector.key: connector
    for connector in (
        Connector(
            "pubmed",
            "PubMed",
            "PubMed (NCBI E-utilities API)",
            "database",
            pubmed_search,
            ("pubmed", "medline", "medline pubmed", "pubmed medline"),
            "PubMed syntax, including field tags such as [tiab], [mh], and [pt].",
        ),
        Connector(
            "europepmc",
            "Europe PMC",
            "Europe PMC REST API",
            "database",
            search_europepmc,
            ("europe pmc", "europepmc"),
            "Europe PMC syntax, such as TITLE:, ABSTRACT:, and AND, OR, NOT.",
        ),
        Connector(
            "openalex",
            "OpenAlex",
            "OpenAlex API",
            "database",
            openalex_search,
            ("openalex",),
            "Keyword search with AND, OR, NOT, and quoted phrases; no field tags.",
        ),
        Connector(
            "crossref",
            "Crossref",
            "Crossref REST API",
            "database",
            search_crossref,
            ("crossref",),
            "Relevance-ranked keyword search; Boolean operators are ignored.",
        ),
        Connector(
            "semantic_scholar",
            "Semantic Scholar",
            "Semantic Scholar Academic Graph API",
            "database",
            search_semantic_scholar,
            ("semantic scholar",),
            "Keyword search; Boolean operators are ignored, and at most 1,000 results are returned.",
        ),
        Connector(
            "clinicaltrials_gov",
            "ClinicalTrials.gov",
            "ClinicalTrials.gov API v2",
            "register",
            search_clinical_trials,
            ("clinicaltrials gov", "clinicaltrials", "clinical trials gov", "ctgov"),
            "ClinicalTrials.gov search expressions, such as AREA[Condition]aspirin.",
        ),
    )
}

IMPORT_ONLY_SOURCES = (
    ImportOnlySource("Embase", "Embase.com or Ovid", "RIS", ("embase", "embase com", "ovid embase", "embase ovid")),
    ImportOnlySource("MEDLINE (Ovid)", "Ovid", "RIS", ("medline ovid", "ovid medline")),
    ImportOnlySource("Scopus", "Scopus", "RIS or CSV", ("scopus",)),
    ImportOnlySource(
        "Web of Science", "Web of Science Core Collection", "plain text or RIS", ("web of science", "wos")
    ),
    ImportOnlySource("CINAHL", "EBSCOhost", "RIS", ("cinahl", "cinahl plus", "cinahl complete")),
    ImportOnlySource("APA PsycInfo", "EBSCOhost or Ovid", "RIS", ("psycinfo", "apa psycinfo")),
    ImportOnlySource(
        "Cochrane CENTRAL",
        "the Cochrane Library",
        "RIS",
        ("cochrane", "cochrane central", "central", "cochrane library"),
    ),
    ImportOnlySource("IEEE Xplore", "IEEE Xplore", "RIS or CSV", ("ieee", "ieee xplore")),
    ImportOnlySource("ACM Digital Library", "the ACM Digital Library", "BibTeX", ("acm", "acm digital library")),
    ImportOnlySource("WHO ICTRP", "the WHO ICTRP search portal", "CSV or XML", ("who ictrp", "ictrp")),
    ImportOnlySource("LILACS", "the Virtual Health Library", "RIS", ("lilacs",)),
    ImportOnlySource("Google Scholar", "Google Scholar (via Publish or Perish)", "RIS or CSV", ("google scholar",)),
)


def normalize_database_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def connector_for(database: str) -> Connector | None:
    name = normalize_database_name(database)
    return next((connector for connector in CONNECTORS.values() if name in connector.aliases), None)


def import_only_source(database: str) -> ImportOnlySource | None:
    name = normalize_database_name(database)
    return next((source for source in IMPORT_ONLY_SOURCES if name in source.aliases), None)


def catalog() -> dict:
    return {
        "connectors": [
            {key: value for key, value in asdict(connector).items() if key != "search"}
            for connector in CONNECTORS.values()
        ],
        "import_only": [asdict(source) for source in IMPORT_ONLY_SOURCES],
        "import_formats": SUPPORTED_FORMATS,
        "max_results": MAX_RESULTS,
    }
