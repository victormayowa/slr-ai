import xml.etree.ElementTree as ET

import pytest
import requests

from services import openalex, pubmed
from services.errors import SearchError

PUBMED_ARTICLE = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>123</PMID>
    <Article>
      <Journal>
        <JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue>
        <Title>The Lancet</Title>
      </Journal>
      <ArticleTitle>Aspirin <i>trial</i></ArticleTitle>
      <Abstract>
        <AbstractText Label="METHODS">We randomized adults.</AbstractText>
        <AbstractText Label="RESULTS">Events fell.</AbstractText>
      </Abstract>
      <AuthorList>
        <Author><LastName>Smith</LastName><Initials>J</Initials></Author>
        <Author><CollectiveName>Aspirin Trialists</CollectiveName></Author>
      </AuthorList>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList><ArticleId IdType="doi">10.1000/abc</ArticleId></ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_pubmed_article_parsed_with_structured_abstract():
    record = pubmed._parse_article(ET.fromstring(PUBMED_ARTICLE))

    assert record == {
        "id": "123",
        "title": "Aspirin trial",
        "authors": "Smith J, Aspirin Trialists",
        "year": "2021",
        "source": "PubMed",
        "venue": "The Lancet",
        "doi": "10.1000/abc",
        "abstract": "METHODS: We randomized adults.\nRESULTS: Events fell.",
    }


def test_pubmed_article_without_abstract_has_empty_abstract():
    article = ET.fromstring(PUBMED_ARTICLE)
    details = article.find("MedlineCitation/Article")
    details.remove(details.find("Abstract"))

    assert pubmed._parse_article(article)["abstract"] == ""


def test_pubmed_no_matches_returns_empty_list(monkeypatch):
    monkeypatch.setattr(pubmed.requests, "get", lambda *a, **k: FakeResponse({"esearchresult": {"idlist": []}}))

    assert pubmed.search_pubmed("nothing matches this") == []


def test_pubmed_network_failure_raises_search_error(monkeypatch):
    def unreachable(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(pubmed.requests, "get", unreachable)

    with pytest.raises(SearchError):
        pubmed.search_pubmed("aspirin")


def test_openalex_abstract_rebuilt_in_word_order():
    index = {"aspirin": [1], "Low-dose": [0], "works": [2, 4], "well": [3]}

    assert openalex._abstract_from_inverted_index(index) == "Low-dose aspirin works well works"


def test_openalex_missing_abstract_is_empty():
    assert openalex._abstract_from_inverted_index(None) == ""


def test_openalex_network_failure_raises_search_error(monkeypatch):
    def unreachable(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(openalex.requests, "get", unreachable)

    with pytest.raises(SearchError):
        openalex.search_openalex("aspirin")


@pytest.mark.live
def test_live_pubmed_returns_abstracts():
    results = pubmed.search_pubmed("aspirin cardiovascular prevention", 3)

    assert results and any(r["abstract"] for r in results)


@pytest.mark.live
def test_live_openalex_returns_abstracts():
    results = openalex.search_openalex("aspirin cardiovascular prevention", 3)

    assert results and any(r["abstract"] for r in results)
