"""Dump real Cochrane API response bodies so the mapper can be written from fact.

Writes each response to data/cochrane/<slug>.json (gitignored). Reads the bearer
token from COCHRANE_TOKEN; the token is never written to disk.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from cochrane_probe import claims, get

OUT = Path("../data/cochrane")

REVIEW = "214326072721231561"
GROUP = "443254919993746113"
STUDY = "443255173517140757"

STUDY_EXPAND = (
    "references[expand=referenceIdentifiers],"
    "studyResults[expand=studyArmResults,studyResultType[expand=outcome],referenceArm]"
)

PATHS = [
    "/reviews?sort=title&page=1&per_page=max&expand=contact,creators&myPracticeReviews=true",
    "/reviews/%s" % REVIEW,
    "/reviews/%s/analysisGroups" % REVIEW,
    "/reviews/%s/analysisGroups/%s/analyses" % (REVIEW, GROUP),
    "/reviews/%s/analysisGroups/%s/pairwiseAnalyses" % (REVIEW, GROUP),
    "/reviews/%s/pairwiseAnalyses" % REVIEW,
    "/reviews/%s/analyses" % REVIEW,
    "/reviews/%s/studies/included" % REVIEW,
    "/reviews/%s/outcomes" % REVIEW,
    "/reviews/%s/interventions" % REVIEW,
    "/reviews/%s/studies/%s?expand=%s" % (REVIEW, STUDY, STUDY_EXPAND),
]


def slug(path: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", path.strip("/"))
    return s[:110]


def dump(path: str) -> object | None:
    status, body, _ = get(path)
    label = "%-3s %s" % (status, path if len(path) < 96 else path[:93] + "...")
    if status != 200 and status != 206:
        print(label, "->", body[:200])
        return None
    try:
        parsed = json.loads(body)
    except Exception:
        print(label, "-> non-JSON", body[:120])
        return None
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / (slug(path) + ".json")
    target.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    print(label, "->", target.name, "(%d bytes)" % target.stat().st_size)
    return parsed


if __name__ == "__main__":
    c = claims()
    left = c["exp"] - time.time()
    print("token for %s, %.1f minutes remaining\n" % (c.get("email"), left / 60))
    if left <= 0:
        sys.exit("TOKEN EXPIRED (exp=%d, now=%d). Fetch a fresh one from RevMan Web."
                 % (c["exp"], int(time.time())))
    for p in sys.argv[1:] or PATHS:
        dump(p)
