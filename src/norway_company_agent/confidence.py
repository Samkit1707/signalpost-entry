"""Confidence labels for published links (website, social profile, news item, careers page, job posting).

Two levels only: `high` and `medium`. A link that would be lower is not published; it stays blank
(`not_available`) with a reason. The numeric `confidence` of a claim is kept for the starter contract and
follows the level (high 0.95, medium 0.8).
"""
from __future__ import annotations

from typing import Any

LEVEL_SCORE = {"high": 0.95, "medium": 0.8}

# website record source_type -> discovery method reported per published site
DISCOVERY_METHODS = {
    "registry_linked_company_website": "registry_hjemmeside",
    "wikidata_linked_company_website": "wikidata_p856",
    "registry_email_domain_candidate": "registry_email_domain",
    "name_derived_domain_candidate": "name_variant",
    "subunit_registry_website_candidate": "subunit_registry_website",
}


def lower(*levels: str | None) -> str | None:
    """The weaker of several levels; None when any is None."""
    if any(level is None for level in levels):
        return None
    return "medium" if "medium" in levels else "high"


def discovery_method(profile: dict[str, Any]) -> str | None:
    website = (profile.get("evidence") or {}).get("website") or {}
    method = DISCOVERY_METHODS.get(str(website.get("source_type") or ""))
    if method == "name_variant":
        final = ((website.get("value") or {}).get("final_url")) or ""
        for item in (profile.get("website_discovery") or {}).get("candidates") or []:
            if str(item.get("result", "")).startswith("published") and item.get("method") == "subunit_name" \
                    and item.get("candidate") and item["candidate"] in final:
                return "subunit_name"
    return method


def website_confidence(profile: dict[str, Any]) -> tuple[str | None, str]:
    """(level, reason) for the identity-gated website; (None, reason) when it is not published."""
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    if website.get("status") != "available" or not identity.get("publishable"):
        return None, "no website passed the exact-identity check"
    method = discovery_method(profile)
    if float(identity.get("score") or 0) >= 1.0:
        return "high", "the site prints this organisation number"
    if any(item.get("type") == "wikidata_official_website" for item in identity.get("promotion_proof") or []):
        return "high", "the domain is the official website on the Wikidata item whose org number (P2333) is this one"
    if method == "registry_hjemmeside":
        return "high", "the registry lists this website for the organisation number and the site shows the full legal name"
    if method == "wikidata_p856":
        return "high", "official website on the exact Wikidata item, and the site shows the full legal name"
    return "medium", "found by " + (method or "candidate").replace("_", " ") + "; the site shows the full legal name with registry address or phone, not the org number"


def proof_text(link: dict[str, Any]) -> str:
    """'linked from <page> footer' / 'JSON-LD sameAs on <page>'."""
    page = link.get("page_url") or ""
    location = link.get("location")
    if location == "jsonld_same_as":
        return f"JSON-LD sameAs on {page}"
    return f"linked from {page} {str(location or 'page').replace('_', ' ')}".strip()


# claim field -> link family in the run report
LINK_FAMILIES = {"official_website": "website", "external_profile": "social", "dated_news": "news", "company_activity": "news",
                 "careers_page": "jobs", "job_posting": "jobs"}


def link_report(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    """Published links per family and confidence, websites per discovery method, and candidates not published."""
    families: dict[str, dict[str, int]] = {}
    methods: dict[str, int] = {}
    missed: dict[str, int] = {}
    companies: dict[str, set[str]] = {}
    social = {"companies": 0, "profiles": 0, "high": 0, "medium": 0}
    for envelope in envelopes:
        for claim in envelope.get("claims") or []:
            family = LINK_FAMILIES.get(str(claim.get("field")))
            if claim.get("field") == "social_profiles":
                if claim.get("availability") == "available":
                    social["companies"] += 1
                    for entry in claim.get("value") or []:
                        social["profiles"] += 1
                        if entry.get("confidence") in ("high", "medium"):
                            social[entry["confidence"]] += 1
                continue  # same candidates as external_profiles; counted once
            if claim.get("not_published"):
                key = "social" if claim.get("field") == "external_profiles" else LINK_FAMILIES.get(str(claim.get("field")), str(claim.get("field")))
                missed[key] = missed.get(key, 0) + len(claim["not_published"])
            if not family or claim.get("availability") != "available":
                continue
            count = len(claim.get("value") or []) if claim.get("field") == "company_activity" else 1
            level = str(claim.get("confidence_level") or "none")
            bucket = families.setdefault(family, {"high": 0, "medium": 0, "none": 0})
            bucket[level] = bucket.get(level, 0) + count
            companies.setdefault(family, set()).add(str(envelope.get("organisation_number")))
            if claim.get("field") == "official_website":
                method = str(claim.get("discovery_method") or "unknown")
                methods[method] = methods.get(method, 0) + 1
    return {"published": {family: {**counts, "companies": len(companies.get(family, ()))} for family, counts in sorted(families.items())},
            "social_profiles": social,
            "websites_by_method": dict(sorted(methods.items())),
            "candidates_not_published": dict(sorted(missed.items()))}
