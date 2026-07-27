"""Offline tests for the Retraction Watch and OpenAlex parsing logic."""
from __future__ import annotations

from datetime import date

import pytest

from rie.sources import openalex
from rie.sources import retractionwatch as rw

HEADER = ("Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,"
          "ArticleType,RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,"
          "OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes,")


def csv(*rows: str) -> str:
    return HEADER + "\n" + "\n".join(rows) + "\n"


def row(record_id="1", title="A trial", journal="J Anesth", author="Boldt J;Smith A",
        retraction_date="3/8/2011 0:00", retraction_doi="10.1/notice",
        retraction_pmid="12345", original_date="1/1/2005 0:00",
        original_doi="10.1/Original", original_pmid="999", nature="Retraction",
        reason="+Investigation by Journal/Publisher;+Concerns/Issues about Data;"):
    return ",".join([
        record_id, title, "", "", journal, "", "", author, "", "",
        retraction_date, retraction_doi, retraction_pmid, original_date,
        original_doi, original_pmid, nature, '"%s"' % reason, "", "", "",
    ])


# --------------------------------------------------------------------------
# Retraction Watch
# --------------------------------------------------------------------------

def test_identifiers_are_normalised():
    r = rw.parse(csv(row(original_doi="https://doi.org/10.1/ORIGINAL")))[0]
    assert r.original_doi == "10.1/original"
    assert r.original_pmid == "999"


def test_zero_pubmed_id_is_treated_as_absent():
    """The export writes 0 where no PubMed ID exists."""
    r = rw.parse(csv(row(original_pmid="0")))[0]
    assert r.original_pmid is None


def test_dates_are_parsed_from_the_us_format_used_by_the_export():
    r = rw.parse(csv(row()))[0]
    assert r.retraction_date == date(2011, 3, 8)
    assert r.original_date == date(2005, 1, 1)


def test_reasons_are_split_and_stripped_of_the_leading_plus():
    r = rw.parse(csv(row()))[0]
    assert r.reasons == ("Investigation by Journal/Publisher", "Concerns/Issues about Data")


def test_authors_are_split_and_searchable():
    r = rw.parse(csv(row()))[0]
    assert r.authors == ("Boldt J", "Smith A")
    assert r.has_author("boldt")
    assert not r.has_author("Higgins")


def test_only_actual_retractions_are_indexed():
    records = rw.parse(csv(
        row(record_id="1", original_doi="10.1/retracted"),
        row(record_id="2", original_doi="10.1/concern", nature="Expression of concern"),
        row(record_id="3", original_doi="10.1/correction", nature="Correction"),
        row(record_id="4", original_doi="10.1/reinstated", nature="Reinstatement"),
    ))
    index = rw.RetractionIndex.build(records)
    assert index.is_retracted(doi="10.1/retracted")
    assert not index.is_retracted(doi="10.1/concern")
    assert not index.is_retracted(doi="10.1/correction")
    assert not index.is_retracted(doi="10.1/reinstated")
    assert sum(index.skipped.values()) == 3


def test_records_without_an_identifier_are_skipped_and_counted():
    index = rw.RetractionIndex.build(
        rw.parse(csv(row(original_doi="", original_pmid="0"))))
    assert len(index.by_doi) == 0
    assert index.skipped["no_identifier"] == 1


def test_retraction_after_the_as_of_date_does_not_count_yet():
    index = rw.RetractionIndex.build(rw.parse(csv(row())))
    assert index.is_retracted(doi="10.1/original", as_of=date(2011, 3, 8))
    assert not index.is_retracted(doi="10.1/original", as_of=date(2011, 3, 7))


def test_undated_retraction_never_silently_triggers_a_finding():
    index = rw.RetractionIndex.build(rw.parse(csv(row(retraction_date=""))))
    assert index.lookup(doi="10.1/original") is not None
    assert not index.is_retracted(doi="10.1/original")


def test_lookup_falls_back_from_doi_to_pubmed_id():
    index = rw.RetractionIndex.build(rw.parse(csv(row())))
    assert index.lookup(doi="10.1/unknown", pmid="999") is not None
    assert index.lookup(doi="10.1/unknown", pmid="404") is None


# --------------------------------------------------------------------------
# OpenAlex
# --------------------------------------------------------------------------

def work(title: str, **kw) -> openalex.Work:
    defaults = dict(openalex_id="https://openalex.org/W1", doi="10.1/x", pmid=None,
                    pmcid=None, publication_year=2013, type="article",
                    journal="BMJ", is_open_access=True, cited_by_count=10)
    defaults.update(kw)
    return openalex.Work(title=title, **defaults)


@pytest.mark.parametrize("title", [
    "Hydroxyethyl starch versus crystalloid: a meta-analysis",
    "Colloids in sepsis: systematic review and meta-analysis",
    "A pooled analysis of albumin trials",
])
def test_meta_analyses_are_classified_with_high_confidence(title):
    candidate = openalex.classify(work(title))
    assert candidate is not None
    assert candidate.kind == "meta_analysis"
    assert candidate.confidence == "high"
    assert candidate.signals


def test_systematic_reviews_without_pooling_are_separated():
    candidate = openalex.classify(work("A systematic review of colloid safety"))
    assert candidate.kind == "systematic_review"
    assert candidate.confidence == "medium"


def test_guidelines_are_recognised():
    candidate = openalex.classify(work("Clinical practice guideline for fluid therapy"))
    assert candidate.kind == "guideline"


def test_primary_trials_and_narrative_reviews_are_not_candidates():
    assert openalex.classify(work("Effects of hydroxyethyl starch on renal function")) is None
    assert openalex.classify(work("Fluid therapy: an update")) is None


def test_abstract_text_contributes_signals():
    w = work("Colloid resuscitation in critical illness")
    assert openalex.classify(w) is None
    assert openalex.classify(w, abstract="We performed a meta-analysis of 12 trials.").kind \
        == "meta_analysis"


def test_work_parsing_strips_identifier_prefixes():
    parsed = openalex.parse_work({
        "id": "https://openalex.org/W2000",
        "ids": {"doi": "https://doi.org/10.1136/BMJ.F839",
                "pmid": "https://pubmed.ncbi.nlm.nih.gov/23418281",
                "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3573769"},
        "title": "Hydroxyethyl starch versus crystalloid",
        "publication_year": 2013,
        "type": "review",
        "primary_location": {"source": {"display_name": "BMJ"}},
        "open_access": {"is_oa": True},
        "cited_by_count": 400,
    })
    assert parsed.doi == "10.1136/bmj.f839"
    assert parsed.pmid == "23418281"
    assert parsed.pmcid == "PMC3573769"
    assert parsed.short_id == "W2000"


def test_citation_walk_counts_candidates_by_kind():
    retracted = work("A retracted trial")
    walk = openalex.CitationWalk(retracted=retracted)
    walk.candidates = [
        openalex.classify(work("x meta-analysis")),
        openalex.classify(work("y meta-analysis")),
        openalex.classify(work("z systematic review")),
    ]
    assert walk.by_kind == {"meta_analysis": 2, "systematic_review": 1}


def test_open_access_filter_keeps_only_extractable_candidates():
    walk = openalex.CitationWalk(retracted=work("retracted"))
    walk.candidates = [
        openalex.classify(work("open meta-analysis", is_open_access=True)),
        openalex.classify(work("closed meta-analysis", is_open_access=False)),
        openalex.classify(work("pmc meta-analysis", is_open_access=False, pmcid="PMC1")),
    ]
    titles = {c.work.title for c in walk.open_access_candidates}
    assert titles == {"open meta-analysis", "pmc meta-analysis"}
