"""Determine which Authorization scheme test-api.cochrane.org expects."""
import base64
from api_probe import get, BASE

CASES = [
    ("empty-ish", {"Authorization": "x"}),
    ("bearer-bogus", {"Authorization": "Bearer notarealtoken"}),
    ("basic-bogus", {"Authorization": "Basic " + base64.b64encode(b"user:pass").decode()}),
    ("apikey-bogus", {"Authorization": "ApiKey notarealtoken"}),
    ("token-bogus", {"Authorization": "Token notarealtoken"}),
]

for name, hdrs in CASES:
    st, hd, body = get(BASE + "/reviews?per_page=1", hdrs)
    print("%-14s -> %s %s" % (name, st, body[:250]))
