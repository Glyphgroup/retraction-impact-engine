"""Run the reproduce-gate against every cached Cochrane analysis.

Offline: reads the dumped JSON in data/cochrane, so no token is needed. This is
the measurement that decides whether the engine can reproduce RevMan.
"""
from __future__ import annotations

import json
from pathlib import Path

from rie.gate import Verdict, check
from rie.sources import cochrane

DATA = Path("data/cochrane")
REVIEW = "214326072721231561"


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def main() -> None:
    analyses = cochrane.unwrap(
        load("reviews_%s_pairwiseAnalyses.json" % REVIEW), "PairwiseAnalyses", "Analyses")
    analyses.sort(key=lambda a: a.get("number") or 0)

    passed = mismatched = unverifiable = 0
    rows = []

    for a in analyses:
        aid = str(a["id"])
        config = cochrane.parse_config(a)
        source = a.get("dataSource") or cochrane.ARM_ONLY
        try:
            raw_rows = load("reviews_%s_pairwiseAnalyses_%s_pairwiseDataRows.json" % (REVIEW, aid))
            raw_res = load("reviews_%s_analyses_%s_results.json" % (REVIEW, aid))
        except FileNotFoundError:
            continue

        studies, unusable = cochrane.parse_data_rows(raw_rows, config, source)
        published = cochrane.parse_results(raw_res)
        report = check(studies, config, published)

        if report.verdict is Verdict.REPRODUCED:
            passed += 1
        elif report.verdict is Verdict.MISMATCH:
            mismatched += 1
        else:
            unverifiable += 1

        rows.append((a, config, source, studies, unusable, published, report))

    width = 96
    print("=" * width)
    print("REPRODUCE-GATE against RevMan, review %s" % REVIEW)
    print("=" * width)
    for a, config, source, studies, unusable, published, report in rows:
        mark = {"reproduced": "PASS", "mismatch": "FAIL", "unverifiable": "SKIP"}[report.verdict.value]
        print("\n[%s] %2s. %s" % (mark, a.get("number"), (a.get("name") or "")[:74]))
        print("      %s / %s / %s%s   dataSource=%s" % (
            config.method.value, config.model.value, config.effect_measure.value,
            "/" + config.tau_estimator.value if config.model.value == "RANDOM" else "",
            source))
        print("      studies extracted=%d  rows without usable numbers=%d  source k=%d"
              % (len(studies), len(unusable), published.k))
        if report.published_estimate is not None and report.recomputed_estimate is not None:
            print("      theirs=%+.10f   ours=%+.10f   diff=%+.3e   tol=%.0e"
                  % (report.published_estimate, report.recomputed_estimate,
                     report.difference, report.tolerance))
            if config.is_ratio:
                print("      display: theirs=%.4f  ours=%.4f"
                      % (report.diagnostics.get("published_ratio", float("nan")),
                         report.diagnostics.get("recomputed_ratio", float("nan"))))
            for key in ("se_difference", "q_difference", "i_squared_difference",
                        "tau_squared_difference"):
                if key in report.diagnostics:
                    print("      %-24s %+.3e" % (key, report.diagnostics[key]))
        if not report.passed:
            print("      reason: %s" % report.reason)

    total = len(rows)
    print("\n" + "=" * width)
    print("reproduced %d / %d   mismatch %d   unverifiable %d"
          % (passed, total, mismatched, unverifiable))
    if total:
        print("reproduce rate on gateable analyses: %.0f%%"
              % (100.0 * passed / max(passed + mismatched, 1)))
    print("=" * width)


if __name__ == "__main__":
    main()
