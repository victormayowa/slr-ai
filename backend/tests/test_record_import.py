"""Parsing exported search results in every supported format."""

import pytest

from services.record_import import ImportFormatError, detect_format, parse_records

RIS = """TY  - JOUR
TI  - Low-dose aspirin for primary prevention
AU  - Smith, John
AU  - Doe, Jane
PY  - 2019///
T2  - The Lancet
DO  - https://doi.org/10.1016/S0140-6736(19)12345-6
AB  - Adults were randomized
      to aspirin or placebo.
AN  - 628123456
UR  - https://example.org/a
ER  -

TY  - JOUR
AU  - Nobody, N
ER  -
"""

MEDLINE = """PMID- 31234567
TI  - Aspirin in older adults: a
      randomized trial.
AB  - We randomized 19,114 adults.
FAU - McNeil, John J
FAU - Nelson, Mark R
DP  - 2018 Oct 18
JT  - The New England journal of medicine
LID - 10.1056/NEJMoa1800722 [doi]
PMC - PMC6426126

PMID- 30000001
TI  - Second record.
DP  - 2020
"""

BIBTEX = """@comment{Exported from a reference manager}
@article{smith2019,
  title = {Low-dose {Aspirin} for Primary Prevention},
  author = {Smith, John and Doe, Jane},
  journal = "The Lancet",
  year = 2019,
  doi = {10.1016/x},
  abstract = {Adults were randomized.}
}
"""

ENDNOTE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<xml><records><record>
<titles>
  <title><style face="normal">Aspirin trial</style></title>
  <secondary-title><style>BMJ</style></secondary-title>
</titles>
<contributors><authors>
  <author><style>Smith, J.</style></author>
  <author><style>Doe, J.</style></author>
</authors></contributors>
<dates><year><style>2021</style></year></dates>
<electronic-resource-num><style>10.1136/bmj.x</style></electronic-resource-num>
<abstract><style>Abstract text</style></abstract>
<accession-num><style>34000000</style></accession-num>
</record></records></xml>
"""

WOS = """FN Clarivate Analytics Web of Science
VR 1.0
PT J
AU Smith, J
   Doe, J
TI Aspirin and
   stroke prevention
SO STROKE
PY 2017
DI 10.1161/STROKEAHA.117.1
AB Background text.
UT WOS:000123
PM 28000000
ER

EF
"""

SCOPUS_CSV = """﻿Authors,Title,Year,Source title,DOI,Abstract,PubMed ID
"Smith J.; Doe J.",Aspirin for stroke,2020,Stroke,10.1/s,"An abstract, with a comma",32000000
"Nobody N.",,2021,Stroke,,,
"""


def test_ris_records_join_continuation_lines_and_skip_untitled_entries():
    parsed = parse_records("embase.ris", RIS.encode())

    assert (parsed.format, parsed.skipped) == ("ris", 1)
    assert parsed.records == [
        {
            "title": "Low-dose aspirin for primary prevention",
            "authors": "Smith, John; Doe, Jane",
            "year": "2019",
            "venue": "The Lancet",
            "doi": "10.1016/S0140-6736(19)12345-6",
            "abstract": "Adults were randomized to aspirin or placebo.",
            "url": "https://example.org/a",
            "identifiers": {"accession": "628123456"},
        }
    ]


def test_medline_records_keep_pmid_pmcid_and_doi():
    parsed = parse_records("pubmed-export.txt", MEDLINE.encode())

    assert parsed.format == "medline"
    first, second = parsed.records
    assert first["title"] == "Aspirin in older adults: a randomized trial."
    assert first["authors"] == "McNeil, John J; Nelson, Mark R"
    assert (first["year"], first["doi"]) == ("2018", "10.1056/NEJMoa1800722")
    assert first["identifiers"] == {"pmid": "31234567", "pmcid": "PMC6426126"}
    assert second["identifiers"] == {"pmid": "30000001"}


def test_bibtex_entries_strip_braces_and_split_authors():
    [record] = parse_records("acm.bib", BIBTEX.encode()).records

    assert record["title"] == "Low-dose Aspirin for Primary Prevention"
    assert record["authors"] == "Smith, John; Doe, Jane"
    assert (record["venue"], record["year"], record["doi"]) == ("The Lancet", "2019", "10.1016/x")


def test_endnote_xml_records_are_read():
    [record] = parse_records("library.xml", ENDNOTE_XML.encode()).records

    assert (record["title"], record["authors"], record["venue"]) == ("Aspirin trial", "Smith, J.; Doe, J.", "BMJ")
    assert record["identifiers"] == {"accession": "34000000"}


def test_web_of_science_records_are_read():
    [record] = parse_records("savedrecs.txt", WOS.encode()).records

    assert record["title"] == "Aspirin and stroke prevention"
    assert record["authors"] == "Smith, J; Doe, J"
    assert record["identifiers"] == {"wos": "WOS:000123", "pmid": "28000000"}


def test_csv_exports_map_common_column_names():
    parsed = parse_records("scopus.csv", SCOPUS_CSV.encode())

    assert (parsed.format, parsed.skipped) == ("csv", 1)
    assert parsed.records[0]["venue"] == "Stroke"
    assert parsed.records[0]["abstract"] == "An abstract, with a comma"
    assert parsed.records[0]["identifiers"] == {"pmid": "32000000"}


def test_xml_entity_attacks_are_refused():
    attack = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><xml><records/></xml>'

    with pytest.raises(ImportFormatError):
        parse_records("library.xml", attack.encode())


def test_unrecognized_files_are_refused():
    with pytest.raises(ImportFormatError):
        detect_format("notes.docx", "just some text")
