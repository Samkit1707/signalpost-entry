"""v6: what leaves the agent. Only verified links are ever written to an envelope, a saved profile or the site.

Root cause of v5's wrong-company findings: the envelope carried our *rejected* candidates next to the published claims
(`profile.website_discovery.candidates[].final_url` held isabellacafe.com, a US cafe, with "all legal-name tokens
appear"; the website record's raw JSON-LD `sameAs` and the claims' `not_published` lists held twitter.com/squarespace).
A reader that collects links from the whole envelope counts those as our answers. So:

1. `public_profile` rebuilds the profile from a whitelist: registry/official records as they are, the website record
   only when the site passed the identity gate (and then without raw page text, JSON-LD or unassessed links),
   enrichment records without `not_published`/`checked` diagnostics, and no candidate trail.
2. `strip_claim_diagnostics` drops `not_published` lists and notes that quote rejected URLs from claims.
3. `guard_urls` walks the finished envelope and removes every link or domain that is not on an allowed host (the
   registry, Wikidata, the verified site, a published profile, an exact Wikidata sitelink). Anything it has to remove is
   counted in the run report; a clean run removes nothing.
4. Everything rejected goes to a separate diagnostics file for the audit, never into the envelope.
"""
from __future__ import annotations

import copy
import re
import urllib.parse
from typing import Any

from .website import normalize_social_url

BRREG_BULK_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"
OFFICIAL_HOST_SUFFIXES = ("brreg.no", "wikidata.org", "schema.org", "w3.org")
# Website-value keys kept for a verified site: what claims, the site and the audit read. Raw page text, JSON-LD and
# link lists (which include links the gate rejected) stay out.
WEBSITE_KEEP = ("requested_url", "final_url", "registered_domain", "title", "description", "content_sha256", "extraction_state",
                "identity_assessment", "social_links", "site_contact", "job_postings", "declared_dates", "crawl_errors")
PAGE_KEEP = ("url", "title", "content_sha256", "category", "declared_dates", "job_postings")
IDENTITY_KEEP = ("status", "score", "publishable", "legal_name_tokens", "matched_tokens", "reasons", "registry_listed",
                 "requires_org_number", "method", "promotion_proof")
DIAGNOSTIC_KEYS = ("not_published", "checked", "social_link_assessments", "discovered_social_links", "quarantined")

TLDS = ("no|com|net|org|io|se|dk|fi|de|uk|co|eu|info|biz|app|dev|nu|me|tv|us|shop|online|site|store|xyz|ai|is|nl|fr|es|it|"
        "pl|ee|lt|lv|ru|ch|at|be|cz|cloud|tech|digital|agency|design|media|studio|group|as|global|world|life|live|page|web")
URL_RE = re.compile(r"(?:https?://[^\s\"'<>()\[\]{}]+)|(?:(?<![\w@.-])(?:www\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:"
                    + TLDS + r")(?![\w-])(?:/[^\s\"'<>()\[\]{}]*)?)", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.I)
WITHHELD = "[unverified link withheld]"


def _host(url: str) -> str:
    text = url if re.match(r"^[a-z]+://", url, re.I) else "https://" + url
    try:
        host = (urllib.parse.urlparse(text).hostname or "").casefold()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _registered(host: str) -> str:
    from .website import _registered_domain
    return _registered_domain("https://" + host + "/") if host else ""


def _publishable(profile: dict[str, Any]) -> bool:
    website = (profile.get("evidence") or {}).get("website") or {}
    return website.get("status") == "available" and bool(((website.get("value") or {}).get("identity_assessment") or {}).get("publishable"))


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    record = {key: item for key, item in record.items() if key not in DIAGNOSTIC_KEYS}
    value = record.get("value")
    if isinstance(value, dict):
        record["value"] = {key: item for key, item in value.items() if key not in DIAGNOSTIC_KEYS}
    return record


