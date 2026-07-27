"""Walk from the Boldt retractions to the syntheses that cite them.

Joachim Boldt's anaesthesia trials are the canonical mass-retraction cluster and
the natural first target: many retractions, well documented, and heavily cited by
meta-analyses of colloid fluid therapy.

Ranks candidate syntheses by how many retracted trials each one cites, since a
review resting on several retracted trials is where a recomputation is most
likely to move the conclusion.
"""
from __future__ import annotations

import sys
from collections import defaultdict

from rie.sources import openalex, retractionwatch as rw

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

AUTHOR = "Boldt"
#: Trials cited by the most syntheses first, so a partial run is still useful.
SEED_LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 40


def main() -> None:
    records = [r for r in rw.load(rw.download())
               if r.is_retraction and r.has_author(AUTHOR) and r.original_doi]
    print("%s retractions with a DOI: %d" % (AUTHOR, len(records)))

    resolved = []
    for record in records:
        work = openalex.get_work_by_doi(record.original_doi)
        if work is None:
            continue
        resolved.append((record, work))
    resolved.sort(key=lambda pair: -pair[1].cited_by_count)
    print("resolved in OpenAlex: %d" % len(resolved))
    print("total citations across them: %d"
          % sum(w.cited_by_count for _, w in resolved))

    seeds = resolved[:SEED_LIMIT]
    print("\nwalking the %d most-cited retracted trials\n" % len(seeds))

    # synthesis openalex id -> (candidate, set of retracted trials it cites)
    hits: dict[str, tuple[openalex.SynthesisCandidate, set[str]]] = {}
    contaminating: dict[str, int] = defaultdict(int)

    for record, work in seeds:
        walk = openalex.CitationWalk(retracted=work)
        for citing in openalex.iter_citing_works(work.openalex_id, max_results=1000):
            walk.citing_total += 1
            candidate = openalex.classify(citing)
            if candidate is None:
                continue
            walk.candidates.append(candidate)
            key = citing.openalex_id
            if key not in hits:
                hits[key] = (candidate, set())
            hits[key][1].add(record.original_doi)
        contaminating[record.original_doi] = len(walk.candidates)
        print("  %-42s cited by %4d, syntheses %3d  %s"
              % (record.original_doi, walk.citing_total, len(walk.candidates),
                 record.title[:44]))

    ranked = sorted(hits.values(), key=lambda pair: (-len(pair[1]), pair[0].work.title))
    print("\n" + "=" * 100)
    print("candidate syntheses citing at least one retracted Boldt trial: %d" % len(ranked))
    kinds: dict[str, int] = defaultdict(int)
    for candidate, _ in ranked:
        kinds[candidate.kind] += 1
    print("by kind: %s" % dict(kinds))
    oa = sum(1 for c, _ in ranked if c.work.is_open_access or c.work.pmcid)
    print("open access or in PMC (extractable): %d" % oa)

    print("\ntop candidates by number of retracted trials cited")
    print("-" * 100)
    for candidate, dois in ranked[:25]:
        w = candidate.work
        print("%2d retracted | %-16s | %s | OA=%s PMC=%s"
              % (len(dois), candidate.kind, (w.title or "")[:56],
                 "y" if w.is_open_access else "n", w.pmcid or "-"))
        print("             | %s | %s | doi=%s"
              % (w.publication_year, (w.journal or "")[:40], w.doi))


if __name__ == "__main__":
    main()
