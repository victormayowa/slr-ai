"""Parsing PubMed search strings, checking syntax, and translating them for other databases."""

import pytest

from search_query import (
    Group,
    Near,
    QuerySyntaxError,
    Term,
    parse_pubmed,
    syntax_for_database,
    translate_pubmed,
    validate,
)

STRATEGY = '("aspirin"[mh] OR aspirin*[tiab]) AND "primary prevention"[tiab] NOT review[pt]'


def test_pubmed_strings_parse_left_to_right_like_pubmed():
    tree = parse_pubmed(STRATEGY)

    assert tree == Group(
        "NOT",
        [
            Group(
                "AND",
                [
                    Group("OR", [Term("aspirin", "heading", True), Term("aspirin*", "title_abstract")]),
                    Term("primary prevention", "title_abstract", True),
                ],
            ),
            Term("review", "publication_type"),
        ],
    )
    assert parse_pubmed("a OR b AND c") == Group("AND", [Group("OR", [Term("a"), Term("b")]), Term("c")])
    assert parse_pubmed('"aspirin bleeding"[tiab:~3]') == Near(["aspirin", "bleeding"], 3, "title_abstract")


@pytest.mark.parametrize(
    "target,expected",
    [
        ("pubmed", '(("aspirin"[mh] OR aspirin*[tiab]) AND "primary prevention"[tiab]) NOT review[pt]'),
        ("ovid_medline", '((exp aspirin/ OR aspirin*.ti,ab.) AND "primary prevention".ti,ab.) NOT review.pt.'),
        ("embase_com", "(('aspirin'/exp OR aspirin*:ti,ab) AND 'primary prevention':ti,ab) NOT review"),
        (
            "scopus",
            '((INDEXTERMS("aspirin") OR TITLE-ABS-KEY(aspirin*)) AND TITLE-ABS-KEY("primary'
            ' prevention")) AND NOT TITLE-ABS-KEY(review)',
        ),
        (
            "ebsco",
            '(((MH "aspirin+") OR (TI aspirin* OR AB aspirin*)) AND (TI "primary prevention" OR AB'
            ' "primary prevention")) NOT PT review',
        ),
        ("cochrane", '(([mh "aspirin"] OR aspirin*:ti,ab,kw) AND "primary prevention":ti,ab,kw) NOT review'),
    ],
)
def test_strategies_translate_with_explicit_parentheses(target, expected):
    query, _ = translate_pubmed(STRATEGY, target)

    assert query == expected


def test_translations_warn_about_what_does_not_carry_across():
    _, embase = translate_pubmed(STRATEGY, "embase_com")
    _, openalex = translate_pubmed(STRATEGY, "openalex")
    _, crossref = translate_pubmed(STRATEGY, "crossref")
    near, _ = translate_pubmed('"aspirin bleeding"[tiab:~3]', "ovid_medline")

    assert any("Emtree" in warning for warning in embase)
    assert any("no field tags" in warning for warning in openalex)
    assert crossref[-1] == "Crossref ranks results by relevance and ignores Boolean operators."
    assert near == "(aspirin adj3 bleeding).ti,ab."


@pytest.mark.parametrize(
    "query,message",
    [
        ("(aspirin OR statin", "A parenthesis isn't closed"),
        ("aspirin OR", "OR needs a term after it"),
        ('"aspirin', "A quotation mark isn't closed"),
        ("aspirin[xyz]", "Unknown PubMed field tag [xyz]"),
        ("()", "Empty parentheses"),
        ("aspirin)", "A closing parenthesis has no opening parenthesis"),
    ],
)
def test_pubmed_syntax_errors_are_explained(query, message):
    with pytest.raises(QuerySyntaxError) as error:
        parse_pubmed(query)

    assert str(error.value) == message


def test_validation_reports_errors_and_warnings_for_each_syntax():
    pubmed = validate("aspirin and asp* OR statin[tiab]", "pubmed")
    scopus = validate("TITLE-ABS-KEY(aspirin AND", "scopus")

    assert pubmed["valid"] is True
    assert pubmed["term_count"] == 4
    assert '"and" is searched as a word; PubMed operators must be in capitals.' in pubmed["warnings"]
    assert any("four characters" in warning for warning in pubmed["warnings"])
    assert scopus["valid"] is False
    assert "A parenthesis isn't closed" in scopus["errors"]


def test_database_names_map_to_syntaxes():
    assert syntax_for_database("MEDLINE") == "pubmed"
    assert syntax_for_database("Ovid Embase") == "ovid_embase"
    assert syntax_for_database("CINAHL") == "ebsco"
    assert syntax_for_database("Some other database") is None