def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """A copy of the profile holding only verified, publishable material (see module docstring)."""
    out = copy.deepcopy({key: item for key, item in profile.items() if key not in {"website_discovery"}})
    records = out.get("evidence") or {}
    verified = _publishable(profile)
    for module, record in list(records.items()):
        if not isinstance(record, dict):
            continue
        if module == "website":
            if verified:
                value = record.get("value") or {}
                kept = {key: value[key] for key in WEBSITE_KEEP if key in value}
                kept["identity_assessment"] = {key: (value.get("identity_assessment") or {}).get(key) for key in IDENTITY_KEEP}
                kept["pages"] = [{key: page.get(key) for key in PAGE_KEEP if page.get(key) is not None}
                                 for page in value.get("pages") or []]
                records[module] = {**_clean_record(record), "value": kept}
            else:
                tried = record.get("status") == "available" or bool(profile.get("website_discovery"))
                records[module] = {
                    "field": "website", "status": "not_found", "source_type": "website_identity_gate",
                    "source_class": "official_registry", "source_url": BRREG_BULK_URL,
                    "retrieved_at": record.get("retrieved_at"), "value": None, "as_of": None,
                    "note": ("candidate site(s) were checked and none proved it is this company's own "
                             "(org number, or registry-listed site with full legal name); nothing from them is published")
                    if tried else (record.get("note") or "no website found"),
                    "content_sha256": None, "source_row_key": record.get("source_row_key"), "effective_at": None}
        else:
            records[module] = _clean_record(record)
    if not verified:
        out["website"] = None  # the registry's website entry did not prove identity: not published
    trail = (profile.get("website_discovery") or {}).get("candidates") or []
    winner = next((item for item in trail if str(item.get("result") or "").startswith("published")), None)
    if trail:
        out["website_discovery"] = {"candidates_checked": len(trail),
                                    "published_candidate": {"method": winner.get("method"), "domain": winner.get("candidate")}
                                    if winner and verified else None}
    return out


def subunit_site_split(profile: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sub-unit registry websites: (kept, withheld). A sub-unit's site is labelled on its location claim only when it is
    the same registered domain as the company's verified website; every other one is unverified for this company and
    goes to the diagnostics file, never the envelope."""
    verified_domain = ""
    if _publishable(profile):
        value = ((profile.get("evidence") or {}).get("website") or {}).get("value") or {}
        verified_domain = (value.get("registered_domain") or _registered(_host(str(value.get("final_url") or "")))).casefold()
    kept, withheld = [], []
    for item in (((profile.get("evidence") or {}).get("locations") or {}).get("value") or {}).get("locations") or []:
        raw = str(item.get("website") or "").strip()
        if not raw:
            continue
        domain = (_registered(_host(raw)) or _host(raw)).casefold()
        entry = {"organisation_number": item.get("organisation_number"), "name": item.get("name"), "website": raw}
        (kept if verified_domain and domain == verified_domain else withheld).append(entry)
    return kept, withheld


_SUBUNIT_SITE_SPAN = re.compile(r'(?:;\s*)?\$\._embedded\.underenheter\[\d+\]\.hjemmeside="[^"]*"')


def restrict_subunit_websites(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Remove, in place, every sub-unit website that is not the company's verified site (value and cited span)."""
    _, withheld = subunit_site_split(profile)
    drop = {(item["organisation_number"], item["website"]) for item in withheld}
    for item in (((profile.get("evidence") or {}).get("locations") or {}).get("value") or {}).get("locations") or []:
        if (item.get("organisation_number"), str(item.get("website") or "").strip()) in drop:
            item["website"] = None
            item["claim_span"] = _SUBUNIT_SITE_SPAN.sub("", str(item.get("claim_span") or ""))
    return withheld


def strip_claim_diagnostics(claims: list[dict[str, Any]]) -> None:
    """Claims never list rejected candidates: drop `not_published`, and notes that quote them."""
    for claim in claims:
        claim.pop("not_published", None)
        if "found but not published" in str(claim.get("note") or ""):
            claim.pop("note", None)


def allowed_links(envelope: dict[str, Any]) -> dict[str, set[str]]:
    """Hosts and exact profile URLs this envelope may mention."""
    profile = envelope.get("profile") or {}
    hosts: set[str] = set()
    profiles: set[str] = set()
    emails: set[str] = set()
    website = (profile.get("evidence") or {}).get("website") or {}
    if _publishable(profile):
        final = (website.get("value") or {}).get("final_url") or ""
        domain = (website.get("value") or {}).get("registered_domain") or _registered(_host(final))
        if domain:
            hosts.add(domain.casefold())
    for claim in envelope.get("claims") or []:
        if claim.get("availability") != "available":
            continue
        values = claim.get("value") if isinstance(claim.get("value"), list) else [claim.get("value")]
        for value in values:
            url = value.get("url") if isinstance(value, dict) else None
            if not url:
                continue
            social = normalize_social_url(str(url))
            if social:
                profiles.add(social["url"])
            elif claim.get("field") in {"external_profile", "external_profiles", "social_profiles"} and "wikipedia.org" in str(url):
                hosts.add(_host(str(url)))
        if claim.get("field") == "job_posting" and isinstance(claim.get("value"), dict):
            from .website import ATS_HOSTS
            host = _host(str(claim["value"].get("url") or ""))
            if any(host == item or host.endswith("." + item) for item in ATS_HOSTS) and hosts:
                hosts.add(host)  # the company's own posting on a recruiting system, linked from its verified careers page
        if claim.get("field") == "registry_email" and isinstance(claim.get("value"), str):
            emails.add(claim["value"].casefold())
    registry = ((profile.get("evidence") or {}).get("registry") or {}).get("value") or {}
    if isinstance(registry, dict) and registry.get("epostadresse"):
        emails.add(str(registry["epostadresse"]).strip().casefold())
    return {"hosts": hosts, "profiles": profiles, "emails": emails}


# Norwegian abbreviations that look like domains: "(org.no. 913 365 798)" is "organisation number", not a link.
NOT_LINKS = {"org.no", "org.nr", "orgnr.no", "foretaksnr.no"}


def _url_allowed(url: str, allowed: dict[str, set[str]]) -> bool:
    if url.casefold().startswith(("about:", "mailto:")) or url.casefold() in NOT_LINKS:
        return True
    host = _host(url)
    if not host:
        return True
    if any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOST_SUFFIXES):
        return True
    social = normalize_social_url(url if "://" in url else "https://" + url)
    if social:
        return social["url"] in allowed["profiles"]
    if any(host == item or host.endswith("." + item) for item in allowed["hosts"]):
        return True
    return False


