"""Probe the Cochrane test API: base path discovery and auth requirements."""
import json
import sys
import urllib.error
import urllib.request

BASE = "https://test-api.cochrane.org"
UA = {"User-Agent": "retraction-impact-engine/0.0 (probe)"}


def get(url, headers=None, limit=1200):
    h = dict(UA)
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        body = r.read()
        return r.status, dict(r.headers), body[:limit]
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()[:limit]
    except Exception as e:
        return None, {}, ("ERR %s %s" % (type(e).__name__, e)).encode()


def show(url, headers=None):
    st, hd, body = get(url, headers)
    print("\n%s  ->  %s  %s" % (url, st, hd.get("Content-Type")))
    if hd.get("WWW-Authenticate"):
        print("  WWW-Authenticate:", hd["WWW-Authenticate"])
    if hd.get("Location"):
        print("  Location:", hd["Location"])
    try:
        print("  ", json.dumps(json.loads(body))[:900])
    except Exception:
        print("  ", body[:900])


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for u in sys.argv[1:]:
            show(u if u.startswith("http") else BASE + u)
    else:
        for p in ["", "/reviews", "/reviews?per_page=5", "/api/reviews", "/v1/reviews",
                  "/rest/reviews", "/reviews/templates"]:
            show(BASE + p)
