#!/usr/bin/env python3
"""v3 -> v4 audit on one batch: citation gaps, state/value disagreements, and every item attached from a site that is
not the company's own (group/parent, another entity, unverified) that v4 removes. Writes JSON and prints a summary."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.citations import check_envelopes, envelope_problems  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from measure_recall import FAMILIES, measure  # noqa: E402

FAMILY_OF = {field: family for family, fields in FAMILIES.items() for field in fields}

SITE_FIELDS = {"site_email", "site_phone", "site_address", "site_logo", "careers_page", "job_posting", "company_jobs",
               "company_activity", "dated_news", "external_profile", "external_profiles"}


def load(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def unowned_content(envelope: dict) -> dict[str, int]:
    """Items read from a fetched site that did not pass the identity gate and still sit in the envelope."""
    website = (envelope.get("profile") or {}).get("evidence", {}).get("website") or {}
    value = website.get("value") or {}
    if website.get("status") != "available" or (value.get("identity_assessment") or {}).get("publishable"):
        return {}
    contact = value.get("site_contact") or {}
    pages = value.get("pages") or []
    found = {
        "social_links": len({item.get("url") for item in (value.get("discovered_social_links") or []) + (value.get("social_links") or [])}),
        "emails": len(set(contact.get("emails") or []) | {e for page in pages for e in (page.get("site_contact") or {}).get("emails") or []}),
        "phones": len(set(contact.get("phones") or []) | {p for page in pages for p in (page.get("site_contact") or {}).get("phones") or []}),
        "jsonld_contacts_and_logos": len(contact.get("organisations") or []),
        "job_postings": len(value.get("job_postings") or []) + sum(len(page.get("job_postings") or []) for page in pages),
        "news_items": len(value.get("news_items") or []) + sum(len(page.get("news_items") or []) for page in pages),
    }
    return {key: count for key, count in found.items() if count}


def published(envelope: dict, fields: set[str] | None = None) -> list[dict]:
    return [claim for claim in envelope.get("claims") or [] if claim.get("availability") == "available"
            and (fields is None or claim.get("field") in fields)]


def site_of(envelope: dict) -> tuple[str | None, bool, dict]:
    website = (envelope.get("profile") or {}).get("evidence", {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    return value.get("final_url"), bool(identity.get("publishable")), identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    before, after = load(args.before), load(args.after)
    after_by_org = {item["organisation_number"]: item for item in after}
    result: dict = {"before": check_envelopes(before), "after": check_envelopes(after)}
    for label, envelopes in (("before", before), ("after", after)):
        counts: Counter = Counter()
        for envelope in envelopes:
            for kind, items in envelope_problems(envelope).items():
                for item in items:
                    counts[f"{kind}:{item['problem']}:{item['field']}"] += 1
        result[label]["problem_counts"] = dict(counts.most_common())
        result[label]["uncited_withheld"] = sum(len(item.get("uncited_withheld") or []) for item in envelopes)
    changes = []
    for old in before:
        org = old["organisation_number"]
        new = after_by_org.get(org) or {}
        old_url, old_owned, _ = site_of(old)
        new_url, new_owned, new_identity = site_of(new)
        removed_content = unowned_content(old)
        old_site_claims = published(old, SITE_FIELDS | {"official_website"})
        new_site_claims = published(new, SITE_FIELDS | {"official_website"})
        old_groups = [c["value"] for c in published(old, {"group_website"})]
        new_groups = [c["value"] for c in published(new, {"group_website"})]
        unpublished = old_owned and not new_owned and old_url == new_url
        if removed_content or unpublished or old_groups != new_groups or (old_url and old_url != new_url and old_owned != new_owned):
            changes.append({
                "organisation_number": org, "name": (old.get("profile") or {}).get("name"),
                "site_v3": old_url, "owned_v3": old_owned, "site_v4": new_url, "owned_v4": new_owned,
                "v4_label": "group_site" if new_identity.get("group_site") else "other_entity_site" if new_identity.get("other_entity_site")
                else ("verified" if new_owned else "unverified" if new_url else None),
                "v4_reason": (new_identity.get("reasons") or [None])[-1],
                "v3_unowned_site_content_in_envelope": removed_content,
                "v4_unowned_site_content_in_envelope": unowned_content(new),
                "v3_published_site_claims": len(old_site_claims), "v4_published_site_claims": len(new_site_claims),
                "v3_group_website": old_groups, "v4_group_website": new_groups,
            })
    result["site_ownership_changes"] = changes
    totals: Counter = Counter()
    for change in changes:
        totals.update(change["v3_unowned_site_content_in_envelope"])
    result["unowned_site_items_removed"] = dict(totals)
    # Recall cost of withholding: claims removed because they could not be cited, per family, and per-family recall.
    withheld: Counter = Counter()
    examples: dict[str, list] = {}
    for envelope in after:
        for item in (envelope.get("uncited_withheld") or []) + (envelope.get("citation_withheld") or []):
            family = FAMILY_OF.get(item.get("field"), item.get("field"))
            withheld[family] += 1
            examples.setdefault(family, []).append({"organisation_number": envelope["organisation_number"], **item})
    recall_before, recall_after = measure(Path(args.before)), measure(Path(args.after))
    families = {}
    for family in FAMILIES:
        b, a = recall_before["families"][family], recall_after["families"][family]
        change = {key: (a[key] - b[key]) / b[key] * 100 if b[key] else None for key in ("companies_covered", "facts")}
        families[family] = {"before": b, "after": a, "pct_change": change, "withheld_uncitable": withheld.get(family, 0)}
        if any(value is not None and value < -5 for value in change.values()):
            families[family]["examples"] = examples.get(family, [])[:10]
    result["recall"] = families
    result["withheld_uncitable_by_family"] = dict(withheld)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{'family':20} {'covered v3→v4':>16} {'facts v3→v4':>18} {'withheld':>9}")
    for family, row in families.items():
        flag = "  <-- >5% drop" if "examples" in row else ""
        print(f"{family:20} {row['before']['companies_covered']:>7} → {row['after']['companies_covered']:<6} "
              f"{row['before']['facts']:>8} → {row['after']['facts']:<7} {row['withheld_uncitable']:>9}{flag}")
    for label in ("before", "after"):
        summary = result[label]
        print(label, {key: summary[key] for key in ("published_claims", "published_claims_missing_hash", "published_claims_missing_span",
                                                    "citation_problems", "state_problems", "site_ownership_problems", "uncited_withheld")})
    print("unowned-site items removed:", dict(totals))
    for change in changes:
        print(json.dumps(change, ensure_ascii=False))


if __name__ == "__main__":
    main()
