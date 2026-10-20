#!/usr/bin/env python3
"""Per-family recall of two runs on the same batch: companies covered, claims emitted and underlying facts.

"Underlying facts" counts a list-valued aggregate claim (e.g. v2's `roles`) by its items and takes, per company and
family group, the larger of the aggregate items and the fact-level claims, so v3's per-fact claims are not
double counted next to the aggregates they expand.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

FAMILIES = {
    "registry_identity": ["legal_name", "legal_form", "industry", "business_address", "postal_address", "registry_status",
                          "accounting_obligation", "nace_code", "founding_date", "registration_date", "share_capital",
                          "vat_registered", "register_membership", "institutional_sector", "registered_purpose",
                          "registered_activity", "auditor_opt_out"],
    "contact": ["registry_email", "registry_phone", "site_address", "site_phone", "site_email", "site_logo"],
    "people_roles": ["roles", "role"],
    "locations": ["registered_locations", "registered_location"],
    "financials": ["latest_annual_accounts", "annual_revenue", "operating_result", "annual_result", "equity", "total_assets"],
    "workforce": ["registered_employees"],
    "group": ["group_structure", "group_relationship", "parent_entity", "group_website"],
    "website": ["official_website"],
    "external_profiles": ["external_profiles", "external_profile", "wikidata_item"],
    "jobs": ["company_jobs", "careers_page", "job_posting"],
    "news_activity": ["dated_news", "company_activity"],
}
# (aggregate field, fact-level fields it expands into)
EXPANDS = {"roles": ["role"], "registered_locations": ["registered_location"], "external_profiles": ["external_profile"],
           "group_structure": ["group_relationship"], "company_jobs": ["careers_page", "job_posting"],
           "latest_annual_accounts": ["annual_revenue", "operating_result", "annual_result", "equity", "total_assets"]}
FIGURES = ("revenue", "operating_result", "annual_result", "equity", "assets")


def aggregate_items(field: str, value) -> int:
    if value is None:
        return 0
    if field == "company_jobs":
        return len(value.get("careers_pages") or []) + len(value.get("job_postings") or [])
    if field == "latest_annual_accounts":
        return sum(1 for key in FIGURES if value.get(key) is not None)
    if field == "company_activity":
        return len(value) if isinstance(value, list) else 1
    return len(value) if isinstance(value, list) else 1


def measure(path: Path) -> dict:
    envelopes = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    family_of = {field: family for family, fields in FAMILIES.items() for field in fields}
    covered: dict[str, int] = defaultdict(int)
    claims_emitted: dict[str, int] = defaultdict(int)
    facts: dict[str, int] = defaultdict(int)
    for envelope in envelopes:
        available = [claim for claim in envelope.get("claims") or [] if claim.get("availability") == "available" and claim.get("value") is not None]
        by_field: dict[str, list] = defaultdict(list)
        for claim in available:
            by_field[claim["field"]].append(claim)
        seen_families = set()
        for claim in available:
            family = family_of.get(claim["field"])
            if family:
                claims_emitted[family] += 1
                seen_families.add(family)
        for family in seen_families:
            covered[family] += 1
        expanded = {field for fields in EXPANDS.values() for field in fields}
        for field, items in by_field.items():
            family = family_of.get(field)
            if not family or field in expanded:
                continue
            if field in EXPANDS:
                aggregate = sum(aggregate_items(field, claim["value"]) for claim in items)
                fact_level = sum(len(by_field.get(sub, [])) for sub in EXPANDS[field])
                facts[family] += max(aggregate, fact_level)
            else:
                facts[family] += sum(aggregate_items(field, claim["value"]) for claim in items)
        for aggregate, subs in EXPANDS.items():  # fact-level claims whose aggregate is absent
            if aggregate not in by_field:
                facts[family_of[aggregate]] += sum(len(by_field.get(sub, [])) for sub in subs)
    return {"companies": len(envelopes),
            "families": {family: {"companies_covered": covered[family], "claims": claims_emitted[family], "facts": facts[family]}
                         for family in FAMILIES}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--before-report")
    parser.add_argument("--after-report")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    before, after = measure(Path(args.before)), measure(Path(args.after))
    result = {"before": before, "after": after}
    for label, report_path in (("before", args.before_report), ("after", args.after_report)):
        if report_path:
            report = json.loads(Path(report_path).read_text(encoding="utf-8"))
            ops = report.get("operations") or {}
            result[label]["operations"] = {key: ops.get(key) for key in ("requests", "requests_per_company", "projected_requests_for_1000",
                                                                         "p50_ms", "p95_ms", "runtime_ms")}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{'family':20} {'covered before→after':>22} {'claims before→after':>22} {'facts before→after':>22}")
    for family in FAMILIES:
        b, a = before["families"][family], after["families"][family]
        print(f"{family:20} {b['companies_covered']:>10} → {a['companies_covered']:<9} {b['claims']:>10} → {a['claims']:<9} {b['facts']:>10} → {a['facts']:<9}")
    for label in ("before", "after"):
        if "operations" in result[label]:
            print(label, result[label]["operations"])


if __name__ == "__main__":
    main()
