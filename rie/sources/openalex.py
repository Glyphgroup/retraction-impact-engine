"""OpenAlex citation walk: from a retracted paper to the syntheses citing it.

OpenAlex is used only for the citation graph and for bibliographic metadata.
Its ``is_retracted`` field is deliberately ignored: it has documented
misclassifications (arXiv:2403.13339). Retraction status comes from Retraction
Watch, always.

Identifying syntheses is done conservatively. OpenAlex exposes a ``type`` of
"review" and indexes MeSH-style concepts, but neither reliably separates a
systematic review with a meta-analysis from a narrative review. So candidates
are scored on explicit title and abstract signals and the reason is recorded,
rather than trusting a single flag. Anything uncertain stays a candidate and is
resolved downstream by whether its numbers can actually be extracted and
reproduced.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from .http import CONTACT, build_url, fetch_json

BASE = "https://api.openalex.org"
PER_PAGE = 200

#: Phrases that indicate a quantitative synthesis. Ordered strongest first.
STRONG_SIGNALS = (
    "meta-analysis", "meta analysis", "metaanalysis",
    "systematic review and meta", "pooled analysis",
)
WEAK_SIGNALS = (
    "systematic review", "systematic literature review", "evidence synthesis",
    "network meta", "individual participant data",
)
GUIDELINE_SIGNALS = (
    "clinical practice guideline", "practice guideline", "consensus statement",
    "recommendations for", "guideline update",
)


def _mailto(**params) -> dict:
    return {"mailto": CONTACT, **params}


def normalise_doi(doi: str) -> str:
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d


@dataclass(frozen=True)
class Work:
    openalex_id: str
    doi: str | None
    pmid: str | None
    pmcid: str | None
    title: str
    publication_year: int | None
    type: str | None
    journal: str | None
    is_open_access: bool
    cited_by_count: int
    referenced_works: tuple[str, ...] = ()

    @property
    def short_id(self) -> str:
        return self.openalex_id.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class SynthesisCandidate:
    work: Work
    #: Which signals fired, so the classification is auditable.
    signals: tuple[str, ...]
    kind: str  # "meta_analysis" | "systematic_review" | "guideline"

    @property
    def confidence(self) -> str:
        return "high" if self.kind == "meta_analysis" else "medium"


def _ids(work: dict) -> tuple[str | None, str | None, str | None]:
    ids = work.get("ids") or {}
    doi = ids.get("doi")
    pmid = ids.get("pmid")
    pmcid = ids.get("pmcid")
    if doi:
        doi = normalise_doi(doi)
    if pmid:
        pmid = pmid.rsplit("/", 1)[-1]
    if pmcid:
        pmcid = pmcid.rsplit("/", 1)[-1]
    return doi, pmid, pmcid


def parse_work(payload: dict) -> Work:
    doi, pmid, pmcid = _ids(payload)
    location = payload.get("primary_location") or {}
    source = location.get("source") or {}
    return Work(
        openalex_id=payload.get("id") or "",
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        title=payload.get("title") or payload.get("display_name") or "",
        publication_year=payload.get("publication_year"),
        type=payload.get("type"),
        journal=source.get("display_name"),
        is_open_access=bool((payload.get("open_access") or {}).get("is_oa")),
        cited_by_count=payload.get("cited_by_count") or 0,
        referenced_works=tuple(payload.get("referenced_works") or ()),
    )


def get_work_by_doi(doi: str) -> Work | None:
    url = build_url("%s/works/doi:%s" % (BASE, normalise_doi(doi)), **_mailto())
    try:
        return parse_work(fetch_json(url, namespace="openalex"))
    except Exception:
        return None


def get_work_by_pmid(pmid: str) -> Work | None:
    url = build_url("%s/works/pmid:%s" % (BASE, pmid), **_mailto())
    try:
        return parse_work(fetch_json(url, namespace="openalex"))
    except Exception:
        return None


def iter_citing_works(openalex_id: str, *, max_results: int = 2000) -> Iterator[Work]:
    """Page through everything that cites ``openalex_id``.

    Cursor paging is used rather than page numbers because OpenAlex caps
    page-based paging at 10,000 results.
    """
    cursor = "*"
    seen = 0
    short = openalex_id.rsplit("/", 1)[-1]
    while cursor and seen < max_results:
        url = build_url("%s/works" % BASE, **_mailto(
            filter="cites:%s" % short,
            per_page=min(PER_PAGE, max_results - seen),
            cursor=cursor,
            select="id,ids,title,display_name,publication_year,type,primary_location,open_access,cited_by_count",
        ))
        payload = fetch_json(url, namespace="openalex")
        results = payload.get("results") or []
        if not results:
            return
        for item in results:
            yield parse_work(item)
            seen += 1
        cursor = (payload.get("meta") or {}).get("next_cursor")


def classify(work: Work, *, abstract: str = "") -> SynthesisCandidate | None:
    """Decide whether a citing work looks like a quantitative synthesis.

    Returns None when nothing indicates a synthesis. The matched signals travel
    with the result so a human can see why it was selected.
    """
    haystack = ("%s %s" % (work.title or "", abstract or "")).lower()
    signals = []

    for phrase in STRONG_SIGNALS:
        if phrase in haystack:
            signals.append(phrase)
    if signals:
        return SynthesisCandidate(work, tuple(signals), "meta_analysis")

    for phrase in GUIDELINE_SIGNALS:
        if phrase in haystack:
            signals.append(phrase)
    if signals:
        return SynthesisCandidate(work, tuple(signals), "guideline")

    for phrase in WEAK_SIGNALS:
        if phrase in haystack:
            signals.append(phrase)
    if signals:
        return SynthesisCandidate(work, tuple(signals), "systematic_review")

    return None


@dataclass
class CitationWalk:
    """Result of walking outward from one retracted paper."""
    retracted: Work
    citing_total: int = 0
    candidates: list[SynthesisCandidate] = field(default_factory=list)

    @property
    def by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.candidates:
            counts[c.kind] = counts.get(c.kind, 0) + 1
        return counts

    @property
    def open_access_candidates(self) -> list[SynthesisCandidate]:
        """Candidates we have a realistic chance of extracting data from."""
        return [c for c in self.candidates if c.work.is_open_access or c.work.pmcid]


def walk(retracted_doi: str | None = None, *, pmid: str | None = None,
         max_results: int = 2000) -> CitationWalk | None:
    """Find syntheses citing one retracted paper."""
    work = get_work_by_doi(retracted_doi) if retracted_doi else None
    if work is None and pmid:
        work = get_work_by_pmid(pmid)
    if work is None:
        return None

    result = CitationWalk(retracted=work)
    for citing in iter_citing_works(work.openalex_id, max_results=max_results):
        result.citing_total += 1
        candidate = classify(citing)
        if candidate is not None:
            result.candidates.append(candidate)
    return result
