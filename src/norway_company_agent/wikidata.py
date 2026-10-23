"""Wikidata bulk index keyed by Norwegian organisation number (P2333).

One SPARQL request per batch (or zero with a declared cache). Wikidata content is CC0.
Identity is exact: an item is used only when its P2333 equals the input organisation number
and no other item carries the same number.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .citations import json_span
from .evidence import evidence, utc_now
from .politeness import host_slot

SPARQL_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "signalpost-entry/0.2 (Builderr Signalpost challenge entry)"

SELECT_CLAUSE = """
SELECT ?item ?orgnr ?website ?x ?facebook ?instagram ?linkedin ?youtube ?tiktok ?nowiki ?enwiki WHERE {
  %VALUES%
  ?item wdt:P2333 ?orgnr .
  OPTIONAL { ?item wdt:P856 ?website . }
  OPTIONAL { ?item wdt:P2002 ?x . }
  OPTIONAL { ?item wdt:P2013 ?facebook . }
  OPTIONAL { ?item wdt:P2003 ?instagram . }
  OPTIONAL { ?item wdt:P4264 ?linkedin . }
  OPTIONAL { ?item wdt:P2397 ?youtube . }
  OPTIONAL { ?item wdt:P7085 ?tiktok . }
  OPTIONAL { ?nowiki schema:about ?item ; schema:isPartOf <https://no.wikipedia.org/> . }
  OPTIONAL { ?enwiki schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }
}
"""
QUERY = SELECT_CLAUSE.replace("%VALUES%", "")
CHUNK = 200


def batch_query(orgs: list[str]) -> str:
    # P2333 is normally stored as 9 plain digits; also match the spaced form some items use.
    values = " ".join(f'"{org}" "{org[:3]} {org[3:6]} {org[6:]}"' for org in orgs)
    return SELECT_CLAUSE.replace("%VALUES%", "VALUES ?orgnr { " + values + " }")


PROFILE_URLS = {
    "x": "https://x.com/{}",
    "facebook": "https://www.facebook.com/{}",
    "instagram": "https://www.instagram.com/{}/",
    "linkedin": "https://www.linkedin.com/company/{}/",
    "youtube": "https://www.youtube.com/channel/{}",
    "tiktok": "https://www.tiktok.com/@{}",
}


def _norm_orgnr(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _run(query: str, timeout: float) -> tuple[dict[str, Any], bytes]:
    # POST: a 200-org VALUES block is ~10 KB, which the service rejects as a GET URL (HTTP 431).
    body = urllib.parse.urlencode({"query": query, "format": "json"}).encode("utf-8")
    req = urllib.request.Request(SPARQL_URL, data=body, method="POST", headers={
        "User-Agent": USER_AGENT, "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with host_slot(SPARQL_URL), urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw), raw


def fetch_raw(cache_path: Path | None = None, timeout: float = 60.0, orgs: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (sparql_json, metrics).

    With ``orgs`` the query is restricted to those organisation numbers in chunks of 200
    (about 5 requests for 1,000 companies). Without ``orgs`` it runs one full P2333 query.
    A fresh cache file is reused with zero requests.
    """
    if cache_path and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        wanted = set(orgs or [])
        fresh_format = bool(cached.get("_meta", {}).get("responses"))  # v4: one hash per SPARQL response
        if fresh_format and (not wanted or wanted.issubset(set(cached.get("_meta", {}).get("orgs") or [])) or cached.get("_meta", {}).get("mode") == "full"):
            return cached, {"requests": 0, "cached": True}
    bindings: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    responses: list[str] = []
    requests = 0

    def keep(body: dict[str, Any], raw: bytes) -> None:
        # Each binding remembers which response it came from and its index there, so a claim cites one response's hash.
        digest.update(raw)
        responses.append(hashlib.sha256(raw).hexdigest())
        for position, row in enumerate(body.get("results", {}).get("bindings", [])):
            bindings.append({**row, "_response": len(responses) - 1, "_index": position})

    if orgs:
        unique = sorted(set(orgs))
        for index in range(0, len(unique), CHUNK):
            body, raw = _run(batch_query(unique[index:index + CHUNK]), timeout)
            requests += 1
            keep(body, raw)
        mode = "batch"
    else:
        body, raw = _run(QUERY, timeout * 2)
        requests += 1
        keep(body, raw)
        mode = "full"
    data = {"results": {"bindings": bindings},
            "_meta": {"retrieved_at": utc_now(), "content_sha256": digest.hexdigest(), "responses": responses, "source_url": SPARQL_URL,
                      "mode": mode, "orgs": sorted(set(orgs or []))}}
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data, {"requests": requests, "cached": False}


def build_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """orgnr -> merged record. Drops org numbers claimed by more than one Wikidata item (ambiguous)."""
    by_org: dict[str, dict[str, Any]] = {}
    items_per_org: dict[str, set[str]] = {}
    for row in data.get("results", {}).get("bindings", []):
        org = _norm_orgnr(row.get("orgnr", {}).get("value", ""))
        if len(org) != 9:
            continue
        item = row["item"]["value"]
        items_per_org.setdefault(org, set()).add(item)
        rec = by_org.setdefault(org, {"item": item, "website": set(), "profiles": {}, "wikipedia": set(), "spans": {}})
        where = (row.get("_response"), row.get("_index"))
        path = f"$.results.bindings[{where[1]}]"
        id_pairs = [(f"{path}.item.value", item), (f"{path}.orgnr.value", row.get("orgnr", {}).get("value"))]
        rec["spans"].setdefault("item", (where, id_pairs))
        if "website" in row:
            rec["website"].add(row["website"]["value"])
            rec["spans"].setdefault("website:" + row["website"]["value"], (where, id_pairs + [(f"{path}.website.value", row["website"]["value"])]))
        for key in PROFILE_URLS:
            if key in row:
                rec["profiles"].setdefault(key, set()).add(row[key]["value"])
                rec["spans"].setdefault(f"{key}:{row[key]['value']}", (where, id_pairs + [(f"{path}.{key}.value", row[key]["value"])]))
        for key in ("nowiki", "enwiki"):
            if key in row:
                rec["wikipedia"].add(row[key]["value"])
                rec["spans"].setdefault(f"wiki:{row[key]['value']}", (where, id_pairs + [(f"{path}.{key}.value", row[key]["value"])]))
    for org, items in items_per_org.items():
        if len(items) > 1:
            by_org[org]["ambiguous"] = True
    return by_org


def evidence_for(orgnr: str, index: dict[str, dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    """Return an evidence record for module 'wikidata_footprint'."""
    rec = index.get(orgnr)
    retrieved = meta.get("retrieved_at")
    responses = meta.get("responses") or []
    sha = responses[0] if len(responses) == 1 else None  # a combined digest of several responses is no response's hash

    def cite(key: str) -> dict[str, Any]:
        (response, _), pairs = rec["spans"][key]
        return {"claim_span": json_span(pairs),
                "content_sha256": responses[response] if isinstance(response, int) and response < len(responses) else None}

    if not rec:
        return evidence("wikidata_footprint", "not_found", "wikidata_org_number_match", SPARQL_URL,
                        note="No Wikidata item with P2333 equal to this organisation number",
                        retrieved_at=retrieved, content_sha256=sha)
    item_url = rec["item"]
    if rec.get("ambiguous"):
        return evidence("wikidata_footprint", "not_applicable", "wikidata_org_number_match", item_url,
                        note="ambiguous: several Wikidata items carry this organisation number",
                        retrieved_at=retrieved, content_sha256=sha)
    item_cite = cite("item")
    value = {
        "wikidata_item": item_url,
        "identity_proof": f"Wikidata P2333 = {orgnr}",
        "claim_span": item_cite["claim_span"],
        "official_websites": sorted(rec["website"]),
        "website_sources": {site: cite("website:" + site) for site in sorted(rec["website"])},
        "profiles": [
            {"platform": p, "handle": h, "url": PROFILE_URLS[p].format(h), "proof": "listed on exact-match Wikidata item", **cite(f"{p}:{h}")}
            for p, handles in sorted(rec["profiles"].items()) for h in sorted(handles)
        ],
        "wikipedia": sorted(rec["wikipedia"]),
        "sitelink_sources": {page: cite("wiki:" + page) for page in sorted(rec["wikipedia"])},
    }
    return evidence("wikidata_footprint", "available", "wikidata_org_number_match", SPARQL_URL,
                    value=value, retrieved_at=retrieved, content_sha256=item_cite["content_sha256"], source_row_key=orgnr,
                    note=f"Wikidata item {item_url}")
