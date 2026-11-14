#!/usr/bin/env python3
"""Before/after audit page: per-family counts for each batch, every NEW link or registry fact with its proof, and a
seeded random sample of role and financial claims. Also prints the new links as plain text.

usage: build_audit_final.py --pair LABEL BEFORE.jsonl AFTER.jsonl [--pair ...] --output out/audit-final.html
"""
from __future__ import annotations

import argparse
import html
import json
import random
from pathlib import Path
from typing import Any

LINK_FIELDS = ("official_website", "related_website", "external_profile", "dated_news", "job_posting", "careers_page")
FAMILIES = (
    ("Verified website", lambda e: _site(e, verified=True)),
    ("Registry-listed website (site unreadable)", lambda e: _site(e, verified=False)),
    ("Sub-unit websites (related_website)", lambda e: _count(e, "related_website")),
    ("Social profiles (per profile)", lambda e: _count(e, "external_profile")),
    ("Dated news items", lambda e: _count(e, "dated_news")),
    ("Job postings", lambda e: _count(e, "job_posting")),
    ("Careers pages", lambda e: _count(e, "careers_page")),
    ("Registry change events", lambda e: _count(e, "registry_event")),
    ("Roles", lambda e: _count(e, "role")),
    ("Filed figures (per year and figure)", lambda e: sum(_count(e, f) for f in ("annual_revenue", "operating_result", "annual_result", "equity", "total_assets"))),
    ("Published claims", lambda e: sum(1 for c in e["claims"] if c.get("availability") == "available")),
)


def _available(env: dict[str, Any], field: str) -> list[dict[str, Any]]:
    return [c for c in env.get("claims") or [] if c.get("field") == field and c.get("availability") == "available"]


def _count(env: dict[str, Any], field: str) -> int:
    return len(_available(env, field))


def _site(env: dict[str, Any], verified: bool) -> int:
    return sum(1 for c in _available(env, "official_website") if (c.get("website_source") != "registry_record") == verified)


def load(path: str) -> dict[str, dict[str, Any]]:
    return {(row := json.loads(line))["organisation_number"]: row for line in Path(path).read_text().splitlines() if line.strip()}


def link_of(claim: dict[str, Any]) -> str | None:
    value = claim.get("value")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("url") or value.get("source_url")
    return None


def key_of(claim: dict[str, Any]) -> str:
    if claim["field"] == "registry_event":
        v = claim["value"]
        return json.dumps([v.get("date"), v.get("entity"), v.get("organisation_number"), v.get("field"), v.get("new")], ensure_ascii=False)
    return f"{claim['field']}|{(link_of(claim) or '').rstrip('/').casefold()}"


def evidence_html(env: dict[str, Any], claim: dict[str, Any]) -> str:
    by_id = {item["id"]: item for item in env.get("evidence") or []}
    out = []
    for evidence_id in claim.get("evidence_ids") or []:
        item = by_id.get(evidence_id) or {}
        url = item.get("source_url") or ""
        out.append(f'<div class="ev"><a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">Check the evidence ↗</a> '
                   f'<code>{html.escape(evidence_id)}</code> <span class="m">{html.escape(url)}</span><br>'
                   f'<span class="span">{html.escape(str(item.get("claim_span") or ""))[:600]}</span>'
                   f'<br><span class="m">sha256 {html.escape(str(item.get("content_sha256") or ""))} · retrieved {html.escape(str(item.get("retrieved_at") or "")[:10])}'
                   f'{" · read from: " + html.escape(item["read_from"]) if item.get("read_from") else ""}</span></div>')
    return "".join(out)


