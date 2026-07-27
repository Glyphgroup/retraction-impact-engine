"""Shared HTTP helpers: polite identification, retries, and on-disk caching.

Crossref and OpenAlex both grant better throughput to callers who identify
themselves, so every request carries a contact address in the User-Agent.
Responses are cached on disk because the citation walk revisits the same works
repeatedly and we would rather not hammer free public infrastructure.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CONTACT = os.environ.get("RIE_CONTACT_EMAIL", "retraction.impact.engine@proton.me")
USER_AGENT = "retraction-impact-engine/0.1 (+https://github.com/Glyphgroup/retraction-impact-engine; mailto:%s)" % CONTACT

CACHE_DIR = Path(os.environ.get("RIE_CACHE_DIR", "cache"))
DEFAULT_TIMEOUT = 60
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 2.0

#: Statuses worth retrying. 429 is rate limiting, 5xx are transient.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class HttpError(RuntimeError):
    def __init__(self, status: int | None, url: str, body: bytes = b""):
        self.status = status
        self.url = url
        self.body = body
        super().__init__("%s on %s: %s" % (status, url, body[:300].decode("utf-8", "replace")))


def _cache_path(url: str, namespace: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:32]
    return CACHE_DIR / namespace / (digest + ".json")


def fetch(url: str, *, headers: dict[str, str] | None = None,
          timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """GET a URL with polite headers and retry on transient failure."""
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    h.update(headers or {})
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
                retry_after = e.headers.get("Retry-After")
                delay = float(retry_after) if (retry_after or "").isdigit() else BACKOFF_SECONDS * (2 ** attempt)
                time.sleep(delay)
                last = e
                continue
            raise HttpError(e.code, url, body) from e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(BACKOFF_SECONDS * (2 ** attempt))
                continue
            raise HttpError(None, url, str(e).encode()) from e
    raise HttpError(None, url, str(last).encode())


def fetch_json(url: str, *, namespace: str = "http", cache: bool = True,
               headers: dict[str, str] | None = None,
               timeout: int = DEFAULT_TIMEOUT) -> Any:
    """GET and parse JSON, caching the parsed body on disk."""
    path = _cache_path(url, namespace)
    if cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    payload = json.loads(fetch(url, headers=headers, timeout=timeout))
    if cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def build_url(base: str, **params: Any) -> str:
    clean = {k: v for k, v in params.items() if v is not None}
    return base + ("?" + urllib.parse.urlencode(clean) if clean else "")
