"""Work out, from input data alone, which studies RevMan pools in a subgrouped analysis.

Analyses 11 and 12 fail the gate because RevMan pooled 7 studies where we pooled
8. The hypothesis is that a study with no value for the subgrouping covariate is
dropped from the total. This checks that against the covariate definitions and
values, so membership is derived from inputs rather than from RevMan's answer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from rie.sources import cochrane

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = Path("data/cochrane")
REVIEW = "214326072721231561"
SUSPECT = {"443282596626443148", "443282597875821569"}


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def main() -> None:
    defs = {d["id"]: d for d in cochrane.unwrap(
        load("reviews_%s_covariateDefinitions.json" % REVIEW), "CovariateDefinitions")}
    print("covariate definitions:")
    for d in defs.values():
        print("  %s  name=%r  type=%s" % (d["id"], d.get("name"), d.get("type")))

    values = cochrane.unwrap(load("reviews_%s_covariateValues.json" % REVIEW), "CovariateValues")
    print("\ncovariate values: %d" % len(values))
    if values:
        print("  sample keys: %s" % sorted(values[0]))

    # study covariate assignments
    scv_by_def: dict[str, set[str]] = {}
    for v in values:
        scv_by_def.setdefault(str(v.get("covariateDefinitionId")), set())
    print("\nvalue ids per definition:")
    for v in values:
        did = str(v.get("covariateDefinitionId"))
        print("  def=%s valueId=%s value=%r" % (did, v.get("id"), v.get("value")))

    analyses = {str(a["id"]): a for a in cochrane.unwrap(
        load("reviews_%s_pairwiseAnalyses.json" % REVIEW), "PairwiseAnalyses", "Analyses")}

    rows_names = cochrane.study_names(
        load("reviews_%s_pairwiseAnalyses_%s_pairwiseDataRows.json"
             % (REVIEW, "443282596626443148")))

    for aid in sorted(SUSPECT):
        a = analyses[aid]
        print("\n" + "=" * 88)
        print("analysis %s  %r" % (a.get("number"), a.get("name")))
        print("  subgroupType=%s  subgroupByCovariateDefinitionId=%s  filterByCovariateValueId=%s"
              % (a.get("subgroupType"), a.get("subgroupByCovariateDefinitionId"),
                 a.get("filterByCovariateValueId")))
        did = str(a.get("subgroupByCovariateDefinitionId"))
        print("  covariate: %r" % (defs.get(did, {}).get("name")))

        subgroups = cochrane.unwrap(
            load("reviews_%s_pairwiseAnalyses_%s_pairwiseSubgroups.json" % (REVIEW, aid)),
            "PairwiseSubgroups")
        in_subgroups: set[str] = set()
        for g in subgroups:
            ids = {str(r.get("studyId") or (r.get("study") or {}).get("id"))
                   for r in cochrane.unwrap(g.get("pairwiseDataRows"), "PairwiseDataRows")}
            in_subgroups |= ids
            print("  subgroup %r: %d studies" % (g.get("name"), len(ids)))

        all_rows = cochrane.unwrap(
            load("reviews_%s_pairwiseAnalyses_%s_pairwiseDataRows.json" % (REVIEW, aid)),
            "PairwiseDataRows")
        all_ids = {str(r.get("studyId") or (r.get("study") or {}).get("id")) for r in all_rows}
        missing = all_ids - in_subgroups
        print("  data rows=%d  in a subgroup=%d  unassigned=%d"
              % (len(all_ids), len(in_subgroups), len(missing)))
        for sid in sorted(missing):
            print("    unassigned: %s (%s)" % (sid, rows_names.get(sid, "?")))


if __name__ == "__main__":
    main()