def describe(claim: dict[str, Any]) -> str:
    value = claim.get("value")
    field = claim["field"]
    if field == "registry_event":
        v = value
        change = f"{v.get('old')} → {v.get('new')}" if v.get("old_stated") else ("value not published" if v.get("value_note") else f"set to {v.get('new')}")
        where = v.get("entity") if v.get("entity") != "sub-unit" else f"sub-unit {v.get('organisation_number')}"
        return f"{v.get('date')} · {v.get('type')} · {where} · {v.get('field_label')}: {change}"
    if field == "related_website":
        return f"{value.get('url')} — {value.get('label')}"
    if field == "official_website":
        return f"{value}" + (" — registry record; site not readable at crawl time" if claim.get("website_source") == "registry_record" else "")
    if field == "external_profile":
        return f"{value.get('platform')}: {value.get('url')}"
    if field == "dated_news":
        return f"{value.get('title')} ({value.get('published_at')}) {value.get('url')}"
    if field == "role":
        return f"{value.get('role')}: {value.get('name')}"
    if isinstance(value, dict) and "amount" in value:
        return f"{field} FY{value.get('fiscal_year')}: {value.get('currency')} {value.get('amount'):,.0f}"
    return json.dumps(value, ensure_ascii=False)[:200]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", nargs=3, action="append", metavar=("LABEL", "BEFORE", "AFTER"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261121)
    args = parser.parse_args()

    tables, new_rows, plain, pool = [], [], [], []
    totals: dict[str, int] = {}
    for label, before_path, after_path in args.pair:
        before, after = load(before_path), load(after_path)
        rows = ""
        for name, count in FAMILIES:
            b = sum(count(env) for env in before.values())
            a = sum(count(env) for env in after.values())
            cb = sum(1 for env in before.values() if count(env))
            ca = sum(1 for env in after.values() if count(env))
            rows += f"<tr><td>{html.escape(name)}</td><td>{b}</td><td>{a}</td><td>{a - b:+d}</td><td>{cb}</td><td>{ca}</td></tr>"
        tables.append(f"<h2>{html.escape(label)}: before (44c988f) vs after</h2><table><thead><tr><th>Family</th><th>Before</th><th>After</th>"
                      f"<th>Δ</th><th>Companies before</th><th>Companies after</th></tr></thead><tbody>{rows}</tbody></table>")
        for org, env in after.items():
            old = {key_of(c) for c in (before.get(org) or {}).get("claims") or [] if c.get("availability") == "available"}
            for claim in env["claims"]:
                if claim.get("availability") != "available":
                    continue
                if claim["field"] in {"role", "annual_revenue", "annual_result", "equity", "operating_result"}:
                    pool.append((label, env, claim))
                if claim["field"] not in (*LINK_FIELDS, "registry_event") or key_of(claim) in old:
                    continue
                totals[claim["field"]] = totals.get(claim["field"], 0) + 1
                new_rows.append((label, env, claim))
                if claim["field"] != "registry_event":
                    plain.append(f"{label}\t{org}\t{env['profile'].get('name')}\t{claim['field']}\t{link_of(claim)}")

    def row(label: str, env: dict[str, Any], claim: dict[str, Any]) -> str:
        proof = claim.get("proof") or claim.get("note") or ""
        return (f"<tr><td>{html.escape(label)}</td><td>{html.escape(env['organisation_number'])}<br>{html.escape(str(env['profile'].get('name')))}</td>"
                f"<td>{html.escape(claim['field'])}<br><span class=m>{html.escape(str(claim.get('confidence_level') or ''))}</span></td>"
                f"<td>{html.escape(describe(claim))}<br><span class=m>{html.escape(proof)[:400]}</span></td><td>{evidence_html(env, claim)}</td></tr>")

    links = [r for r in new_rows if r[2]["field"] != "registry_event"]
    events = [r for r in new_rows if r[2]["field"] == "registry_event"]
    random.Random(args.seed).shuffle(pool)
    sample = pool[:args.sample]
    head = "<thead><tr><th>Batch</th><th>Company</th><th>Field</th><th>Value and proof</th><th>Evidence</th></tr></thead>"
    by_company: dict[tuple[str, str], list] = {}
    for item in events:
        by_company.setdefault((item[0], item[1]["organisation_number"]), []).append(item)
    event_html = "".join(
        f"<details><summary>{html.escape(label)} · {html.escape(org)} {html.escape(str(items[0][1]['profile'].get('name')))} — {len(items)} events</summary>"
        f"<table>{head}<tbody>{''.join(row(*item) for item in items)}</tbody></table></details>"
        for (label, org), items in by_company.items())
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Final audit</title><style>
body{{font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px;background:#f7f5ef;color:#181713}}table{{border-collapse:collapse;width:100%;background:#fff;margin:8px 0 20px}}
th,td{{border:1px solid #ddd5c6;padding:6px 8px;vertical-align:top;text-align:left}}th{{background:#efe9dc}}.m{{color:#5f5b53;font-size:12px}}
.span{{font-family:ui-monospace,monospace;font-size:11px;overflow-wrap:anywhere}}a{{color:#7d2432;display:inline-flex;min-height:44px;align-items:center}}
code{{font-size:11px}}details{{background:#fff;border:1px solid #ddd5c6;margin:4px 0;padding:0 8px}}summary{{min-height:44px;display:flex;align-items:center;cursor:pointer}}
.scroll{{overflow-x:auto}}</style></head><body>
<h1>Final audit — new links and registry facts, with proof</h1>
<p>Every link or registry event published by the new code that the 44c988f run on the same companies did not publish. Each row has the
claim, its confidence and proof, and the evidence: the exact source (opens the per-company record or page), the cited span and the
response hash. New counts: {html.escape(json.dumps(totals))}.</p>
{''.join(tables)}
<h2>New links ({len(links)})</h2><div class="scroll"><table>{head}<tbody>{''.join(row(*item) for item in links)}</tbody></table></div>
<h2>Random sample: {len(sample)} role and financial claims (seed {args.seed})</h2><div class="scroll"><table>{head}<tbody>{''.join(row(*item) for item in sample)}</tbody></table></div>
<h2>New registry change events ({len(events)}, grouped by company)</h2>{event_html}
</body></html>"""
    Path(args.output).write_text(page, encoding="utf-8")
    print("\n".join(plain))
    print(json.dumps({"new": totals, "links": len(links), "events": len(events), "sample": len(sample)}))


if __name__ == "__main__":
    main()
