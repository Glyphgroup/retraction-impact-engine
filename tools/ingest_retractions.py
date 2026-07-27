"""Download and index the Retraction Watch database, and report its shape."""
from __future__ import annotations

import sys
from collections import Counter
from datetime import date

from rie.sources import retractionwatch as rw

# Titles contain Greek letters and other non-cp1252 characters, which the
# default Windows console encoding cannot represent.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    path = rw.download()
    print("export: %s (%.1f MB)" % (path, path.stat().st_size / 1e6))

    records = rw.load(path)
    print("records: %d" % len(records))

    natures = Counter(r.nature or "(blank)" for r in records)
    print("\nRetractionNature:")
    for nature, n in natures.most_common(10):
        print("  %-28s %6d" % (nature, n))

    index = rw.RetractionIndex.build(records)
    print("\nindexed retractions: %d DOIs, %d PMIDs" % (len(index.by_doi), len(index.by_pmid)))
    print("skipped:")
    for reason, n in sorted(index.skipped.items(), key=lambda kv: -kv[1])[:6]:
        print("  %-34s %6d" % (reason, n))

    retractions = [r for r in records if r.is_retraction]
    undated = sum(1 for r in retractions if r.retraction_date is None)
    print("\nretractions: %d, of which undated: %d" % (len(retractions), undated))
    effective = sum(1 for r in retractions if r.effective_on(date.today()))
    print("effective as of today: %d" % effective)

    reasons = Counter(reason for r in retractions for reason in r.reasons)
    print("\ntop retraction reasons:")
    for reason, n in reasons.most_common(10):
        print("  %-46s %6d" % (reason[:46], n))

    # Boldt is the canonical mass-retraction cluster for anaesthesia trials and
    # the first target for the citation walk.
    boldt = [r for r in retractions if r.has_author("Boldt")]
    print("\nBoldt-authored retractions: %d" % len(boldt))
    with_doi = sum(1 for r in boldt if r.original_doi)
    with_pmid = sum(1 for r in boldt if r.original_pmid)
    print("  with original DOI: %d   with original PMID: %d" % (with_doi, with_pmid))
    years = Counter(r.retraction_date.year for r in boldt if r.retraction_date)
    print("  retraction years: %s" % dict(sorted(years.items())))
    for r in boldt[:5]:
        print("   - %s | %s | doi=%s pmid=%s"
              % (r.title[:58], r.journal[:26], r.original_doi, r.original_pmid))


if __name__ == "__main__":
    main()
