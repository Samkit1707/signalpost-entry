"""Dated Brønnøysund change events for one company (Enhetsregisteret update feeds, filtered to its org number).

One feed per company, through the shared 2-per-host cap and paced so that each of the 2 connections waits at least
0.6 s between two update-feed requests:
  /oppdateringer/enheter?organisasjonsnummer=<org>&includeChanges=true   (the main unit; newest page too if > 100)
The runner fetches it last, after all core registry work, and skips it once half of the run deadline has passed.

The feed is JSON Patch: each change gives the field path and the NEW value only. The old value is stated only when an
earlier event in the same feed set that same field (cited from that event); otherwise it is "not stated", never guessed.
Past `hjemmeside` values are never published.
"""
from __future__ import annotations

import re

import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Iterator

from .citations import json_span
from .evidence import evidence
from .http import FetchResult, fetch_json

UNIT_UPDATES = "https://data.brreg.no/enhetsregisteret/api/oppdateringer/enheter?organisasjonsnummer={org}&includeChanges=true&size=100"
MIN_GAP_S = 0.6
TYPES = {"Ny": "registered", "Endring": "changed", "Sletting": "deleted", "Fjernet": "removed", "Ukjent": "updated (type not stated)"}
# Bookkeeping fields the register re-stamps on every employee-count report: not company changes.
NOISE_FIELDS = {"registreringsdatoAntallAnsatteEnhetsregisteret", "registreringsdatoAntallAnsatteNAVAaregisteret"}
# Contact fields of a sole proprietorship are a private person's; not published as events.
PERSONAL_FIELDS = {"epostadresse", "telefon", "mobil"}
# Fields whose values are never published from the change feed: a past website is a candidate only (it still needs the
# org number on the site), and a past e-mail names a domain nobody verified. The dated event is published without values.
VALUE_WITHHELD_FIELDS = {"hjemmeside", "epostadresse"}
FIELD_LABELS = {
    "hjemmeside": "website", "navn": "name", "forretningsadresse": "business address", "postadresse": "postal address",
    "beliggenhetsadresse": "location address", "naeringskode1": "main industry code", "naeringskode2": "second industry code",
    "naeringskode3": "third industry code", "organisasjonsform": "legal form", "antallAnsatte": "registered employees",
    "harRegistrertAntallAnsatte": "employee count registered", "kapital": "share capital", "konkurs": "bankruptcy",
    "underAvvikling": "liquidation", "underTvangsavviklingEllerTvangsopplosning": "compulsory dissolution",
    "epostadresse": "e-mail", "telefon": "phone", "mobil": "mobile", "sisteInnsendteAarsregnskap": "latest filed annual accounts",
    "vedtektsdato": "articles of association date", "vedtektsfestetFormaal": "registered purpose", "aktivitet": "registered activity",
    "registrertIMvaregisteret": "VAT registration", "registrertIForetaksregisteret": "Register of Business Enterprises",
    "registrertIFrivillighetsregisteret": "Register of Non-Profit Organisations", "stiftelsesdato": "founding date",
    "institusjonellSektorkode": "institutional sector", "overordnetEnhet": "parent unit", "oppstartsdato": "start date",
    "slettedato": "deletion date", "fravalgRevisjonDato": "audit opt-out", "maalform": "written language",
    "erIKonsern": "part of a group", "hjelpeenhetskode": "auxiliary unit code", "fravalgRevisjonBeslutningsDato": "audit opt-out decision date",
    "registreringsdatoMerverdiavgiftsregisteret": "VAT registration date",
    "registreringsdatoMerverdiavgiftsregisteretEnhetsregisteret": "VAT registration date (Central Coordinating Register)",
    "registreringsdatoFrivilligMerverdiavgiftsregisteret": "voluntary VAT registration date",
    "frivilligMvaRegistrertBeskrivelser": "voluntary VAT registration", "underAvviklingDato": "liquidation date",
    "registreringsdatoEnhetsregisteret": "registration date", "registreringsdatoForetaksregisteret": "Register of Business Enterprises date",
    "registrertIStiftelsesregisteret": "Register of Foundations", "registrertIPartiregisteret": "Register of Political Parties",
    "konkursdato": "bankruptcy date", "tvangsavvikletPgaManglendeSlettingDato": "compulsory dissolution date",
    "tvangsopplostPgaManglendeDagligLederDato": "compulsory dissolution date", "tvangsopplostPgaManglendeRevisorDato": "compulsory dissolution date",
    "tvangsopplostPgaManglendeRegnskapDato": "compulsory dissolution date", "tvangsopplostPgaMangelfulltStyreDato": "compulsory dissolution date",
}
INDUSTRY = {"naeringskode1": "main industry", "naeringskode2": "second industry", "naeringskode3": "third industry"}
ADDRESS_PARTS = {"adresse": "street", "postnummer": "postcode", "poststed": "town", "kommune": "municipality",
                 "kommunenummer": "municipality number", "land": "country", "landkode": "country code"}


