"""Brønnøysund bulk entity snapshot: use a supplied file, or download it once into cache/.

The evaluator gets only a clone, `uv sync` and one command, so the runner fetches the official open-data
bulk CSV (gzip, NLOD 2.0) itself when no snapshot is supplied. The download is one request per attempt.
"""
from __future__ import annotations

import gzip
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .politeness import host_slot

BULK_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"
USER_AGENT = "signalpost-entry/0.3 (Builderr Signalpost challenge entry)"
CHUNK = 1 << 20


class BulkUnavailable(RuntimeError):
    """The snapshot could not be obtained within its attempts or time budget; the run continues without it."""

    def __init__(self, message: str, requests: int):
        super().__init__(message)
        self.requests = requests


def _is_complete_gzip(path: Path) -> bool:
    try:
        with gzip.open(path, "rb") as handle:
            while handle.read(CHUNK * 8):
                pass
        return True
    except (OSError, EOFError):
        return False


def _download(target: Path, timeout: float, opener: Callable[..., Any], deadline_at: float | None) -> int:
    partial = target.with_name(target.name + ".part")
    request = urllib.request.Request(BULK_URL, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    received = 0
    with host_slot(BULK_URL), opener(request, timeout=timeout) as response, partial.open("wb") as handle:
        expected = int(response.headers.get("Content-Length") or 0)
        while chunk := response.read(CHUNK):
            handle.write(chunk)
            received += len(chunk)
            if deadline_at is not None and time.monotonic() > deadline_at:
                raise TimeoutError(f"bulk download exceeded its time budget after {received} bytes")
    if expected and received != expected:
        raise OSError(f"bulk download truncated: {received} of {expected} bytes")
    with partial.open("rb") as handle:
        if handle.read(2) != b"\x1f\x8b":
            raise OSError("bulk download is not gzip data")
    # Without a Content-Length, only a full decompression proves the transfer was not cut short.
    if not expected and not _is_complete_gzip(partial):
        raise OSError("bulk download is not a complete gzip file")
    partial.replace(target)
    return received


def ensure_bulk(
    supplied: str | None,
    cache_path: str | Path,
    *,
    max_age_hours: float = 24.0,
    attempts: int = 3,
    timeout: float = 120.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    deadline_at: float | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Return (snapshot path, report). Uses the supplied file, a fresh cached copy, or downloads one.

    ``deadline_at`` (time.monotonic) bounds the whole download, so a slow link cannot eat the run's time
    limit. When every attempt fails or the budget runs out, an older cached snapshot is used if one exists
    (report source ``stale_cache``); otherwise BulkUnavailable is raised and the caller continues with the live
    registry per company.
    """
    if supplied and Path(supplied).is_file():
        return Path(supplied), {"source": "supplied", "path": str(supplied), "requests": 0}
    cache = Path(cache_path)
    note = {"supplied_missing": supplied} if supplied else {}
    if cache.is_file() and (time.time() - cache.stat().st_mtime) < max_age_hours * 3600:
        return cache, {"source": "cache", "path": str(cache), "requests": 0, "url": BULK_URL, **note}
    cache.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    errors = []
    used = 0
    for attempt in range(1, attempts + 1):
        if deadline_at is not None and time.monotonic() >= deadline_at:
            errors.append("time budget exhausted before this attempt")
            break
        used = attempt
        print(f"Downloading Brønnøysund bulk registry (attempt {attempt}/{attempts}) -> {cache}", file=sys.stderr, flush=True)
        try:
            if deadline_at is not None:
                timeout = max(5.0, min(timeout, deadline_at - time.monotonic()))
            received = _download(cache, timeout, opener, deadline_at)
            return cache, {"source": "downloaded", "path": str(cache), "url": BULK_URL, "requests": attempt,
                           "bytes": received, "elapsed_ms": int((time.monotonic() - started) * 1000),
                           "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **note}
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
            if attempt < attempts:
                sleep(5 * attempt)
    if cache.is_file():
        # An older complete snapshot beats none: a cached file only ever exists after a full, verified transfer
        # (downloads land in .part and are renamed when complete). Identity checks are unchanged; per-company live
        # registry calls still report current status.
        age_hours = round((time.time() - cache.stat().st_mtime) / 3600, 1)
        print(f"Bulk download failed; using the cached snapshot from {age_hours} h ago ({cache})", file=sys.stderr, flush=True)
        return cache, {"source": "stale_cache", "path": str(cache), "url": BULK_URL, "requests": used, "age_hours": age_hours,
                       "download_errors": errors, "elapsed_ms": int((time.monotonic() - started) * 1000), **note}
    raise BulkUnavailable(f"Could not download the Brønnøysund bulk registry from {BULK_URL}: {errors}", used)
