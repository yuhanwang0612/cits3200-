"""HTTP with a disk cache and retry.

A full run is roughly 800 requests and fifteen minutes. Caching each
response means re-running to change *parsing* costs seconds instead, which
is most of what you actually iterate on.

The cache never expires. Pass force=True, set FORCE_REFRESH, or delete
cache/http/ to get fresh data.
"""

import hashlib
import json
import time

import requests

from core.config import CACHE_DIR

FORCE_REFRESH = False           # set True to bypass the cache everywhere


def _key(url, params):
    raw = url + json.dumps(params or {}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def cached_get(url, params=None, headers=None, timeout=30,
               tries=3, backoff=5, sleep=0.0, force=False, allow_404=False):
    """GET returning parsed JSON, cached on disk by url + params.

    sleep is applied only after a real request, so a cached run does not
    spend minutes asleep. Returns None on a 404 when allow_404 is set —
    some journals genuinely have no report for a given year.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_key(url, params)}.json"

    if path.exists() and not (force or FORCE_REFRESH):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    last = None
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 404 and allow_404:
                return None
            if r.status_code == 429:            # rate limited — always worth a wait
                time.sleep(backoff * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            if sleep:
                time.sleep(sleep)
            return data
        except (requests.RequestException, ValueError) as e:
            last = e
            if attempt < tries - 1:
                time.sleep(backoff * (attempt + 1))

    raise last


def cache_stats():
    if not CACHE_DIR.exists():
        return 0, 0.0
    files = list(CACHE_DIR.glob("*.json"))
    mb = sum(f.stat().st_size for f in files) / 1e6
    return len(files), round(mb, 1)
