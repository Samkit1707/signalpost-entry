"""Per-host concurrency cap shared by every outbound request.

Raising --workers speeds up a network-bound batch, but no single server (a small company site,
data.brreg.no, query.wikidata.org) ever sees more than MAX_PER_HOST requests in flight from us.
"""
from __future__ import annotations

import threading
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager

MAX_PER_HOST = 2

_LOCK = threading.Lock()
_SLOTS: dict[str, threading.BoundedSemaphore] = {}


def host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")


@contextmanager
def host_slot(url: str) -> Iterator[float]:
    """Hold one of the host's MAX_PER_HOST request slots for the duration of the block.

    Yields the monotonic time the slot was acquired, so latency metrics exclude our own queueing.
    """
    host = host_of(url)
    with _LOCK:
        slot = _SLOTS.get(host)
        if slot is None:
            slot = _SLOTS[host] = threading.BoundedSemaphore(MAX_PER_HOST)
    with slot:
        yield time.monotonic()
