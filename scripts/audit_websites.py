#!/usr/bin/env python3
"""Audit page for newly published websites (after vs before on the same batch), weakest proof first."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def published(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        envelope = json.loads(line)
        claim = next((c for c in envelope.get("claims") or [] if c["field"] == "official_website"), None)
        if claim and claim.get("availability") == "available" and claim.get("value"):
            out[envelope["organisation_number"]] = {"envelope": envelope, "claim": claim}
    return out


def strength(entry: dict) -> tuple[int, float]:
    """0 = weakest. Name + registry details < org number on a subpage < org number on the homepage."""
    website = entry["envelope"]["profile"]["evidence"]["website"]
    assessment = (website.get("value") or {}).get("identity_assessment") or {}
    reasons = " ".join(assessment.get("reasons") or [])
    if "exact organisation number appears in homepage" in reasons:
        rank = 3
    elif "exact organisation number appears on the company's own page" in reasons:
        rank = 2
    elif "organisation number" in reasons:
        rank = 2
    else:
        rank = 0 if (website.get("source_type") or "").endswith("candidate") else 1
    return rank, float(assessment.get("score") or 0)


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def row(entry: dict) -> str:
    envelope = entry["envelope"]
    profile = envelope["profile"]
    website = profile["evidence"]["website"]
    value = website.get("value") or {}
    assessment = value.get("identity_assessment") or {}
    evidence = {item["id"]: item for item in envelope.get("evidence") or []}
    span = (evidence.get((entry["claim"].get("evidence_ids") or [None])[0]) or {}).get("claim_span")
    discovery = profile.get("website_discovery") or {}
    method = next((item.get("method") for item in discovery.get("candidates") or [] if item.get("result", "").startswith("published")),
                  website.get("source_type"))
    registry = (profile["evidence"].get("registry") or {}).get("value") or {}
    address = ", ".join(part for part in (registry.get("forretningsadresse.adresse"), registry.get("forretningsadresse.postnummer"),
                                          registry.get("forretningsadresse.poststed")) if part)
    pages = "<br>".join(f'<a href="{esc(p.get("url"))}" target="_blank" rel="noopener">{esc(p.get("url"))}</a>' for p in value.get("pages") or [])
    rank, score = strength(entry)
    label = ["name + registry details", "registry/Wikidata link + name", "org no. on subpage", "org no. on homepage"][rank]
    return (f'<tr class="r{rank}"><td><b>{esc(profile.get("name"))}</b><br>{esc(envelope["organisation_number"])}<br><small>{esc(address)}'
            f'<br>registry e-mail: {esc(registry.get("epostadresse") or "—")}</small></td>'
            f'<td><a href="{esc(entry["claim"]["value"])}" target="_blank" rel="noopener">{esc(entry["claim"]["value"])}</a>'
            f'<br><small>{esc(method)} · score {score}</small></td><td><b>{esc(label)}</b><br><q>{esc(span)}</q></td>'
            f'<td><small>{"<br>".join(esc(r) for r in assessment.get("reasons") or [])}</small></td><td><small>{pages}</small></td></tr>')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    before, after = published(Path(args.before)), published(Path(args.after))
    new = sorted((entry for org, entry in after.items() if org not in before or before[org]["claim"]["value"] != entry["claim"]["value"]),
                 key=strength)
    dropped = [entry for org, entry in before.items() if org not in after]
    groups = []
    for line in Path(args.after).read_text(encoding="utf-8").splitlines():
        envelope = json.loads(line)
        for claim in envelope.get("claims") or []:
            if claim["field"] == "group_website":
                groups.append((envelope["profile"].get("name"), envelope["organisation_number"], claim["value"]))
    total = sum(1 for line in Path(args.after).read_text(encoding="utf-8").splitlines() if line.strip())
    head = "<tr><th>Company</th><th>Published site</th><th>Proof (weakest first)</th><th>Gate reasons</th><th>Pages checked</th></tr>"
    body = (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Website audit v3</title><style>body{{font:14px/1.45 system-ui,sans-serif;margin:16px;background:#fbfaf7;color:#171614}}"
            f"table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:6px;vertical-align:top;text-align:left}}"
            f"tr.r0{{background:#fdecea}}tr.r1{{background:#fff6e0}}tr.r2{{background:#f3f8ee}}q{{font-family:ui-monospace,monospace;font-size:12px}}"
            f"a{{overflow-wrap:anywhere}}</style></head><body><h1>Newly published websites (v3 vs v2, same {total} companies)</h1>"
            f"<p>{len(new)} new · {len(dropped)} published by v2 but not by v3 · {len(groups)} labelled group sites. Weakest proof first "
            f"(red: name + registry details; amber: registry/Wikidata link; green: org no. on subpage; white: org no. on homepage).</p>"
            f"<table>{head}{''.join(row(entry) for entry in new)}</table>"
            f"<h2>Published by v2, not by v3 (stricter rules)</h2><table>{head}{''.join(row(entry) for entry in dropped) or '<tr><td colspan=5>None</td></tr>'}</table>"
            f"<h2>Labelled group sites (not published as the company's own)</h2><ul>"
            + "".join(f"<li>{esc(name)} ({esc(org)}): {esc(value['url'])} — group org no. {esc(', '.join(value['group_organisation_numbers']))}</li>" for name, org, value in groups)
            + "</ul></body></html>")
    Path(args.output).write_text(body, encoding="utf-8")
    print(f"{len(new)} new, {len(dropped)} dropped, {len(groups)} group sites -> {args.output}")


if __name__ == "__main__":
    main()