def _clean_text(text: str, allowed: dict[str, set[str]], removed: list[str]) -> str:
    def email_ok(match: re.Match[str]) -> bool:
        domain = match.group(1).casefold()
        return match.group(0).casefold() in allowed["emails"] or any(domain == item or domain.endswith("." + item) for item in allowed["hosts"])

    spans = [(m.start(), m.end()) for m in EMAIL_RE.finditer(text) if email_ok(m)]

    def replace(match: re.Match[str]) -> str:
        if any(start <= match.start() < end for start, end in spans):
            return match.group(0)
        url = match.group(0).rstrip(".,;:")
        if _url_allowed(url, allowed):
            return match.group(0)
        # an e-mail address not on an allowed domain is withheld whole
        removed.append(url)
        return WITHHELD + match.group(0)[len(url):]

    cleaned = URL_RE.sub(replace, text)
    for match in list(EMAIL_RE.finditer(cleaned)):
        if not email_ok(match):
            removed.append(match.group(0))
            cleaned = cleaned.replace(match.group(0), WITHHELD)
    return cleaned


LINK_KEYS = {"url", "source_url", "item_url", "page_url", "final_url", "requested_url"}
# Registry values are the company's own registration and may name any e-mail domain; they are cited, never guessed.
REGISTRY_VALUE_FIELDS = {"registry_email"}


