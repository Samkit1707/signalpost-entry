"""Hand-check page for the coverage update: only the links the new run publishes that the base run did not,
each with the exact org-number line that proved the company's website. Also prints the list as plain text.

    uv run python scripts/build_audit_coverage.py --before out/coverage-run/before-envelopes.jsonl \
        --after out/coverage-run/after2-envelopes.jsonl --before-report out/coverage-run/before-report.json \
        --after-report out/coverage-run/after2-report.json --output out/audit-coverage.html
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

TYPES = {"official_website": "WEBSITE", "external_profile": "SOCIAL", "dated_news": "NEWS", "careers_page": "CAREERS",
         "job_posting": "JOB", "registered_location": "SUBUNIT-SITE"}
FAMILY = {"WEBSITE": "websites", "SOCIAL": "socials", "NEWS": "news", "CAREERS": "careers pages", "JOB": "job postings",
          "SUBUNIT-SITE": "sub-unit site labels"}


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def load(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def links(envelope: dict) -> list[dict]:
    evidence = {item["id"]: item for item in envelope.get("evidence") or []}
    out = []
    for claim in envelope.get("claims") or []:
        if claim.get("field") not in TYPES or claim.get("availability") != "available":
            continue
        value = claim.get("value")
        url = value if isinstance(value, str) else (value or {}).get("url")
        if claim["field"] == "registered_location":
            url = (value or {}).get("subunit_website")
            if not url:
                continue
        ev = evidence.get((claim.get("evidence_ids") or [None])[0]) or {}
        out.append({"type": TYPES[claim["field"]], "url": url, "span": " ".join(str(ev.get("claim_span") or "").split()),
                    "source": ev.get("source_url"), "method": claim.get("discovery_method"), "proof": claim.get("proof")})
    return out


def org_line(span: str, org: str) -> str:
    """The proof quote cut to the org-number line: 100 characters centred on the number."""
    match = re.search(r"(?<!\d)" + r"[\s. -]?".join([org[:3], org[3:6], org[6:]]) + r"(?!\d)", span)
    if not match:
        return span[:100]
    start = max(0, min(match.start() - 60, len(span) - 100))
    return span[start:start + 100]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--before-report")
    parser.add_argument("--after-report")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    before = {envelope["organisation_number"]: {link["url"] for link in links(envelope)} for envelope in load(args.before)}
    rows, counts = [], {"before": {}, "after": {}}
    for label, path in (("before", args.before), ("after", args.after)):
        for envelope in load(path):
            for link in links(envelope):
                family = FAMILY[link["type"]]
                counts[label][family] = counts[label].get(family, 0) + 1
    for envelope in load(args.after):
        org, name = envelope["organisation_number"], (envelope.get("profile") or {}).get("name")
        company_links = links(envelope)
        site = next((link for link in company_links if link["type"] == "WEBSITE"), None)
        site_line = org_line(site["span"], org) if site else ""
        for link in company_links:
            if link["url"] in before.get(org, set()):
                continue
            proof = org_line(link["span"], org) if link["type"] == "WEBSITE" else link["span"][:100]
            rows.append({"type": link["type"], "org": org, "name": name, "url": link["url"], "proof": proof,
                         "proof_page": link["source"], "site_org_line": site_line, "method": link["method"]})

    reports = {label: json.loads(Path(path).read_text()) for label, path in (("before", args.before_report), ("after", args.after_report))
               if path and Path(path).exists()}
    ops = {label: report.get("operations") or {} for label, report in reports.items()}
    families = ("websites", "socials", "news", "careers pages", "job postings", "sub-unit site labels")
    table = "".join(f"<tr><td>{family}</td><td>{counts['before'].get(family, 0)}</td><td>{counts['after'].get(family, 0)}</td></tr>"
                    for family in families)
    if ops:
        table += "".join(f"<tr><td>{label}</td><td>{ops.get('before', {}).get(key)}</td><td>{ops.get('after', {}).get(key)}</td></tr>"
                         for label, key in (("requests", "requests"), ("p50 ms", "p50_ms"), ("p95 ms", "p95_ms"),
                                            ("runtime ms", "runtime_ms")))
    body = "".join(
        f'<div class="row" data-id="{esc(row["org"])}|{esc(row["url"])}"><div><span class="chip">{esc(row["type"])}</span> '
        f'<b>{esc(row["name"])}</b> <span class="muted">{esc(row["org"])}</span><br>'
        f'<a href="{esc(row["url"])}" target="_blank" rel="noopener">{esc(row["url"])}</a>'
        f'{"<br><span class=chip>found via " + esc(row["method"].replace("_", " ")) + "</span>" if row["method"] else ""}'
        f'<div class="quote">{esc(row["proof"])}</div><div class="muted small">quoted from '
        f'<a href="{esc(row["proof_page"])}" target="_blank" rel="noopener">{esc(row["proof_page"])}</a></div>'
        + (f'<div class="muted small">company website org-number line: {esc(row["site_org_line"])}</div>' if row["type"] != "WEBSITE" else "")
        + '</div><div><button class="r" data-m="r">Right</button> <button class="w" data-m="w">Wrong</button></div></div>'
        for row in rows)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coverage Audit</title><style>
:root{{--bg:#fafaf9;--fg:#1c1917;--muted:#57534e;--card:#fff;--line:#e7e5e4;--accent:#1d4ed8;--ok:#15803d;--bad:#b91c1c;--chip:#f5f5f4;--quote:#f8fafc}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#0c0a09;--fg:#e7e5e4;--muted:#a8a29e;--card:#1c1917;--line:#292524;--accent:#93c5fd;--ok:#4ade80;--bad:#f87171;--chip:#292524;--quote:#111827}}}}
:root[data-theme="dark"]{{--bg:#0c0a09;--fg:#e7e5e4;--muted:#a8a29e;--card:#1c1917;--line:#292524;--accent:#93c5fd;--ok:#4ade80;--bad:#f87171;--chip:#292524;--quote:#111827}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}}main{{max-width:1000px;margin:0 auto;padding:24px 16px 80px}}
a{{color:var(--accent);word-break:break-all}}.muted{{color:var(--muted)}}.small{{font-size:12px}}table{{border-collapse:collapse;margin:12px 0}}td,th{{border-top:1px solid var(--line);padding:4px 12px;text-align:left}}
.row{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:10px 0;display:grid;grid-template-columns:1fr auto;gap:8px}}
.chip{{display:inline-block;background:var(--chip);border-radius:6px;padding:0 6px;font-size:12px}}
.quote{{background:var(--quote);border-left:3px solid var(--line);padding:4px 8px;font:12px/1.45 ui-monospace,Menlo,monospace;white-space:pre-wrap;word-break:break-word;margin-top:4px}}
button{{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:4px 10px;cursor:pointer}}
.row.right{{border-color:var(--ok)}}.row.wrong{{border-color:var(--bad)}}.bar{{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line)}}
@media (max-width:640px){{.row{{grid-template-columns:1fr}}}}</style></head><body><main>
<h1>{esc(Path(args.output).stem.replace("-", " ").capitalize())}: new links only</h1>
<p class="muted">Same 100 unseen companies as the last audit. Base = commit on main before this update, new = working tree, same day.
Only links the new run publishes and the base run did not are listed. Websites are proven by the exact org number; the quote
is the line that printed it. SUBUNIT-SITE rows are labels on the sub-unit's location claim (its registry website entry),
not the company's website.</p>
<table><tr><th></th><th>before</th><th>after</th></tr>{table}</table>
<div class="bar"><b id="tally"></b></div>{body or '<p>No new links.</p>'}
</main><script>
const K='audit-coverage-marks';let m={{}};try{{m=JSON.parse(localStorage.getItem(K)||'{{}}')}}catch(e){{}}
function p(){{let r=0,w=0;document.querySelectorAll('.row').forEach(e=>{{const v=m[e.dataset.id];e.classList.toggle('right',v==='r');e.classList.toggle('wrong',v==='w');r+=v==='r';w+=v==='w'}});
document.getElementById('tally').textContent=`${{r}} right · ${{w}} wrong · ${{document.querySelectorAll('.row').length-r-w}} unchecked`}}
document.addEventListener('click',e=>{{const b=e.target.closest('button[data-m]');if(!b)return;const id=b.closest('.row').dataset.id;m[id]=m[id]===b.dataset.m?undefined:b.dataset.m;try{{localStorage.setItem(K,JSON.stringify(m))}}catch(x){{}}p()}});p();
</script></body></html>"""
    Path(args.output).write_text(page, encoding="utf-8")
    for row in rows:
        print(f"{row['type']} | {row['org']} | {row['name']} | {row['url']} | {row['proof']}")
    print(f"-- {len(rows)} new links; before {counts['before']}; after {counts['after']}")


if __name__ == "__main__":
    main()
