"""Claim verification, citations, AI draft assembly, checklist evaluation, publication helpers, and surveillance
parsing. No database, network, or R needed."""

import models
from ai_disclosure import disclosure_text
from citations import parse_authors, render, to_bibtex, to_ris
from claims import EvidenceItem, ReferenceState, check_content, split_sentences
from manuscript_checklists import CHECKLISTS, evaluate
from publication_state import first_int, word_count
from services.ai_manuscript import DraftBlock, DraftOutput, DraftSentence, assemble_draft, preservation_problems
from services.ai_publication import GuidelineOutput, RequirementOut, grounded_requirements, rule_split_comments
from surveillance import parse_feed, record_keys, sample_size

CATALOG = {
    "analysis:3": EvidenceItem(
        "analysis:3",
        "Aspirin versus placebo",
        [
            "Pooled exp_estimate: 0.5943",
            "Pooled exp_ci_lower: 0.3612",
            "Pooled exp_ci_upper: 0.9781",
            "Studies: 2",
            "summary I2: 12.3456",
        ],
    ),
    "prisma": EvidenceItem(
        "prisma", "PRISMA flow", ["Records screened: 1200", "Reports excluded, wrong population: 3"]
    ),
    "grade:4": EvidenceItem("grade:4", "GRADE", ["Moderate risk: difference -61 per 1000 (-96 to -4)"]),
}
REFERENCES = {5: ReferenceState(5), 6: ReferenceState(6, retracted=True)}


def statuses(text: str, acknowledged: set[str] | None = None) -> list[tuple[str, str | None]]:
    return [(c.status, c.confidence) for c in check_content(text, CATALOG, REFERENCES, acknowledged or set())]


# Claims


def test_sentences_keep_their_markers_and_skip_headings_embeds_and_abbreviations():
    content = (
        "## Results\n\nWe screened 1,200 records. [#prisma] Two trials (e.g. Smith et al. 2020) contributed [@5].\n\n"
        "[[table:sof]]\n\n- Inclusion criterion: adults [#prisma]"
    )

    assert [s.text for s in split_sentences(content)] == [
        "We screened 1,200 records [#prisma].",
        "Two trials (e.g. Smith et al. 2020) contributed [@5].",
        "Inclusion criterion: adults [#prisma]",
    ]


def test_numbers_must_match_the_linked_evidence_allowing_rounding_and_percentages():
    assert statuses("The pooled risk ratio was 0.59 (95% CI 0.36 to 0.98), with I² = 12%. [#analysis:3]") == [
        ("verified", "high")
    ]
    assert statuses("We screened 1,200 records. [#prisma]") == [("verified", "high")]
    assert statuses("About 6.1% fewer people had a heart attack. [#grade:4]") == [("verified", "high")]
    assert statuses("The pooled risk ratio was 0.61. [#analysis:3]") == [("number_mismatch", None)]


def test_unsupported_retracted_and_unknown_links_are_flagged():
    assert statuses("Aspirin is widely used.") == [("unsupported", None)]
    assert statuses("Aspirin is widely used [@5].") == [("verified", "low")]
    assert statuses("Aspirin is used by 40% of adults [@5].") == [("unverifiable_numbers", None)]
    assert statuses("Aspirin is widely used [@6].") == [("retracted_citation", None)]
    assert statuses("Aspirin is widely used [@9]. [#unknown:1]") == [("invalid_link", None)]


def test_acknowledgements_resolve_only_unsupported_claims():
    [unsupported] = check_content("Aspirin is widely used.", CATALOG, REFERENCES, set())
    [mismatch] = check_content("The pooled risk ratio was 0.61. [#analysis:3]", CATALOG, REFERENCES, set())

    assert statuses("Aspirin is widely used.", {unsupported.sentence.hash}) == [("unsupported", "acknowledged")]
    acknowledged = check_content(
        "The pooled risk ratio was 0.61. [#analysis:3]", CATALOG, REFERENCES, {mismatch.sentence.hash}
    )
    assert acknowledged[0].acknowledged is False