def guard_urls(envelope: dict[str, Any]) -> list[dict[str, str]]:
    """Remove, in place, every link or domain the envelope may not carry. Returns what was removed (path, text)."""
    allowed = allowed_links(envelope)
    removed_log: list[dict[str, str]] = []

    def walk(node: Any, path: str, in_list: bool = False) -> Any:
        if isinstance(node, dict):
            out = {}
            for key, item in node.items():
                child = f"{path}.{key}"
                if key in LINK_KEYS and isinstance(item, str) and item and not _url_allowed(item, allowed):
                    removed_log.append({"path": child, "text": item})
                    if in_list:
                        return None  # a list item (link, page, evidence entry) whose own link is not allowed goes whole
                    out[key] = None
                    continue
                out[key] = walk(item, child)
            return out
        if isinstance(node, list):
            items = [walk(item, f"{path}[{index}]", True) for index, item in enumerate(node)]
            return [item for original, item in zip(node, items) if not (item is None and isinstance(original, dict))]
        if isinstance(node, str):
            removed: list[str] = []
            cleaned = _clean_text(node, allowed, removed)
            removed_log.extend({"path": path, "text": text} for text in removed)
            return cleaned
        return node

    for key in list(envelope.keys()):
        if key in {"run_id", "organisation_number", "state", "started_at", "completed_at", "modules"}:
            continue
        if key == "claims":
            kept = []
            for index, claim in enumerate(envelope["claims"]):
                if claim.get("field") in REGISTRY_VALUE_FIELDS:
                    kept.append(claim)
                    continue
                before = len(removed_log)
                cleaned = walk(claim, f"claims[{index}]", True)
                if cleaned is not None and len(removed_log) > before and claim.get("availability") == "available":
                    cleaned = None  # part of its value was an unverified link: the claim is not published at all
                if cleaned is None:
                    # a claim whose own link is not allowed: keep the field, publish nothing
                    cleaned = {"field": claim.get("field"), "value": None, "availability": "not_available", "confidence": None,
                               "as_of": None, "evidence_ids": [], "module": claim.get("module"),
                               "note": "withheld: link is not on the verified site or a verified profile"}
                kept.append(cleaned)
            envelope["claims"] = kept
        else:
            cleaned = walk(envelope[key], key)
            envelope[key] = cleaned if cleaned is not None else ([] if isinstance(envelope[key], list) else None)
    # evidence entries removed above leave no dangling ids; a published claim left without evidence is withheld
    ids = {item.get("id") for item in envelope.get("evidence") or [] if isinstance(item, dict)}
    for claim in envelope.get("claims") or []:
        before = list(claim.get("evidence_ids") or [])
        claim["evidence_ids"] = [item for item in before if item in ids]
        if claim.get("availability") == "available" and before and not claim["evidence_ids"]:
            claim.update({"value": None, "availability": "not_available", "confidence": None, "as_of": None,
                          "note": "withheld: its source is not the verified site or a verified profile"})
            for key in ("confidence_level", "proof"):
                claim.pop(key, None)
    return removed_log


def diagnostics_record(profile: dict[str, Any]) -> dict[str, Any]:
    """Everything the gate rejected for one company, with the reason: for the audit file, never the envelope."""
    records = profile.get("evidence") or {}
    website = records.get("website") or {}
    value = website.get("value") or {}
    rejected_sites = []
    if website.get("status") == "available" and not _publishable(profile):
        identity = value.get("identity_assessment") or {}
        rejected_sites.append({"url": value.get("final_url") or website.get("source_url"), "source_type": website.get("source_type"),
                               "score": identity.get("score"), "reasons": identity.get("reasons")})
    for item in (profile.get("website_discovery") or {}).get("candidates") or []:
        if not str(item.get("result") or "").startswith("published") and item.get("final_url"):
            rejected_sites.append({"url": item.get("final_url"), "source_type": item.get("method"), "score": item.get("identity_score"),
                                   "reasons": item.get("reasons")})
    rejected_links = []
    for link in value.get("social_link_assessments") or []:
        if not link.get("publishable") or not _publishable(profile):
            rejected_links.append({"url": link.get("url"), "location": link.get("location"),
                                   "reason": link.get("reason") if link.get("publishable") is False else "site not verified"})
    for module in ("external_profiles", "company_activity", "dated_news"):
        record = records.get(module) or {}
        for item in ((record.get("value") or {}) if isinstance(record.get("value"), dict) else {}).get("not_published") or \
                (record.get("checked") or {}).get("not_published") or []:
            rejected_links.append({"module": module, "url": item.get("url"), "reason": item.get("reason")})
    subunit_sites = subunit_site_split(profile)[1]
    return {"organisation_number": profile.get("organisation_number"), "name": profile.get("name"),
            "subunit_sites_not_labelled": [{**item, "reason": "not the same domain as the company's verified website"}
                                           for item in subunit_sites],
            "website": {"published": _publishable(profile), "url": value.get("final_url") if _publishable(profile) else None,
                        "source_type": website.get("source_type"),
                        "identity": value.get("identity_assessment")},
            "candidates": (profile.get("website_discovery") or {}).get("candidates") or [],
            "rejected_sites": rejected_sites, "rejected_links": rejected_links}
