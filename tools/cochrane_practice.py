"""Obtain a readable Cochrane review: list practice copies, or create one from a template.

RevMan's example reviews are readable only as a practice copy owned by the
caller. POST /reviews?copyOf=<id>&deepCopy=true is the documented mechanism.
Any review created here is deletable with DELETE /reviews/<id>.
"""
from __future__ import annotations

import json
import sys
import urllib.request

from cochrane_probe import BASE, TOKEN, get, show

ASTHMA = "431723072513484702"   # "Inhaled corticosteroids for asthma", study-centric data
CAFFEINE = "191718052509081627"  # "Caffeine for daytime drowsiness", manual data entry


def post(path: str, body: dict | None = None):
    url = BASE + path
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "retraction-impact-engine/0.1 (retraction.impact.engine@proton.me)",
    })
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, r.read()
    except Exception as e:
        body = e.read()[:800] if hasattr(e, "read") else str(e).encode()
        return getattr(e, "code", None), body


if __name__ == "__main__":
    if not TOKEN:
        sys.exit("COCHRANE_TOKEN is not set")

    print("=== existing reviews visible to this account ===")
    for q in ("?myReviews=true&per_page=20", "?myPracticeReviews=true&per_page=20"):
        show("/reviews" + q, limit=2500)

    if "--create" in sys.argv:
        source = CAFFEINE if "--caffeine" in sys.argv else ASTHMA
        print("\n=== creating practice copy of %s ===" % source)
        st, body = post("/reviews?copyOf=%s&deepCopy=true" % source)
        print(st, body[:1200])


def try_variants(source: str, practice_key: str):
    """The spec advertises ?copyOf= but the server rejects that parameter name.

    Try the plausible alternatives before concluding the copy route is closed.
    """
    attempts = [
        ("?deepCopy=true", {"sourceReviewId": source, "practiceKey": practice_key,
                            "title": "RIE validation copy"}),
        ("", {"sourceReviewId": source, "practiceKey": practice_key,
              "title": "RIE validation copy"}),
        ("", {"practiceKey": practice_key, "title": "RIE validation copy"}),
        ("?practiceKey=%s" % practice_key, {"title": "RIE validation copy"}),
        ("?sourceReviewId=%s" % source, {"title": "RIE validation copy"}),
    ]
    for query, body in attempts:
        st, resp = post("/reviews" + query, body)
        print("POST /reviews%-40s %s %s" % (query, st, resp[:220]))
