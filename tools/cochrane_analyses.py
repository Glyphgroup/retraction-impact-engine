"""For each analysis in the practice review, dump its config, data rows and results.

The results endpoint is the reproduce-gate target: it is RevMan's own pooled
estimate, computed by RevMan, for the same study-level numbers we are given.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from cochrane_dump import REVIEW, dump
from cochrane_probe import claims, get

ANALYSES = Path("../data/cochrane/reviews_214326072721231561_pairwiseAnalyses.json")

CONFIG_KEYS = ("id", "number", "name", "method", "model", "effectMeasure",
               "heterogeneityEstimator", "ciMethod", "ciLevel", "totals",
               "swapEvents", "subgroupType", "type", "dataSource")


def analyses() -> list[dict]:
    payload = json.loads(ANALYSES.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        for key in ("PairwiseAnalyses", "Analyses", "pairwiseAnalyses", "analyses"):
            if key in payload:
                return payload[key]
        # Fall back to the first list-valued key.
        for v in payload.values():
            if isinstance(v, list):
                return v
    return payload if isinstance(payload, list) else []


if __name__ == "__main__":
    left = claims()["exp"] - time.time()
    print("token %.1f minutes remaining\n" % (left / 60))
    if left <= 0:
        sys.exit("TOKEN EXPIRED. Fetch a fresh one from RevMan Web.")

    rows = analyses()
    print("found %d analyses\n" % len(rows))
    for a in rows:
        print("  " + "  ".join("%s=%s" % (k, a.get(k)) for k in CONFIG_KEYS if a.get(k) is not None))
    print()

    for a in rows:
        aid = a.get("id")
        if not aid:
            continue
        dump("/reviews/%s/pairwiseAnalyses/%s/pairwiseDataRows" % (REVIEW, aid))
        dump("/reviews/%s/analyses/%s/results" % (REVIEW, aid))
