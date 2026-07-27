"""Verify that covariate assignments alone predict RevMan's study membership.

If a subgrouped analysis drops studies with no value for the subgrouping
covariate, then the set of studies holding a value for that covariate should
exactly equal the set RevMan pooled, for every COVARIATE-subgrouped analysis.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from rie.sources import cochrane

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = Path("data/cochrane")
REVIEW = "214326072721231561"


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def main() -> None:
    defs = {str(d["id"]): d.get("name") for d in cochrane.unwrap(
        load("reviews_%s_covariateDefinitions.json" % REVIEW), "CovariateDefinitions")}
    values = {str(v["id"]): str(v["covariateDefinitionId"]) for v in cochrane.unwrap(
        load("reviews_%s_covariateValues.json" % REVIEW), "CovariateValues")}

    included = cochrane.unwrap(load("reviews_%s_studies_included.json" % REVIEW), "Studies")
    names = {str(s["id"]): s.get("name") for s in included}

    # study id -> set of covariate definition ids it has a value for
    assigned: dict[str, set[str]] = {}
    for sid in names:
        payload = load("reviews_%s_studies_%s_studyCovariateValues.json" % (REVIEW, sid))
        rows = cochrane.unwrap(payload, "StudyCovariateValues")
        dids = set()
        for r in rows:
            vid = str(r.get("covariateValueId") or "")
            did = str(r.get("covariateDefinitionId") or values.get(vid) or "")
            if did:
                dids.add(did)
        assigned[sid] = dids

    print("covariate assignments per study:")
    for sid, dids in assigned.items():
        print("  %-22s %d covariates" % (names[sid], len(dids)))

    analyses = cochrane.unwrap(
        load("reviews_%s_pairwiseAnalyses.json" % REVIEW), "PairwiseAnalyses", "Analyses")
    analyses.sort(key=lambda a: a.get("number") or 0)

    print("\n%-4s %-46s %-8s %-8s %s" % ("#", "subgroup covariate", "rows", "predict", "revman"))
    print("-" * 92)
    agree = total = 0
    for a in analyses:
        if a.get("subgroupType") != "COVARIATE":
            continue
        aid = str(a["id"])
        did = str(a.get("subgroupByCovariateDefinitionId") or "")
        rows = cochrane.unwrap(
            load("reviews_%s_pairwiseAnalyses_%s_pairwiseDataRows.json" % (REVIEW, aid)),
            "PairwiseDataRows")
        row_ids = {str(r.get("studyId") or (r.get("study") or {}).get("id")) for r in rows}
        predicted = {sid for sid in row_ids if did in assigned.get(sid, set())}

        published = cochrane.parse_results(
            load("reviews_%s_analyses_%s_results.json" % (REVIEW, aid)))
        revman_k = published.k

        total += 1
        ok = len(predicted) == revman_k
        agree += ok
        print("%-4s %-46s %-8d %-8d %-6d %s"
              % (a.get("number"), (defs.get(did) or "?")[:46], len(row_ids),
                 len(predicted), revman_k, "OK" if ok else "MISMATCH"))
        if not ok:
            print("      dropped by us: %s"
                  % sorted(names[s] for s in row_ids - predicted))

    print("\ncovariate-subgrouped analyses where the prediction matches RevMan: %d / %d"
          % (agree, total))


if __name__ == "__main__":
    main()