# Citations

CSL = {
    1: {
        "title": "Aspirin for prevention",
        "author": [{"family": "Smith", "given": "John A"}, {"family": "Doe", "given": "Jane"}],
        "container-title": "BMJ",
        "issued": {"date-parts": [[2020]]},
        "volume": "368",
        "issue": "2",
        "page": "l100",
        "DOI": "10.1136/bmj.l100",
    },
    2: {
        "title": "Statins",
        "author": [{"family": "Lee", "given": "K"}, {"family": "Park", "given": "S"}, {"family": "Kim", "given": "H"}],
        "container-title": "Lancet",
        "issued": {"date-parts": [[2019]]},
    },
}


def test_author_strings_are_parsed():
    assert parse_authors("Smith JA, Doe J, et al.") == (
        [{"family": "Smith", "given": "JA"}, {"family": "Doe", "given": "J"}],
        True,
    )
    assert parse_authors("Smith, John; Doe, Jane") == (
        [{"family": "Smith", "given": "John"}, {"family": "Doe", "given": "Jane"}],
        False,
    )


def test_vancouver_numbers_references_by_first_citation():
    rendered = render(["Statins help [@2]. Aspirin too [@1; @2]."], CSL, "vancouver")

    assert rendered.texts == ["Statins help [1]. Aspirin too [1,2]."]
    assert rendered.bibliography == [
        (1, 2, "Lee K, Park S, Kim H. Statins. Lancet. 2019."),
        (2, 1, "Smith JA, Doe J. Aspirin for prevention. BMJ. 2020;368(2):l100. doi:10.1136/bmj.l100"),
    ]


def test_apa_uses_author_date_citations_and_an_alphabetical_list():
    rendered = render(["Statins help [@2]. Aspirin too [@1; @2]."], CSL, "apa")

    assert rendered.texts == ["Statins help (Lee et al., 2019). Aspirin too (Smith & Doe, 2020; Lee et al., 2019)."]
    assert [entry for _, _, entry in rendered.bibliography] == [
        "Lee, K., Park, S., & Kim, H. (2019). Statins. *Lancet*.",
        "Smith, J. A., & Doe, J. (2020). Aspirin for prevention. *BMJ*, *368*(2), l100. https://doi.org/10.1136/bmj.l100",
    ]


def test_bibtex_and_ris_exports():
    bibtex = to_bibtex(CSL)
    ris = to_ris(CSL)

    assert "@article{ref1," in bibtex and "pages = {l100}" in bibtex and "doi = {10.1136/bmj.l100}" in bibtex
    assert "TY  - JOUR" in ris and "AU  - Smith, John A" in ris and "DO  - 10.1136/bmj.l100" in ris


# AI output handling


def test_drafts_are_assembled_with_markers_and_unknown_links_removed():
    output = DraftOutput(
        blocks=[
            DraftBlock(
                heading="Study selection",
                sentences=[
                    DraftSentence(text="We screened 1,200 records.", evidence=["prisma", "made:up"], citations=[5, 99])
                ],
            )
        ]
    )

    content, problems = assemble_draft(output, CATALOG, {5: {}})

    assert content == "## Study selection\n\nWe screened 1,200 records [#prisma] [@5].\n"
    assert problems == [
        "Removed evidence keys that don't exist: made:up",
        "Removed citations of references that don't exist: 99",
    ]


def test_language_edits_must_keep_markers_and_numbers():
    assert preservation_problems("Rate 0.59 [#analysis:3].", "The rate was 0.59 [#analysis:3].") == []
    assert preservation_problems("Rate 0.59 [#analysis:3].", "Rate 0.6.") == [
        "Evidence markers, citations, or embeds changed: removed analysis:3",
        "Numbers changed: removed 0.59; added 0.6",
    ]


