"""Zero- or low-request external field families built on exact-entity evidence.

Every module returns one evidence record (see evidence.py) so terminal envelopes stay valid.
Nothing here guesses: values come from the official bulk registry, an exact Wikidata match
(P2333 == organisation number) or pages of a website that passed the identity gate.
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from .evidence import evidence
from .identity import link_with_provenance
from .website import normalize_social_url

ENRICHMENT_MODULES = ("workforce", "external_profiles", "company_activity", "company_jobs")
BRREG_BULK_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"


def _website(profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], bool]:
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    publishable = website.get("status") == "available" and bool(identity.get("publishable"))
    return website, value, publishable


# What a website record keeps when the site is not this company's own (group/parent site, another entity, unverified).
KEPT_FROM_UNOWNED_SITE = ("requested_url", "final_url", "registered_domain", "title", "content_sha256", "identity_assessment",
                          "extraction_state", "crawl_errors")


def quarantine_unowned_site(profile: dict[str, Any]) -> dict[str, int] | None:
    """Drop everything read from a fetched site that did not pass the identity gate, so none of its social profiles,
    news, jobs, contact details or logo can be attached to this company. A group/parent site stays only as the
    labelled `group_website` relationship (URL, group organisation numbers, quoted proof). Idempotent."""
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value")
    if website.get("status") != "available" or not isinstance(value, dict):
        return None
    if (value.get("identity_assessment") or {}).get("publishable"):
        return None
    if value.get("quarantined") is not None:
        return value["quarantined"]
    contact = value.get("site_contact") or {}
    pages = value.get("pages") or []
    removed = {
        "social_links": len({item.get("url") for item in (value.get("discovered_social_links") or []) + (value.get("social_links") or [])}),
        "emails": len(set(contact.get("emails") or []) | {email for page in pages for email in (page.get("site_contact") or {}).get("emails") or []}),
        "phones": len(set(contact.get("phones") or []) | {phone for page in pages for phone in (page.get("site_contact") or {}).get("phones") or []}),
        "jsonld_contacts_and_logos": len(contact.get("organisations") or []),
        "job_postings": len(value.get("job_postings") or []) + sum(len(page.get("job_postings") or []) for page in pages),
        "news_items": len(value.get("news_items") or []) + sum(len(page.get("news_items") or []) for page in pages),
        "feeds": len(value.get("feed_links") or []),
    }
    kept = {key: value[key] for key in KEPT_FROM_UNOWNED_SITE if key in value}
    kept["pages"] = [{key: page.get(key) for key in ("url", "title", "content_sha256", "category")} for page in pages]
    kept["social_links"] = []
    kept["quarantined"] = removed
    website["value"] = kept
    return removed


def workforce_record(profile: dict[str, Any]) -> dict[str, Any]:
    registry = (profile.get("evidence") or {}).get("registry") or {}
    employees = profile.get("employees")
    raw = registry.get("value") or {}
    registered = raw.get("registreringsdatoAntallAnsatteEnhetsregisteret") or raw.get("Registreringsdato antall ansatte Enhetsregisteret")
    common = dict(
        retrieved_at=registry.get("retrieved_at"),
        content_sha256=registry.get("content_sha256"),
        source_row_key=profile.get("organisation_number"),
    )
    if employees is None:
        return evidence("workforce", "not_found", "official_registry_bulk", BRREG_BULK_URL,
                        note="Registry snapshot has no employee count for this entity", **common)
    return evidence(
        "workforce", "available", "official_registry_bulk", BRREG_BULK_URL,
        value={"registered_employees": employees, "registered_at": registered or None,
               "interpretation": "Employee count reported to the official register; not a live headcount."},
        as_of=registered or None, **common,
    )


def _profile_key(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").casefold().removeprefix("www.").removeprefix("m.")
    host = "x.com" if host == "twitter.com" else host
    return host + parsed.path.rstrip("/").casefold()


def external_profiles_record(profile: dict[str, Any], wikidata: dict[str, Any] | None) -> dict[str, Any]:
    """Company-owned profiles from a gated website and/or an exact Wikidata match."""
    website, value, publishable = _website(profile)
    merged: dict[str, dict[str, Any]] = {}
    if publishable:
        for link in value.get("social_links") or []:
            url = str(link.get("url") or "")
            if not url:
                continue
            merged.setdefault(_profile_key(url), {
                "platform": link.get("platform"), "url": url,
                "sources": [], "identity_proof": [],
            })
            item = merged[_profile_key(url)]
            item["sources"].append({"source_url": link.get("page_url") or value.get("final_url") or website.get("source_url"),
                                    "content_sha256": link.get("content_sha256") if link.get("page_url") else value.get("content_sha256"),
                                    "retrieved_at": website.get("retrieved_at"),
                                    "claim_span": link.get("raw"),
                                    "method": "linked_from_identity_gated_company_site"})
            item["identity_proof"].append("linked from verified company website")
    wiki_value = (wikidata or {}).get("value") if (wikidata or {}).get("status") == "available" else None
    if wiki_value:
        for link in wiki_value.get("profiles") or []:
            normalized = normalize_social_url(link["url"]) or {"platform": link["platform"], "url": link["url"]}
            key = _profile_key(normalized["url"])
            item = merged.setdefault(key, {"platform": link["platform"], "url": normalized["url"],
                                           "sources": [], "identity_proof": []})
            item["sources"].append({"source_url": wikidata.get("source_url") or wiki_value["wikidata_item"],
                                    "content_sha256": link.get("content_sha256") or wikidata.get("content_sha256"),
                                    "retrieved_at": wikidata.get("retrieved_at"),
                                    "claim_span": link.get("claim_span"),
                                    "method": "wikidata_exact_org_number_match"})
            item["identity_proof"].append(wiki_value["identity_proof"])
        for page in wiki_value.get("wikipedia") or []:
            item = merged.setdefault(_profile_key(page), {"platform": "wikipedia", "url": page,
                                                          "sources": [], "identity_proof": []})
            item["sources"].append({"source_url": wikidata.get("source_url") or wiki_value["wikidata_item"],
                                    "content_sha256": (wiki_value.get("sitelink_sources") or {}).get(page, {}).get("content_sha256") or wikidata.get("content_sha256"),
                                    "retrieved_at": wikidata.get("retrieved_at"),
                                    "claim_span": (wiki_value.get("sitelink_sources") or {}).get(page, {}).get("claim_span"),
                                    "method": "wikidata_sitelink"})
            item["identity_proof"].append(wiki_value["identity_proof"])
    profiles = sorted(merged.values(), key=lambda item: (str(item["platform"]), item["url"]))
    source_url = (value.get("final_url") if publishable else None) or (wiki_value or {}).get("wikidata_item") or BRREG_BULK_URL
    if not profiles:
        note = "No company-owned profile found on a verified site or exact Wikidata item"
        if not publishable and not wiki_value:
            note = "No verified website and no exact Wikidata match to search"
        return evidence("external_profiles", "not_found", "company_owned_profiles", source_url, note=note)
    return evidence("external_profiles", "available", "company_owned_profiles", source_url,
                    value={"profiles": profiles, "platforms": sorted({str(p["platform"]) for p in profiles})},
                    retrieved_at=website.get("retrieved_at") if publishable else (wikidata or {}).get("retrieved_at"))


def company_activity_record(profile: dict[str, Any]) -> dict[str, Any]:
    """Dated public activity the verified company site itself declares (news pages, published dates)."""
    website, value, publishable = _website(profile)
    if not publishable:
        return evidence("company_activity", "not_found", "company_site_activity",
                        website.get("source_url") or BRREG_BULK_URL,
                        note="No identity-gated company website to read activity from")
    items = []
    pages = value.get("pages") or []
    for page in pages:
        dates = page.get("declared_dates") or []
        is_news = page.get("category") == "news"
        if not dates and not is_news:
            continue
        items.append({
            "url": page.get("url"),
            "title": page.get("title"),
            "page_type": "news" if is_news else "page",
            "declared_dates": dates,
            "latest_declared_date": dates[0] if dates else None,
            "content_sha256": page.get("content_sha256"),
            # the declared date as written, or (undated news page) the page's own title
            "claim_span": (page.get("declared_date_spans") or {}).get(dates[0]) if dates
            else " ".join(str(page.get("title") or "").split()) or None,
        })
    homepage_dates = value.get("declared_dates") or []
    if homepage_dates and not any(item["url"] == value.get("final_url") for item in items):
        items.append({"url": value.get("final_url"), "title": value.get("title"), "page_type": "homepage",
                      "declared_dates": homepage_dates, "latest_declared_date": homepage_dates[0],
                      "content_sha256": value.get("content_sha256"),
                      "claim_span": (value.get("declared_date_spans") or {}).get(homepage_dates[0])})
    items = [item for item in items if item["claim_span"] and item["content_sha256"]]  # cited exactly or not at all
    if not items:
        return evidence("company_activity", "not_found", "company_site_activity", value.get("final_url") or website.get("source_url"),
                        note="Verified site crawled; no news page or declared publication date found",
                        retrieved_at=website.get("retrieved_at"))
    latest = max((item["latest_declared_date"] for item in items if item["latest_declared_date"]), default=None)
    return evidence("company_activity", "available", "company_site_activity", items[0]["url"],
                    value={"items": items, "latest_declared_date": latest,
                           "interpretation": "Company-owned activity; not independent sentiment. Dates are those the page declares."},
                    as_of=latest, retrieved_at=website.get("retrieved_at"), content_sha256=items[0].get("content_sha256"))


def company_jobs_record(profile: dict[str, Any]) -> dict[str, Any]:
    """Hiring signals from the verified site: careers page and embedded schema.org JobPosting."""
    website, value, publishable = _website(profile)
    if not publishable:
        return evidence("company_jobs", "not_found", "company_site_careers",
                        website.get("source_url") or BRREG_BULK_URL,
                        note="No identity-gated company website to read hiring information from")
    careers_pages = []
    postings = []
    for page in [{"url": value.get("final_url"), "title": value.get("title"), "content_sha256": value.get("content_sha256"),
                  "job_postings": value.get("job_postings") or [], "category": None}] + list(value.get("pages") or []):
        if page.get("category") == "careers" and page.get("content_sha256"):
            # The careers page itself is the evidence: its title as fetched (or its URL when it has no title).
            careers_pages.append({"url": page.get("url"), "title": page.get("title"), "content_sha256": page.get("content_sha256"),
                                  "claim_span": " ".join(str(page.get("title") or "").split()) or page.get("url")})
        for job in page.get("job_postings") or []:
            postings.append({**job, "source_url": page.get("url"), "content_sha256": page.get("content_sha256")})
    if not careers_pages and not postings:
        return evidence("company_jobs", "not_found", "company_site_careers", value.get("final_url") or website.get("source_url"),
                        note="Verified site crawled; no careers page or embedded job posting found",
                        retrieved_at=website.get("retrieved_at"))
    source = (postings[0]["source_url"] if postings else careers_pages[0]["url"])
    return evidence("company_jobs", "available", "company_site_careers", source,
                    value={"careers_pages": careers_pages, "job_postings": postings, "open_postings_found": len(postings),
                           "interpretation": "Company-owned hiring pages; a careers page alone does not prove an open vacancy."},
                    retrieved_at=website.get("retrieved_at"))


def wikidata_website_candidate(wikidata: dict[str, Any] | None) -> str | None:
    if not wikidata or wikidata.get("status") != "available":
        return None
    sites = (wikidata.get("value") or {}).get("official_websites") or []
    return sites[0] if len(sites) == 1 else None


def _domain(url: str | None) -> str:
    host = (urllib.parse.urlparse(str(url or "")).hostname or "").casefold()
    return host.removeprefix("www.")


def apply_wikidata_identity_support(profile: dict[str, Any], wikidata: dict[str, Any] | None) -> None:
    """Promote a 'review' website to exact only when an exact-match Wikidata item names the same domain."""
    website, value, _ = _website(profile)
    identity = value.get("identity_assessment")
    if not identity or identity.get("publishable") or website.get("status") != "available":
        return
    if identity.get("requires_org_number"):
        return  # cross-domain redirect or deep-path landing: only the printed org number can prove identity
    if not wikidata or wikidata.get("status") != "available":
        return
    wiki_domains = {_domain(url) for url in (wikidata.get("value") or {}).get("official_websites") or []}
    site_domain = _domain(value.get("final_url") or website.get("source_url"))
    if site_domain and site_domain in wiki_domains and float(identity.get("score") or 0) >= 0.8:
        identity["publishable"] = True
        identity["status"] = "exact"
        identity.setdefault("reasons", []).append(
            "official website (P856) on the Wikidata item whose P2333 equals this organisation number")
        identity.setdefault("promotion_proof", []).append(
            {"type": "wikidata_official_website", "item": (wikidata.get("value") or {}).get("wikidata_item")})
        assessments = value.get("social_link_assessments") or []
        value["social_links"] = [link_with_provenance(item) for item in assessments if item.get("publishable")]