class SlotPacer:
    """At most `slots` requests in flight; each slot starts its next request no sooner than `gap` seconds after its last."""

    def __init__(self, slots: int = 2, gap: float = MIN_GAP_S, clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.gap, self.clock, self.sleeper = gap, clock, sleeper
        self.lock = threading.Lock()
        self.free = threading.Semaphore(slots)
        self.last = [float("-inf")] * slots
        self.busy = [False] * slots

    @contextmanager
    def slot(self) -> Iterator[int]:
        self.free.acquire()
        with self.lock:
            index = min((i for i, busy in enumerate(self.busy) if not busy), key=lambda i: self.last[i])
            self.busy[index] = True
            wait = self.last[index] + self.gap - self.clock()
        try:
            if wait > 0:
                self.sleeper(wait)
            with self.lock:
                self.last[index] = self.clock()
            yield index
        finally:
            with self.lock:
                self.busy[index] = False
            self.free.release()


PACER = SlotPacer()


def paced_fetch(url: str) -> FetchResult:
    with PACER.slot():
        return fetch_json(url, attempts=2)


def feed_date(stamp: Any) -> str | None:
    """The date exactly as the feed timestamp writes it (its first 10 characters, no timezone shift), so the claim's
    date matches the cited JSON character for character."""
    text = str(stamp or "")
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else None


def instant(stamp: Any) -> float:
    """Sort key: the timestamp as a moment in time (the feed mixes `Z` and `+02:00` offsets)."""
    try:
        return datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# Only updates that state a concrete change are published: registered, deleted/removed, or a change that names at
# least one field. An Endring without change details ("record changed") and Ukjent go to the diagnostics file.
WHOLE_RECORD_TYPES = {"Ny", "Sletting", "Fjernet"}


def field_name(path: str) -> str:
    return path.strip("/").replace("/", ".")


def field_label(field: str | None) -> str:
    if not field:
        return "record"
    head = field.split(".")[0]
    label = FIELD_LABELS.get(head, head)
    rest = field.split(".")[1:]
    if head == "kapital" and rest:
        label = {"belop": "share capital", "antallAksjer": "number of shares", "innfortDato": "share capital date",
                 "innbetalt": "paid-in share capital"}.get(rest[0], label)
    elif head in INDUSTRY:
        label = f"{INDUSTRY[head]} {'description' if rest[:1] == ['beskrivelse'] else 'code'}"
    elif head in {"forretningsadresse", "postadresse", "beliggenhetsadresse"} and rest and rest[0] in ADDRESS_PARTS:
        label = f"{label} ({ADDRESS_PARTS[rest[0]]})"
    elif head not in FIELD_LABELS:
        label = f"register field {head}"  # no English label: the register's own field name, stated as such
    return label


def unit_events(body: Any, *, entity: str, organisation_number: str, source: FetchResult, personal: bool,
                list_key: str, skipped: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One event per registration or deletion, and one per named changed field of an Endring, oldest first. Updates that
    state no concrete change are appended to `skipped` (diagnostics), never published."""
    skipped = skipped if skipped is not None else []
    rows = ((body or {}).get("_embedded") or {}).get(list_key) or [] if isinstance(body, dict) else []
    events: list[dict[str, Any]] = []
    seen: dict[str, tuple[Any, str]] = {}
    for index, row in enumerate(rows):
        base = f"$._embedded.{list_key}[{index}]"
        head = [(f"{base}.organisasjonsnummer", row.get("organisasjonsnummer")), (f"{base}.dato", row.get("dato")),
                (f"{base}.endringstype", row.get("endringstype"))]
        common = {"date": feed_date(row.get("dato")), "timestamp": row.get("dato"), "type": TYPES.get(row.get("endringstype"), str(row.get("endringstype"))),
                  "entity": entity, "organisation_number": row.get("organisasjonsnummer") or organisation_number,
                  "source_url": source.url, "content_sha256": source.content_sha256, "retrieved_at": source.retrieved_at}
        changes = row.get("endringer") or []
        kind = row.get("endringstype")
        for number, change in enumerate(changes):  # every change is remembered as an earlier value, even when not published
            path = str(change.get("path") or "")
            if kind in WHOLE_RECORD_TYPES:
                seen[path] = (change.get("value"), json_span([(f"{base}.dato", row.get("dato")),
                                                              (f"{base}.endringer[{number}].op", change.get("op")),
                                                              (f"{base}.endringer[{number}].path", path),
                                                              (f"{base}.endringer[{number}].value", change.get("value"))]))
        if kind in WHOLE_RECORD_TYPES:
            events.append({**common, "field": None, "field_label": "record", "old": None, "old_stated": False, "new": None,
                           "claim_span": json_span(head)})
            continue
        named = [change for change in changes if field_name(str(change.get("path") or ""))]
        if kind != "Endring" or not named:
            skipped.append({"entity": entity, "organisation_number": common["organisation_number"], "timestamp": row.get("dato"),
                            "endringstype": kind, "source_url": source.url, "index": index,
                            "reason": "Ukjent: the feed does not state the type of update" if kind == "Ukjent" else
                                      "the update lists no changed field" if kind == "Endring" else f"update type {kind!r} not published"})
            continue
        published_before = len(events)
        for number, change in enumerate(changes):
            path = str(change.get("path") or "")
            name = field_name(path)
            new = change.get("value")
            pair = [(f"{base}.endringer[{number}].op", change.get("op")), (f"{base}.endringer[{number}].path", path)]
            withheld = name.split(".")[0] in VALUE_WITHHELD_FIELDS
            if "value" in change and not withheld:
                pair.append((f"{base}.endringer[{number}].value", new))
            before = seen.get(path)
            seen[path] = (new, json_span([(f"{base}.dato", row.get("dato")), *pair]))
            if name.split(".")[0] in NOISE_FIELDS or (personal and name.split(".")[0] in PERSONAL_FIELDS):
                continue
            span = json_span(head + pair) + (f"; earlier value: {before[1]}" if before and not withheld else "")
            event = {**common, "field": name, "field_label": field_label(name), "op": change.get("op"),
                     "old": before[0] if before and not withheld else None, "old_stated": before is not None and not withheld,
                     "new": None if withheld else new, "claim_span": span}
            if withheld:
                event["value_note"] = "value not published (used only as a candidate, or names an unverified domain)"
                event["_raw_new"] = new  # kept in the raw profile for candidate discovery; removed from the public profile
            events.append(event)
        if len(events) == published_before:
            skipped.append({"entity": entity, "organisation_number": common["organisation_number"], "timestamp": row.get("dato"),
                            "endringstype": kind, "source_url": source.url, "index": index,
                            "reason": "only registration-date or personal contact fields changed (not published)"})
    return events


def fetch_registry_events(org: str, *, personal: bool,
                          fetcher: Callable[[str], FetchResult] = paced_fetch) -> tuple[dict[str, Any], list[FetchResult]]:
    """The `registry_events` module record and the request metrics."""
    metrics: list[FetchResult] = []
    events: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    errors: list[str] = []
    skipped: list[dict[str, Any]] = []

    def get(url: str) -> FetchResult | None:
        result = fetcher(url)
        metrics.append(result)
        responses.append({"url": url, "status": result.status, "content_sha256": result.content_sha256})
        if result.status != 200:
            errors.append(f"{url}: {result.error or result.status}")
            return None
        return result

    first = get(UNIT_UPDATES.format(org=org))
    if first is not None:
        pages = [first]
        total = ((first.body or {}).get("page") or {}).get("totalPages") or 1
        if total > 1:
            last = get(UNIT_UPDATES.format(org=org) + f"&page={total - 1}")
            if last is not None:
                pages.append(last)
        for page in pages:
            events += unit_events(page.body, entity="main unit", organisation_number=org, source=page, personal=personal,
                                  list_key="oppdaterteEnheter", skipped=skipped)
    events.sort(key=lambda item: (instant(item.get("timestamp")), item.get("field") or ""), reverse=True)
    source_url = UNIT_UPDATES.format(org=org)
    if not events:
        status = "source_error" if errors and len(errors) == len(responses) else "not_found"
        record = evidence("registry_events", status, "official_registry_updates", source_url,
                          note="; ".join(errors)[:300] if status == "source_error" else
                          ("the update feed lists no stated change for this organisation number" +
                           (f" ({len(skipped)} update(s) without a stated change are not published)" if skipped else "")),
                          content_sha256=first.content_sha256 if first is not None else None,
                          retrieved_at=first.retrieved_at if first is not None else None)
    else:
        record = evidence("registry_events", "available", "official_registry_updates", source_url,
                          value={"events": events, "responses": responses, "errors": errors},
                          content_sha256=first.content_sha256 if first is not None else events[0]["content_sha256"],
                          retrieved_at=first.retrieved_at if first is not None else None)
    record["not_published"] = skipped  # diagnostics only; removed from the public profile
    return record, metrics


DEADLINE_SKIP_NOTE = "skipped to protect the run deadline"


def skipped_record(org: str) -> dict[str, Any]:
    """The events module when the run is past half its deadline: not fetched on purpose, stated as not available."""
    return evidence("registry_events", "not_found", "deadline_guard", UNIT_UPDATES.format(org=org), note=DEADLINE_SKIP_NOTE)

