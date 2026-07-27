"""Dump each included study's covariate assignments (input-side membership data)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from cochrane_dump import REVIEW, dump
from cochrane_probe import claims, get
import time

sys.path.insert(0, "..")

DATA = Path("../data/cochrane")


def main() -> None:
    left = claims()["exp"] - time.time()
    print("token %.1f minutes remaining\n" % (left / 60))
    if left <= 0:
        sys.exit("TOKEN EXPIRED")

    included = json.loads(
        (DATA / ("reviews_%s_studies_included.json" % REVIEW)).read_text(encoding="utf-8"))
    studies = included.get("Studies") or included.get("studies") or []
    if isinstance(included, list):
        studies = included
    print("included studies: %d" % len(studies))
    for s in studies:
        sid = str(s.get("id"))
        print("  %s  %s" % (sid, s.get("name")))
        dump("/reviews/%s/studies/%s/studyCovariateValues" % (REVIEW, sid))


if __name__ == "__main__":
    main()
