"""Hand-check page for v6: every published website and social profile (plus news and job links) with its proof.

Each row has Right / Wrong buttons; the tally and a copyable result line stay in the browser only.

    uv run python scripts/build_audit_v6.py --envelopes out/audit-v6-run/envelopes.jsonl \
        --diagnostics out/audit-v6-run/envelopes.diagnostics.jsonl --v5 out/audit-v6-run/v5-envelopes.jsonl \
        --output out/audit-v6.html
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

KINDS = {"official_website": "Website", "external_profile": "Social", "dated_news": "News", "careers_page": "Careers",
         "job_posting": "Job"}


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def load(path: str | None) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def registry_line(envelope: dict) -> str:
    claims = {c["field"]: c.get("value") for c in envelope.get("claims") or [] if c.get("availability") == "available"}
    parts = [claims.get("business_address"), (claims.get("industry") or {}).get("description") if isinstance(claims.get("industry"), dict) else None]
    return " · ".join(str(part) for part in parts if part)


def published_links(envelope: dict) -> list[dict]:
    evidence = {item["id"]: item for item in envelope.get("evidence") or []}
    rows = []
    for claim in envelope.get("claims") or []:
        field = claim.get("field")
        if field not in KINDS or claim.get("availability") != "available":
            continue
        value = claim.get("value")
        url = value if isinstance(value, str) else (value or {}).get("url")
        label = (value or {}).get("platform") if field == "external_profile" else (value or {}).get("title") if isinstance(value, dict) else None
        ev = evidence.get((claim.get("evidence_ids") or [None])[0]) or {}
        rows.append({"field": field, "kind": KINDS[field], "url": url, "label": label, "confidence": claim.get("confidence_level"),
                     "method": claim.get("discovery_method"), "proof": claim.get("proof"), "span": ev.get("claim_span"),
                     "source": ev.get("source_url"), "date": (value or {}).get("published_date") if isinstance(value, dict) else None,
                     "match": (value or {}).get("company_match") if isinstance(value, dict) else None})
    return rows


def v5_links(envelopes: list[dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for envelope in envelopes:
        for claim in envelope.get("claims") or []:
            if claim.get("field") in {"official_website", "external_profile"} and claim.get("availability") == "available":
                value = claim.get("value")
                url = value if isinstance(value, str) else (value or {}).get("url")
                if url:
                    out.setdefault(envelope["organisation_number"], set()).add(url)
    return out


CSS = """
:root{--bg:#fafaf9;--fg:#1c1917;--muted:#57534e;--card:#fff;--line:#e7e5e4;--accent:#1d4ed8;--ok:#15803d;--bad:#b91c1c;--chip:#f5f5f4;--quote:#f8fafc}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0c0a09;--fg:#e7e5e4;--muted:#a8a29e;--card:#1c1917;--line:#292524;--accent:#93c5fd;--ok:#4ade80;--bad:#f87171;--chip:#292524;--quote:#111827}}
:root[data-theme="dark"]{--bg:#0c0a09;--fg:#e7e5e4;--muted:#a8a29e;--card:#1c1917;--line:#292524;--accent:#93c5fd;--ok:#4ade80;--bad:#f87171;--chip:#292524;--quote:#111827}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 80px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 8px}
.muted{color:var(--muted)}a{color:var(--accent);word-break:break-all}
.stats{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.stat b{display:block;font-size:22px}.bar{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 0;z-index:2;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.card h3{margin:0;font-size:16px}.row{border-top:1px solid var(--line);padding:10px 0;display:grid;grid-template-columns:1fr auto;gap:8px}
.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:0 6px;font-size:12px;margin-right:4px}
.quote{background:var(--quote);border-left:3px solid var(--line);padding:4px 8px;font:12px/1.45 ui-monospace,Menlo,monospace;white-space:pre-wrap;word-break:break-word;margin-top:4px}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:4px 10px;cursor:pointer}
.row.right{background:color-mix(in srgb,var(--ok) 8%,transparent)}.row.wrong{background:color-mix(in srgb,var(--bad) 10%,transparent)}
.row.right button.r{border-color:var(--ok);color:var(--ok);font-weight:600}.row.wrong button.w{border-color:var(--bad);color:var(--bad);font-weight:600}
details{margin:8px 0}summary{cursor:pointer}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-top:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
@media (max-width:640px){.row{grid-template-columns:1fr}}
"""

JS = """
const KEY='audit-v6-marks';let marks={};try{marks=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
function paint(){let r=0,w=0;document.querySelectorAll('.row[data-id]').forEach(el=>{const m=marks[el.dataset.id];el.classList.toggle('right',m==='r');el.classList.toggle('wrong',m==='w');if(m==='r')r++;if(m==='w')w++});
const total=document.querySelectorAll('.row[data-id]').length;document.getElementById('tally').textContent=`${r} right · ${w} wrong · ${total-r-w} unchecked of ${total}`+(r+w?` · ${(100*r/(r+w)).toFixed(1)}% right`:'')}
document.addEventListener('click',e=>{const b=e.target.closest('button[data-m]');if(!b)return;const id=b.closest('.row').dataset.id;marks[id]=marks[id]===b.dataset.m?undefined:b.dataset.m;try{localStorage.setItem(KEY,JSON.stringify(marks))}catch(e){}paint()});
document.getElementById('copy').onclick=()=>{const wrong=[...document.querySelectorAll('.row.wrong')].map(el=>el.dataset.id);const t=document.getElementById('tally').textContent+(wrong.length?'\\nWrong: '+wrong.join(', '):'');navigator.clipboard&&navigator.clipboard.writeText(t);document.getElementById('copy').textContent='Copied'};
paint();
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--diagnostics")
    parser.add_argument("--report")
    parser.add_argument("--v5", help="v5 envelopes for the same companies (optional comparison)")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    envelopes = load(args.envelopes)
    diagnostics = {item["organisation_number"]: item for item in load(args.diagnostics)}
    old = v5_links(load(args.v5))
    report = json.loads(Path(args.report).read_text()) if args.report and Path(args.report).exists() else {}

    cards, counts = [], {kind: 0 for kind in KINDS.values()}
    companies_with = 0
    for envelope in envelopes:
        links = published_links(envelope)
        if not links:
            continue
        companies_with += 1
        org, name = envelope["organisation_number"], (envelope.get("profile") or {}).get("name")
        rows = []
        for index, link in enumerate(links):
            counts[link["kind"]] += 1
            meta = [f'<span class="chip">{esc(link["kind"])}</span>']
            if link["label"]:
                meta.append(f'<span class="chip">{esc(link["label"])}</span>')
            if link["confidence"]:
                meta.append(f'<span class="chip">{esc(link["confidence"])} confidence</span>')
            if link["method"]:
                meta.append(f'<span class="chip">found via {esc(link["method"].replace("_", " "))}</span>')
            if link["date"]:
                meta.append(f'<span class="chip">{esc(link["date"])}</span>')
            proof = f'<div class="muted">{esc(link["proof"])}</div>' if link["proof"] else ""
            match = f'<div class="muted">Company match: {esc(link["match"])}</div>' if link["match"] else ""
            quote = (f'<div class="quote">{esc(link["span"])}</div><div class="muted" style="font-size:12px">quoted from '
                     f'<a href="{esc(link["source"])}" target="_blank" rel="noopener">{esc(link["source"])}</a></div>') if link["span"] else ""
            rows.append(f'<div class="row" data-id="{esc(org)}#{index}"><div><a href="{esc(link["url"])}" target="_blank" rel="noopener">'
                        f'{esc(link["url"])}</a><div>{"".join(meta)}</div>{proof}{match}{quote}</div>'
                        f'<div><button class="r" data-m="r">Right</button> <button class="w" data-m="w">Wrong</button></div></div>')
        headline = (envelope.get("synthesis") or {}).get("headline")
        cards.append(f'<div class="card"><h3>{esc(name)} <span class="muted">· {esc(org[:3])} {esc(org[3:6])} {esc(org[6:])}</span></h3>'
                     f'<div class="muted">{esc(registry_line(envelope))} · <a href="https://virksomhet.brreg.no/oppslag/enheter/{esc(org)}" '
                     f'target="_blank" rel="noopener">Brønnøysund</a></div>'
                     + (f'<div style="margin-top:4px">Summary opens: “{esc(headline)}”</div>' if headline else "")
                     + "".join(rows) + "</div>")

    # what v6 no longer publishes (from v5 on the same companies), and what the gate rejected
    dropped = []
    for envelope in envelopes:
        org = envelope["organisation_number"]
        now = {link["url"] for link in published_links(envelope)}
        for url in sorted(old.get(org, set()) - now):
            reason = next((("; ".join(site.get("reasons") or [])[-220:]) for site in (diagnostics.get(org) or {}).get("rejected_sites") or []
                           if url and (site.get("url") or "").split("/")[2:3] == url.split("/")[2:3]), "")
            dropped.append(f'<tr><td>{esc((envelope.get("profile") or {}).get("name"))}<br><span class="muted">{esc(org)}</span></td>'
                           f'<td><a href="{esc(url)}" target="_blank" rel="noopener">{esc(url)}</a></td><td class="muted">{esc(reason)}</td></tr>')
    rejected = []
    for org, item in diagnostics.items():
        for site in item.get("rejected_sites") or []:
            rejected.append(f'<tr><td>{esc(item.get("name"))}<br><span class="muted">{esc(org)}</span></td><td><a href="{esc(site.get("url"))}" '
                            f'target="_blank" rel="noopener">{esc(site.get("url"))}</a><br><span class="chip">{esc(site.get("source_type"))}</span></td>'
                            f'<td class="muted">{esc((site.get("reasons") or [""])[-1])}</td></tr>')

    total = sum(counts.values())
    ops = report.get("operations") or {}
    stats = "".join(f'<div class="stat"><b>{value}</b>{esc(label)}</div>' for label, value in [
        ("companies", len(envelopes)), ("with a published link", companies_with), ("websites", counts["Website"]),
        ("social profiles", counts["Social"]), ("news items", counts["News"]), ("careers/jobs", counts["Careers"] + counts["Job"])])
    run_line = (f'Run {esc(report.get("run_id"))}: validation {"passed" if (report.get("validation") or {}).get("passed") else "FAILED"}, '
                f'{ops.get("requests")} requests, p95 {ops.get("p95_ms")} ms, link guard removed {(report.get("link_guard") or {}).get("removed_from_envelopes")} '
                "stray mentions (rejected registry websites in raw rows).") if report else ""
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Precision Audit v6</title><style>{CSS}</style></head><body><main>
<h1>Precision audit v6</h1>
<p class="muted">100 companies never used in any earlier run (fresh seed, picked after the v6 rules were final). Every website, social
profile, news item and job link v6 publishes is listed with its proof. Mark each one; the tally stays in this browser.
Target: ≥95% right. {run_line}</p>
<div class="stats">{stats}</div>
<div class="bar"><b id="tally"></b><button id="copy">Copy result</button></div>
<h2>Published ({total} links)</h2>
{''.join(cards) or '<p>No links published.</p>'}
<h2>Published by v5 on these companies, not by v6 ({len(dropped)})</h2>
<p class="muted">The recall v6 gives up for precision. Most are sites that show the name but not the org number.</p>
<table><tr><th>Company</th><th>Link v5 published</th><th>Why v6 does not</th></tr>{''.join(dropped) or '<tr><td colspan="3">None, or v5 comparison not run.</td></tr>'}</table>
<details><summary>All candidate sites the v6 gate rejected ({len(rejected)})</summary>
<table><tr><th>Company</th><th>Rejected site</th><th>Last reason</th></tr>{''.join(rejected)}</table></details>
</main><script>{JS}</script></body></html>"""
    Path(args.output).write_text(page, encoding="utf-8")
    print(json.dumps({"output": args.output, "published_links": total, **counts, "companies_with_links": companies_with,
                      "v5_only_links": len(dropped), "rejected_sites": len(rejected)}))


if __name__ == "__main__":
    main()
