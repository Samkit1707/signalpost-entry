#!/usr/bin/env python3
"""Seeded random sample of published registry_event claims, each shown next to the update-feed entry it came from
(re-fetched from the claim's own source URL; the hash is compared with the one in the evidence).

usage: sample_registry_events.py ENVELOPES.jsonl [...] [--count 10] [--seed N] [--output FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
from datetime import datetime, timezone
import urllib.request
from pathlib import Path

USER_AGENT = "signalpost-entry-audit/1.0 (registry event sample)"


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("envelopes", nargs="+")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261121)
    parser.add_argument("--output")
    args = parser.parse_args()
    pool = []
    for path in args.envelopes:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            env = json.loads(line)
            by_id = {item["id"]: item for item in env.get("evidence") or []}
            for claim in env.get("claims") or []:
                if claim.get("field") == "registry_event" and claim.get("availability") == "available":
                    pool.append((env["organisation_number"], env["profile"].get("name"), claim, by_id[claim["evidence_ids"][0]]))
    sample = random.Random(args.seed).sample(pool, min(args.count, len(pool)))
    bodies: dict[str, tuple[bytes, object]] = {}
    out = []
    for number, (org, name, claim, item) in enumerate(sample, 1):
        url = item["source_url"]
        if url not in bodies:
            time.sleep(0.6)
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}), timeout=30).read()
            bodies[url] = (raw, json.loads(raw))
        raw, body = bodies[url]
        match = re.match(r"\$(?:\._embedded\.(\w+))?\[(\d+)\]", item["claim_span"])
        entry = body["_embedded"][match.group(1)][int(match.group(2))] if match and match.group(1) else body[int(match.group(2))] if match else None
        same = hashlib.sha256(raw).hexdigest() == item["content_sha256"]
        cited = re.search(r'\.(?:dato|time)="([^"]+)"', item["claim_span"])
        stamp = (entry or {}).get("dato") or (entry or {}).get("time")
        entry_org = (entry or {}).get("organisasjonsnummer") or ((entry or {}).get("data") or {}).get("organisasjonsnummer")
        matches = bool(cited and stamp and instant(cited.group(1)) == instant(stamp) and entry_org == claim["value"].get("organisation_number"))
        out.append(f"#{number}  {org} {name}\n  published claim: {json.dumps(claim['value'], ensure_ascii=False)}\n"
                   f"  as_of: {claim.get('as_of')}\n  source: {url}\n  cited span: {item['claim_span']}\n"
                   f"  response hash then {item['content_sha256'][:16]}… now {'identical' if same else 'different'}; "
                   f"entry at the cited index has the same org number and instant: {'yes' if matches else 'NO'}\n"
                   f"  source entry: {json.dumps(entry, ensure_ascii=False)}\n")
    text = "\n".join(out)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
