from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import urllib3

from .politeness import MAX_PER_HOST, host_slot


@dataclass
class FetchResult:
    url: str
    status: int
    elapsed_ms: int
    bytes_received: int
    body: Any = None
    error: str | None = None
    content_sha256: str | None = None
    retrieved_at: str | None = None
    effective_at: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# Keep-alive pool: brreg calls reuse TLS connections (~0.2 s instead of ~0.9 s per call on a slow link).
# Pool size matches the per-host politeness cap, so idle connections never exceed it either.
# urllib3 is pinned in uv.lock (2.7.0) through trafilatura's dependency tree.
_POOL = urllib3.PoolManager(maxsize=MAX_PER_HOST, block=True, retries=urllib3.Retry(total=False, connect=0, read=0, status=0, other=0, redirect=5))


def fetch_json(url: str, *, timeout: float = 20.0, attempts: int = 3) -> FetchResult:
    last_error = "request failed"
    for attempt in range(attempts):
        started = time.monotonic()
        try:
            with host_slot(url) as started:
                response = _POOL.request(
                    "GET",
                    url,
                    headers={"Accept": "application/json", "User-Agent": "builderr-signalpost-poc/0.1 (+https://builderr.ai)"},
                    timeout=urllib3.Timeout(connect=timeout, read=timeout),
                )
            raw = response.data
            elapsed = int((time.monotonic() - started) * 1000)
            if response.status == 200:
                return FetchResult(url, response.status, elapsed, len(raw), json.loads(raw), content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now())
            if response.status in {404, 410}:
                return FetchResult(url, response.status, elapsed, len(raw), error=f"HTTP {response.status}", content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now())
            last_error = f"HTTP {response.status}"
        except (urllib3.exceptions.HTTPError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
        if attempt + 1 < attempts:
            time.sleep(0.4 * (2**attempt))
    return FetchResult(url, 0, 0, 0, error=last_error, retrieved_at=_utc_now())