def test_guideline_requirements_need_a_quote_found_in_the_guidelines():
    text = "Manuscripts should not exceed 3,500 words. Abstracts are limited to 250 words."
    output = GuidelineOutput(
        requirements=[
            RequirementOut(name="word_limit", value="3500", quote="should not exceed 3,500 words"),
            RequirementOut(name="figure_formats", value="TIFF", quote="Figures must be supplied as TIFF files"),
            RequirementOut(name="colour", value="blue", quote=""),
        ]
    )

    requirements = grounded_requirements(output, text)

    assert (requirements["word_limit"]["grounded"], requirements["figure_formats"]["grounded"]) == (True, False)
    assert "colour" not in requirements


def test_reviewer_reports_split_by_reviewer_and_number():
    report = (
        "Reviewer 1\n1. The search is outdated.\nPlease update it.\n2. Explain the GRADE ratings.\n\n"
        "Reviewer #2:\nThe abstract is too long."
    )

    assert rule_split_comments(report) == [
        {"reviewer": "Reviewer 1", "number": "1", "body": "The search is outdated. Please update it.", "category": ""},
        {"reviewer": "Reviewer 1", "number": "2", "body": "Explain the GRADE ratings.", "category": ""},
        {"reviewer": "Reviewer 2", "number": "1", "body": "The abstract is too long.", "category": ""},
    ]


# Checklists and publication helpers


def test_checklists_are_filled_from_headings_and_review_records():
    manuscript = models.Manuscript(title="Aspirin for prevention: a systematic review", statements={"funding": "None."})
    manuscript.sections = [
        models.ManuscriptSection(
            key="introduction",
            title="Introduction",
            position=0,
            content="## Rationale\n\nAspirin is common [@5].\n\n## Objectives\n",
        ),
        models.ManuscriptSection(
            key="methods",
            title="Methods",
            position=1,
            content="## Information sources\n\nWe searched PubMed. [#search]",
        ),
    ]
    context = {
        "title": manuscript.title, "kinds": {"database"}, "analysis_types": set(), "tools": set(), "grade": False,
        "registration": False, "surveillance": False, "ai_runs": False, "qa": False, "press": False, "embedded": set(),
        "statements": {"funding"}, "protocol_sections": set(),
    }  # fmt: skip

    prisma = {i["item_id"]: i for i in evaluate(CHECKLISTS["prisma_2020"], manuscript, context, {})}
    search = {i["item_id"]: i for i in evaluate(CHECKLISTS["prisma_s"], manuscript, context, {})}

    assert (prisma["1"]["status"], prisma["3"]["status"], prisma["3"]["location"]) == (
        "reported",
        "reported",
        "Introduction › Rationale",
    )
    assert (prisma["4"]["status"], prisma["6"]["status"]) == ("not_reported", "reported")
    assert search["3"]["status"] == "not_applicable"


def test_word_counts_ignore_markers_headings_and_embeds():
    assert word_count("## Methods\n\nWe screened 1,200 records [#prisma] [@5].\n\n[[table:sof]]") == 4
    assert first_int("Up to 3,500 words") == 3500
    assert "No artificial intelligence tools" in disclosure_text({"tasks": [], "oversight": {}})


# Surveillance


def test_surveillance_reads_sample_sizes_identifiers_and_feeds():
    assert sample_size("A randomized trial in 1,200 participants (n = 1250).") == 1250
    keys = record_keys("10.1/A", {"pmid": "123"}, "Aspirin trial", "2020")
    assert {"doi:10.1/a", "pmid:123"} <= keys
    rss = b"<rss><channel><item><guid>a1</guid><title>New guideline</title><link>https://x.org/1</link></item></channel></rss>"
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>tag:1</id><title>Update</title><link href="https://x.org/2"/></entry></feed>'
    assert parse_feed(rss) == [{"id": "a1", "title": "New guideline", "link": "https://x.org/1"}]
    assert parse_feed(atom) == [{"id": "tag:1", "title": "Update", "link": "https://x.org/2"}]
