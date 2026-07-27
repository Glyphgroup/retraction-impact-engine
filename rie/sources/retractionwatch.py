"""Retraction Watch ingest, via the Crossref Labs export.

Retraction Watch is the source of truth for retraction status. Crossref hosts the
full database as a CSV export, free, with a contact address as the query string:

    https://api.labs.crossref.org/data/retractionwatch?you@example.com

Deliberately NOT used: OpenAlex's ``is_retracted`` flag, which has documented
misclassifications (arXiv:2403.13339).

Two subtleties this module handles:

  * A retraction notice is itself a record with its own DOI. What we need is the
    ``OriginalPaperDOI`` -- the paper that was retracted -- not the notice.
  * ``RetractionNature`` distinguishes real retractions from expressions of
    concern, corrections and reinstatements. Only actual retractions should
    trigger a recomputation, so nature is preserved and filtering is explicit.
"""
from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .http import CONTACT, USER_AGENT, fetch

EXPORT_URL = "https://api.labs.crossref.org/data/retractionwatch?" + CONTACT

DEFAULT_PATH = Path(os.environ.get("RIE_DATA_DIR", "data")) / "retractions.csv"

#: Values of the RetractionNature column that mean the paper was withdrawn from
#: the literature. Anything else is a weaker signal and must not silently drive
#: a recomputation.
RETRACTION_NATURES = frozenset({"retraction", "removal", "withdrawal"})


def _parse_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    # The export uses US month/day/year with a trailing zero time.
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def normalise_doi(raw: str | None) -> str | None:
    """Lowercase, strip any resolver prefix. Returns None for blanks."""
    if not raw:
        return None
    doi = raw.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "http://dx.doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    doi = doi.strip()
    return doi or None


def normalise_pmid(raw: str | None) -> str | None:
    """Returns None for blanks and for the export's '0' placeholder."""
    if not raw:
        return None
    pmid = raw.strip().lstrip("0") if raw.strip() != "0" else ""
    return pmid or None


@dataclass(frozen=True)
class Retraction:
    """One retracted paper, keyed on the original paper rather than the notice."""
    record_id: str
    title: str
    journal: str
    #: Semicolon-separated in the export; kept split. Mass-retraction clusters
    #: are usually found by author, so this needs to be searchable.
    authors: tuple[str, ...]
    original_doi: str | None
    original_pmid: str | None
    retraction_doi: str | None
    retraction_pmid: str | None
    retraction_date: date | None
    original_date: date | None
    nature: str
    reasons: tuple[str, ...]

    @property
    def is_retraction(self) -> bool:
        return self.nature.strip().lower() in RETRACTION_NATURES

    @property
    def has_identifier(self) -> bool:
        return bool(self.original_doi or self.original_pmid)

    def has_author(self, surname: str) -> bool:
        needle = surname.strip().lower()
        return any(needle in a.lower() for a in self.authors)

    def effective_on(self, as_of: date) -> bool:
        """Whether the retraction had taken effect by ``as_of``.

        A missing date is treated as not yet effective, so an undated record
        never silently triggers a finding.
        """
        return self.retraction_date is not None and self.retraction_date <= as_of


def download(path: Path = DEFAULT_PATH, *, force: bool = False) -> Path:
    """Fetch the export to disk. Skips the download if the file already exists."""
    if path.exists() and not force:
        return path
    payload = fetch(EXPORT_URL, headers={"Accept": "text/csv", "User-Agent": USER_AGENT},
                    timeout=180)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def parse(text: str) -> list[Retraction]:
    reader = csv.DictReader(io.StringIO(text))
    out: list[Retraction] = []
    for row in reader:
        reasons = tuple(r.strip().lstrip("+").strip()
                        for r in (row.get("Reason") or "").split(";") if r.strip())
        out.append(Retraction(
            record_id=(row.get("Record ID") or "").strip(),
            title=(row.get("Title") or "").strip(),
            journal=(row.get("Journal") or "").strip(),
            authors=tuple(a.strip() for a in (row.get("Author") or "").split(";") if a.strip()),
            original_doi=normalise_doi(row.get("OriginalPaperDOI")),
            original_pmid=normalise_pmid(row.get("OriginalPaperPubMedID")),
            retraction_doi=normalise_doi(row.get("RetractionDOI")),
            retraction_pmid=normalise_pmid(row.get("RetractionPubMedID")),
            retraction_date=_parse_date(row.get("RetractionDate", "")),
            original_date=_parse_date(row.get("OriginalPaperDate", "")),
            nature=(row.get("RetractionNature") or "").strip(),
            reasons=reasons,
        ))
    return out


def load(path: Path = DEFAULT_PATH) -> list[Retraction]:
    return parse(path.read_text(encoding="utf-8-sig", errors="replace"))


@dataclass
class RetractionIndex:
    """Lookup of retraction status by DOI or PubMed ID.

    Only records whose nature is an actual retraction are indexed, so a hit
    always means the paper was withdrawn, not merely flagged.
    """
    by_doi: dict[str, Retraction] = field(default_factory=dict)
    by_pmid: dict[str, Retraction] = field(default_factory=dict)
    #: Records skipped, with the reason, so coverage gaps stay visible.
    skipped: dict[str, int] = field(default_factory=dict)

    @classmethod
    def build(cls, records: list[Retraction], *,
              natures: frozenset[str] = RETRACTION_NATURES) -> "RetractionIndex":
        index = cls()
        for r in records:
            if r.nature.strip().lower() not in natures:
                index.skipped["nature:" + (r.nature or "blank")] = \
                    index.skipped.get("nature:" + (r.nature or "blank"), 0) + 1
                continue
            if not r.has_identifier:
                index.skipped["no_identifier"] = index.skipped.get("no_identifier", 0) + 1
                continue
            if r.original_doi:
                index.by_doi.setdefault(r.original_doi, r)
            if r.original_pmid:
                index.by_pmid.setdefault(r.original_pmid, r)
        return index

    def lookup(self, *, doi: str | None = None, pmid: str | None = None) -> Retraction | None:
        d = normalise_doi(doi)
        if d and d in self.by_doi:
            return self.by_doi[d]
        p = normalise_pmid(pmid)
        if p and p in self.by_pmid:
            return self.by_pmid[p]
        return None

    def is_retracted(self, *, doi: str | None = None, pmid: str | None = None,
                     as_of: date | None = None) -> bool:
        """True when the identifier is a retracted paper as of ``as_of``."""
        hit = self.lookup(doi=doi, pmid=pmid)
        return bool(hit and hit.effective_on(as_of or date.today()))

    def __len__(self) -> int:
        return len(self.by_doi) + len(self.by_pmid)


def build_index(path: Path = DEFAULT_PATH, *, download_if_missing: bool = True) -> RetractionIndex:
    if download_if_missing:
        download(path)
    return RetractionIndex.build(load(path))
