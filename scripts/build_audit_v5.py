"""Hand-check page: every published website, profile, news item and job link with its proof and confidence, weakest first.

    uv run python scripts/build_audit_v5.py --input out/v5-unseen-30/envelopes.jsonl --label unseen-30 \
        --input out/v5-unseen-30-websites/envelopes.jsonl --label unseen-30-websites --output out/audit-v5.html
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.confidence import link_report  # noqa: E402

KINDS = {"official_website": "website", "external_profile": "social", "dated_news": "news", "company_activity": "news",
         "careers_page": "jobs", "job_posting": "jobs"}
ORDER = {"medium": 0, "high": 1}


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def rows_for(envelope: dict, batch: str) -> tuple[list[dict], list[dict]]:
    evidence = {item["id"]: item for item in envelope.get("evidence") or []}
    name = (envelope.get("profile") or {}).get("name")
    published, missed = [], []
    for claim in envelope.get("claims") or []:
        field = claim.get("field")
        for item in [] if field == "social_profiles" else claim.get("not_published") or []:
            missed.append({"batch": batch, "org": envelope["organisation_number"], "name": name,
                           "kind": "social" if field == "external_profiles" else KINDS.get(field, field), "url": item.get("url"),
                           "reason": item.get("reason"), "seen": item.get("seen")})
        if field not in KINDS or claim.get("availability") != "available":
            continue
        value = claim.get("value")
        items = value if field == "company_activity" else [value]
        for item in items:
            url = item if isinstance(item, str) else (item.get("url") or item.get("source_url"))
            title = "" if isinstance(item, str) else (item.get("title") or item.get("platform") or "")
            ev = evidence.get((claim.get("evidence_ids") or [None])[0]) or {}
            published.append({"batch": batch, "org": envelope["organisation_number"], "name": name, "kind": KINDS[field],
                              "field": field, "url": url, "title": title, "date": (item or {}).get("published_at") if isinstance(item, dict) else None,
                              "confidence": claim.get("confidence_level") or "none", "proof": claim.get("proof"),
                              "method": claim.get("discovery_method"), "span": ev.get("claim_span"), "source": ev.get("source_url"),
                              "sha": ev.get("content_sha256")})
    return published, missed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--label", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    published, missed, envelopes, batches = [], [], [], []
    for path, label in zip(args.input, args.label):
        batch = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
        envelopes.extend(batch)
        batches.append(f"{esc(label)}: {len(batch)} companies")
        for envelope in batch:
            rows, rejected = rows_for(envelope, label)
            published.extend(rows)
            missed.extend(rejected)
    published.sort(key=lambda row: (ORDER.get(row["confidence"], -1), row["kind"] != "news", row["kind"], row["name"] or ""))
    report = link_report(envelopes)
    counts = "".join(f"<li><b>{esc(family)}</b>: {values['high']} high, {values['medium']} medium "
                     f"({values['companies']} companies)</li>" for family, values in report["published"].items())
    methods = ", ".join(f"{esc(key)} {value}" for key, value in report["websites_by_method"].items()) or "none"
    body = "".join(
        f'<tr class="{esc(row["confidence"])}"><td><input type="checkbox" aria-label="correct"></td><td>{index}</td>'
        f'<td><span class="chip {esc(row["confidence"])}">{esc(row["confidence"])}</span></td><td>{esc(row["kind"])}</td>'
        f'<td>{esc(row["name"])}<br><code>{esc(row["org"])}</code><br><small>{esc(row["batch"])}</small></td>'
        f'<td><a href="{esc(row["url"])}" target="_blank" rel="noopener noreferrer">{esc(row["url"])}</a>'
        f'{"<br>" + esc(row["title"]) if row["title"] and row["kind"] != "social" else ""}{" · " + esc(row["date"]) if row["date"] else ""}</td>'
        f'<td>{esc(row["proof"])}{"<br><small>found by " + esc(row["method"]) + "</small>" if row["method"] else ""}</td>'
        f'<td><small>{esc(row["span"])}</small><br><small class="muted">{esc(row["source"])}<br>sha256 {esc((row["sha"] or "")[:16])}…</small></td></tr>'
        for index, row in enumerate(published, 1))
    rejected = "".join(f'<tr><td>{esc(row["kind"])}</td><td>{esc(row["name"])}<br><code>{esc(row["org"])}</code></td>'
                       f'<td><code>{esc(row["url"])}</code></td><td>{esc(row["reason"])}</td></tr>' for row in missed)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signalpost v5 audit</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e2e2e6;--high:#1a7f37;--medium:#9a6700;--medbg:#fff8e6}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--fg:#ececee;--muted:#a0a0a8;--line:#333338;--high:#4ac26b;--medium:#d4a72c;--medbg:#2a2412}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
main{{max-width:1400px;margin:auto}} table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid var(--line);padding:6px;vertical-align:top;text-align:left}}
tr.medium{{background:var(--medbg)}} .chip{{border:1px solid currentColor;border-radius:999px;padding:0 8px;font-size:12px}}
.chip.high{{color:var(--high)}} .chip.medium{{color:var(--medium)}} .muted,small{{color:var(--muted)}} a{{color:inherit}} code{{font-size:12px}}
.scroll{{overflow-x:auto}} td{{word-break:break-word}}
</style></head><body><main>
<h1>Signalpost v5: published links, weakest first</h1>
<p>Batches never run before: {"; ".join(batches)}. Every published website, social profile, news item and job link, with the proof and
confidence the envelope carries. Medium rows come first. Tick a row once it is checked by hand.</p>
<ul>{counts}</ul><p>Published websites by discovery method: {methods}.</p>
<h2>Published ({len(published)})</h2><div class="scroll"><table><thead><tr><th>✓</th><th>#</th><th>Confidence</th><th>Kind</th><th>Company</th>
<th>Link</th><th>Proof</th><th>Evidence span</th></tr></thead><tbody>{body}</tbody></table></div>
<h2>Found but not published ({len(missed)})</h2><div class="scroll"><table><thead><tr><th>Kind</th><th>Company</th><th>URL</th><th>Reason</th></tr></thead>
<tbody>{rejected}</tbody></table></div></main></body></html>"""
    Path(args.output).write_text(page, encoding="utf-8")
    print(json.dumps({"published": len(published), "not_published": len(missed), **report}, indent=1))


if __name__ == "__main__":
    main()
