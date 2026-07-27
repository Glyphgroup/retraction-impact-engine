"""Reduce the cached Cochrane dumps to a compact numeric regression fixture.

Only what the gate needs travels into the repo: the analysis configuration, the
study-level numbers, and RevMan's published pooled result. Review prose,
authorship, risk-of-bias text and internal identifiers are dropped.
"""
from __future__ import annotations

import json
from pathlib import Path

from rie.sources import cochrane

DATA = Path("data/cochrane")
OUT = Path("tests/data/cochrane_asthma.json")
REVIEW = "214326072721231561"

KEEP_CONFIG = ("number", "name", "method", "model", "effectMeasure",
               "heterogeneityEstimator", "ciMethod", "ciLevel", "swapEvents",
               "subgroupType", "subgroupByCovariateDefinitionId", "dataSource")
KEEP_ROW = ("events1", "total1", "events2", "total2", "mean1", "sd1", "mean2",
            "sd2", "n1", "n2", "estimate", "se", "oe", "variance")


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def main() -> None:
    analyses = cochrane.unwrap(
        load("reviews_%s_pairwiseAnalyses.json" % REVIEW), "PairwiseAnalyses", "Analyses")
    analyses.sort(key=lambda a: a.get("number") or 0)

    # Covariate assignments decide study membership in subgrouped analyses, so
    # they travel with the fixture. Only the definition ids are kept.
    value_map = cochrane.parse_covariate_value_map(
        load("reviews_%s_covariateValues.json" % REVIEW))
    included = cochrane.unwrap(load("reviews_%s_studies_included.json" % REVIEW), "Studies")
    payloads = {}
    for study in included:
        sid = str(study["id"])
        try:
            payloads[sid] = load("reviews_%s_studies_%s_studyCovariateValues.json" % (REVIEW, sid))
        except FileNotFoundError:
            continue
    assignments = cochrane.parse_study_covariate_assignments(payloads, value_map)

    out = []
    for a in analyses:
        aid = str(a["id"])
        try:
            raw_rows = load("reviews_%s_pairwiseAnalyses_%s_pairwiseDataRows.json" % (REVIEW, aid))
            raw_res = load("reviews_%s_analyses_%s_results.json" % (REVIEW, aid))
        except FileNotFoundError:
            continue

        rows = []
        for row in cochrane.unwrap(raw_rows, "PairwiseDataRows"):
            study = row.get("study") or {}
            entry = {"studyId": str(row.get("studyId") or study.get("id")),
                     "name": study.get("name") or ""}
            entry.update({k: row[k] for k in KEEP_ROW if row.get(k) is not None})
            rows.append(entry)

        published = cochrane.parse_results(raw_res)
        result = (raw_res.get("result") or {})
        out.append({
            "analysis": {k: a.get(k) for k in KEEP_CONFIG if a.get(k) is not None},
            "dataRows": rows,
            "published": {
                "estimable": published.estimable,
                "logScale": published.log_scale,
                "mean": published.estimate,
                "se": published.se,
                "ciStart": published.ci_low,
                "ciEnd": published.ci_high,
                "heterogeneity": result.get("heterogeneity"),
                "overallEffect": result.get("overallEffect"),
                "k": published.k,
            },
        })

    fixture = {
        "review": REVIEW,
        "studyCovariateAssignments": {k: sorted(v) for k, v in assignments.items()},
        "analyses": out,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, indent=1), encoding="utf-8")
    print("wrote %s: %d analyses, %d studies with covariates, %d bytes"
          % (OUT, len(out), len(assignments), OUT.stat().st_size))


if __name__ == "__main__":
    main()
