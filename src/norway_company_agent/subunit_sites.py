"""Site check for a sub-unit's registered website (`hjemmeside` in its underenhet record).

The registry entry alone is not enough to publish a sub-unit's website: the domain must resolve in DNS, the site must
not redirect to another domain, and a page on it must print the sub-unit's or the parent's organisation number, or
the exact legal name of either. Anything else stays in the diagnostics file.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .domain_filings import ambiguity, domain_resolves, registered_domain
from .evidence import utc_now
from .website import page_shows_org


def _folded(text: str) -> str:
    return " ".join(str(text or "").replace(" ", " ").split()).casefold()


def name_on_page(page: dict[str, Any], name: str | None) -> str | None:
    """The page text line that prints `name` exactly (case and spacing aside), or None."""
    wanted = _folded(name or "")
    if len(wanted) < 4:
        return None
    pattern = re.compile(r"(?<![\w])" + re.escape(wanted) + r"(?![\w])")
    for text in (page.get("title"), page.get("identity_text_excerpt"), page.get("main_text_excerpt"), *(page.get("org_number_lines") or [])):
        folded = _folded(text or "")
        match = pattern.search(folded)
        if match:
            return folded[max(0, match.start() - 80): match.end() + 80]
    return None


def site_proof(value: dict[str, Any], subunit: dict[str, Any], parent: dict[str, Any]) -> dict[str, Any] | None:
    """First page that prints the sub-unit's or the parent's org number, then the exact legal name of either."""
    pages = [value, *(value.get("pages") or [])[1:]]
    for org, who in ((subunit.get("organisation_number"), "sub-unit"), (parent.get("organisation_number"), "parent")):
        for page in pages:
            if org and page_shows_org({**page, "org_number_lines": page.get("org_number_lines") or []}, str(org)):
                return {"kind": "org_number", "who": who, "value": org, "page_url": page.get("final_url") or page.get("url"),
                        "content_sha256": page.get("content_sha256")}
    for name, who in ((subunit.get("name"), "sub-unit"), (parent.get("name"), "parent")):
        for page in pages:
            line = name_on_page(page, name)
            if line:
                return {"kind": "legal_name", "who": who, "value": name, "page_url": page.get("final_url") or page.get("url"),
                        "content_sha256": page.get("content_sha256"), "line": line[:240]}
    return None


def check_subunit_site(raw: str, subunit: dict[str, Any], parent: dict[str, Any], fetch: Callable[..., Any],
                       resolver: Callable[[str], tuple[bool, str]] = domain_resolves) -> tuple[dict[str, Any], dict[str, Any]]:
    """(result, metrics). result["ok"] is True only with DNS, no off-domain redirect and an on-site proof."""
    result: dict[str, Any] = {"url": raw, "checked_at": utc_now(), "ok": False}
    metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
    resolves, how = resolver(raw)
    result["dns"] = how
    if not resolves:
        result["reason"] = how
        return result, metrics
    record, metrics = fetch(raw, source_type="subunit_registry_website", probe=True,
                            proof_org=str(subunit.get("organisation_number") or "") or None)
    result["fetch_status"] = record.get("status")
    if record.get("status") != "available":
        result["reason"] = f"site could not be read ({record.get('note') or record.get('status')}), so nothing on it proves the link"
        return result, metrics
    value = record.get("value") or {}
    result.update({"final_url": value.get("final_url"), "content_sha256": value.get("content_sha256"),
                   "retrieved_at": record.get("retrieved_at")})
    filed, landed = registered_domain(raw), registered_domain(value.get("final_url"))
    if landed != filed:
        result["reason"] = f"{filed} redirects to {landed}, another domain"
        return result, metrics
    proof = site_proof(value, subunit, parent)
    if not proof:
        result["reason"] = ("no page checked prints the sub-unit's or the parent's organisation number or exact legal name "
                            f"({len(value.get('pages') or [])} page(s))")
        return result, metrics
    result.update({"ok": True, "proof": proof})
    return result, metrics


def verify_subunit_sites(profile: dict[str, Any], fetch: Callable[..., Any], filings: dict[str, int] | None,
                         resolver: Callable[[str], tuple[bool, str]] = domain_resolves) -> dict[str, Any]:
    """Check every sub-unit website that is not on the company's verified domain and not ambiguous; results go to
    profile["subunit_site_checks"] (url -> result). Returns the request metrics."""
    from .public import _publishable  # local: public imports this module's results
    verified = ""
    if _publishable(profile):
        value = ((profile.get("evidence") or {}).get("website") or {}).get("value") or {}
        verified = (value.get("registered_domain") or registered_domain(value.get("final_url"))).casefold()
    reasons = profile.get("domain_filings") or {}
    parent = {"organisation_number": profile.get("organisation_number"), "name": profile.get("name")}
    checks: dict[str, Any] = {}
    total = {"requests": 0, "bytes": 0, "latencies_ms": []}
    for item in (((profile.get("evidence") or {}).get("locations") or {}).get("value") or {}).get("locations") or []:
        raw = str(item.get("website") or "").strip()
        domain = registered_domain(raw)
        if not raw or raw in checks or (verified and domain == verified):
            continue
        if (reasons[domain] if domain in reasons else ambiguity(raw, filings)):
            continue  # withheld as ambiguous before any fetch
        result, metrics = check_subunit_site(raw, item, parent, fetch, resolver)
        checks[raw] = result
        for key in ("requests", "bytes"):
            total[key] += metrics.get(key, 0)
        total["latencies_ms"] += metrics.get("latencies_ms", [])
    profile["subunit_site_checks"] = checks
    return total
