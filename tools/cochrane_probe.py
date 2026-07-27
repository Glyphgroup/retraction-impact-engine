"""Probe the authenticated Cochrane production API.

Reads the bearer token from the COCHRANE_TOKEN environment variable. The token
is a short-lived Keycloak JWT and must never be written to a file in this repo,
which is public.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.cochrane.org"
TOKEN = os.environ.get("COCHRANE_TOKEN", "").removeprefix("Bearer ").strip()


def claims() -> dict:
    payload = TOKEN.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def get(path: str, limit: int = 1500):
    url = path if path.startswith("http") else BASE + path
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + TOKEN,
        "Accept": "application/json",
        "User-Agent": "retraction-impact-engine/0.1 (retraction.impact.engine@proton.me)",
    })
    try:
        r = urllib.request.urlopen(req, timeout=45)
        return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:limit], dict(e.headers)
    except Exception as e:
        return None, ("ERR %s %s" % (type(e).__name__, e)).encode(), {}


def show(path: str, limit: int = 1200):
    st, body, _ = get(path)
    print("\n%-58s -> %s" % (path, st))
    try:
        parsed = json.loads(body)
    except Exception:
        print("   ", body[:limit])
        return None
    print("   ", json.dumps(parsed)[:limit])
    return parsed


if __name__ == "__main__":
    if not TOKEN:
        sys.exit("COCHRANE_TOKEN is not set")
    c = claims()
    left = c["exp"] - time.time()
    print("token: %s <%s>  client=%s" % (c.get("name"), c.get("email"), c.get("azp")))
    print("expires in %.1f minutes" % (left / 60))
    if left <= 0:
        sys.exit("token has expired; fetch a new one from RevMan Web")

    for path in sys.argv[1:] or [
        "/reviews?per_page=1",
        "/reviews?per_page=3&metadata=true",
        "/reviews?myReviews=true&per_page=1",
        "/reviews/templates",
    ]:
        show(path)
