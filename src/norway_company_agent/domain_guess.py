"""Name-derived website candidates for companies with no registry or Wikidata website.

A guessed domain is only a *candidate*. It is published only when the fetched site prints the
exact organisation number (identity score 1.0), or prints the full legal name together with at least two
independent registry details (postcode+city, street address, phone) in its own footer/contact block. A phone
printed next to a person's name does not count. Name similarity alone never publishes a guess.
DNS resolution is used as a free pre-check so that no HTTP request is spent on dead domains.
"""
from __future__ import annotations

import socket
from typing import Any

from .identity import _tokens

# Names made only of these words are too generic to own a matching domain.
GENERIC_LABEL_TOKENS = {
    "bygg", "service", "services", "eiendom", "holding", "invest", "consulting", "consult", "drift", "handel",
    "transport", "regnskap", "norge", "norway", "nordic", "group", "gruppen", "partner", "partners", "utvikling",
    "management", "solutions", "systems", "tjenester", "entreprenor", "maskin", "elektro", "ror", "data",
}
SKIP_NAME_TOKENS = {"holding", "invest", "investment", "eiendom", "eiendommer", "boligsameie", "sameie", "borettslag"}


def candidate_domains(name: str | None, limit: int = 2) -> list[str]:
    tokens = _tokens(name)
    if not tokens or any(token in SKIP_NAME_TOKENS for token in tokens):
        return []
    if all(token in GENERIC_LABEL_TOKENS for token in tokens):
        return []
    tokens = tokens[:4]
    labels = ["".join(tokens)]
    if len(tokens) > 1:
        labels.append("-".join(tokens))
    domains = []
    for label in labels:
        if 3 <= len(label) <= 63 and label not in {item.split(".")[0] for item in domains}:
            domains.append(f"{label}.no")
    return domains[:limit]


def resolves(host: str) -> bool:
    try:
        return bool(socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP))
    except (socket.gaierror, UnicodeError, OSError):
        return False


# Distinct registry details a guessed domain must show next to the full legal name when the org number is absent.
MIN_GUESS_DETAILS = 2


def guessed_domain_proven(assessment: dict[str, Any]) -> bool:
    """v6: a guessed domain needs the exact org number printed on it (score 1.0). Before v6 the full legal name with two
    registry details also counted; a name/address match on a domain we chose ourselves is no longer proof."""
    return bool(assessment.get("publishable")) and float(assessment.get("score") or 0) >= 1.0 and not assessment.get("group_site")


def guess_website(profile: dict[str, Any], fetch, gate, *, resolver=resolves) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]]]:
    """Try name-derived domains. Returns (accepted_gated_record or None, metrics, audit_trail)."""
    metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
    trail: list[dict[str, Any]] = []
    if profile.get("bankrupt") or profile.get("liquidating") or profile.get("deleted"):
        return None, metrics, trail
    for domain in candidate_domains(profile.get("name")):
        if not resolver(domain):
            trail.append({"candidate": domain, "result": "no_dns"})
            continue
        record, fetched = fetch("https://" + domain + "/", source_type="name_derived_domain_candidate")
        metrics["requests"] += fetched["requests"]
        metrics["bytes"] += fetched["bytes"]
        metrics["latencies_ms"].extend(fetched["latencies_ms"])
        gated = gate(profile, record)
        assessment = gated.get("assessment") or {}
        trail.append({"candidate": domain, "result": record.get("status"), "identity_score": assessment.get("score"),
                      "reasons": assessment.get("reasons")})
        if record.get("status") == "available" and guessed_domain_proven(assessment):
            return gated["website"], metrics, trail
    return None, metrics, trail


# --- v3: ordered website candidates (registry e-mail domain, name variants .no then .com) -----------------------

GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.com", "live.no", "msn.com",
    "yahoo.com", "yahoo.no", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "gmx.com", "gmx.net", "mail.com",
    "protonmail.com", "proton.me", "pm.me", "online.no", "broadpark.no", "getmail.no", "start.no", "frisurf.no", "c2i.net",
    "chello.no", "altibox.no", "lyse.net", "epost.no", "tele2.no", "telia.no", "telenor.no", "netcom.no", "bluezone.no",
    "enivest.net", "tdcadsl.no", "ebnett.no", "haugnett.no", "nextgentel.com", "loqal.no", "kvamnet.no", "hesbynett.no",
    "sensewave.no", "tussa.com", "neasonline.no", "vikenfiber.no", "mimer.no", "post.com", "yandex.com", "mail.ru", "qq.com",
}
MAX_CANDIDATE_FETCHES = 8  # HTTP candidate homepages per company (DNS checks are free)


def registry_email_domain(profile: dict[str, Any]) -> str | None:
    evidence_ = profile.get("evidence") or {}
    row = (evidence_.get("registry") or {}).get("value") or {}
    live = ((evidence_.get("registry_live") or {}).get("value") or {}).get("extras") or {}
    email = str(row.get("epostadresse") or live.get("epostadresse") or "").strip().casefold()
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip(". ")
    if not domain or "." not in domain or domain in GENERIC_EMAIL_DOMAINS:
        return None
    return domain.removeprefix("www.")


def _alt_tokens(name: str | None) -> list[str]:
    """The legal-name tokens with the long Norwegian transliteration (ø -> oe, å -> aa, æ -> ae)."""
    text = str(name or "").translate(str.maketrans({"ø": "oe", "Ø": "OE", "å": "aa", "Å": "AA", "æ": "ae", "Æ": "AE"}))
    return _tokens(text)


def name_labels(name: str | None) -> list[str]:
    """Legal name without legal-form suffix, ø/å/æ as o/a/ae and as oe/aa/ae: joined, hyphenated, first two and
    first distinctive tokens."""
    tokens = _tokens(name)
    if not tokens or any(token in SKIP_NAME_TOKENS for token in tokens) or all(token in GENERIC_LABEL_TOKENS for token in tokens):
        return []
    labels: list[str] = []
    for variant in (tokens[:4], _alt_tokens(name)[:4]):
        if not variant:
            continue
        labels.append("".join(variant))
        if len(variant) > 1:
            labels.append("-".join(variant))
    if len(tokens) > 2:
        labels.append("".join(tokens[:2]))
        labels.append("-".join(tokens[:2]))
    distinctive = [token for token in tokens[:4] if token not in GENERIC_LABEL_TOKENS and not token.isdigit()]
    if len(tokens) > 1 and distinctive and len(distinctive[0]) >= 5:
        labels.append(distinctive[0])
    return [label for index, label in enumerate(labels) if 3 <= len(label) <= 63 and label not in labels[:index]]


def subunit_labels(profile: dict[str, Any]) -> list[str]:
    """Trading names of registered sub-units that differ from the legal name (often the brand on the sign)."""
    own = "".join(_tokens(profile.get("name")))
    labels = []
    for item in (((profile.get("evidence") or {}).get("locations") or {}).get("value") or {}).get("locations") or []:
        for label in name_labels(item.get("name"))[:2]:
            if label != own and label not in labels:
                labels.append(label)
    return labels[:2]


def subunit_registry_domains(profile: dict[str, Any]) -> list[str]:
    """Hosts of the websites registered for the company's sub-units (underenheter): the parent's candidate only if
    the site prints the parent's exact organisation number."""
    import urllib.parse
    domains: list[str] = []
    for item in (((profile.get("evidence") or {}).get("locations") or {}).get("value") or {}).get("locations") or []:
        raw = str(item.get("website") or "").strip()
        if not raw:
            continue
        host = (urllib.parse.urlparse(raw if "://" in raw else "https://" + raw).hostname or "").casefold().removeprefix("www.")
        if host and "." in host and host not in domains:
            domains.append(host)
    return domains[:2]


def website_candidates(profile: dict[str, Any], tried: set[str] | None = None) -> list[dict[str, str]]:
    """Ordered candidates after the registry website and Wikidata: e-mail domain, name variants (.no), sub-unit
    names (.no), then the name variants on .com."""
    tried = {domain.removeprefix("www.") for domain in tried or set()}
    ordered: list[dict[str, str]] = []

    def add(domain: str, method: str) -> None:
        if domain not in tried and domain not in {item["domain"] for item in ordered}:
            ordered.append({"domain": domain, "method": method})

    email_domain = registry_email_domain(profile)
    if email_domain:
        add(email_domain, "registry_email_domain")
    for domain in subunit_registry_domains(profile):
        add(domain, "subunit_registry_website")
    labels = name_labels(profile.get("name"))
    for label in labels:
        add(f"{label}.no", "name_variant")
    subunits = subunit_labels(profile)
    for label in subunits:
        add(f"{label}.no", "subunit_name")
    # .com last, full names only (a single word on .com is almost always someone else): joined and hyphenated name in
    # both transliterations, then the first sub-unit name
    tokens, alt = _tokens(profile.get("name"))[:4], _alt_tokens(profile.get("name"))[:4]
    full = [label for label in ("".join(tokens), "-".join(tokens), "".join(alt), "-".join(alt)) if label in labels]
    for label in full:
        add(f"{label}.com", "name_variant")
    for label in subunits[:1]:
        add(f"{label}.com", "subunit_name")
    return ordered


PROBE_TIMEOUT = 5.0  # seconds per socket operation for a guessed-domain probe (registry-listed sites keep 15 s)


def discover_website(profile: dict[str, Any], fetch, gate, *, resolver=resolves, tried: set[str] | None = None,
                     keep_going=lambda: True, max_fetches: int = MAX_CANDIDATE_FETCHES) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]]]:
    """Probe candidates in order; publish only the exact org number on the homepage/contact/about/legal page (single
    number in that block).
    Group sites are labelled, never published. After acceptance, the site gets the normal crawl for enrichment."""
    metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
    trail: list[dict[str, Any]] = []
    if profile.get("bankrupt") or profile.get("liquidating") or profile.get("deleted"):
        return None, metrics, trail
    fetches = 0
    terms = [token for token in _tokens(profile.get("name")) if len(token) >= 3]

    def spend(fetched: dict[str, Any]) -> None:
        metrics["requests"] += fetched["requests"]
        metrics["bytes"] += fetched["bytes"]
        metrics["latencies_ms"].extend(fetched["latencies_ms"])

    for candidate in website_candidates(profile, tried):
        domain, method = candidate["domain"], candidate["method"]
        if fetches >= max_fetches:
            trail.append({"candidate": domain, "method": method, "result": "skipped_fetch_cap"})
            continue
        if not keep_going():
            trail.append({"candidate": domain, "method": method, "result": "skipped_deadline"})
            break
        host = domain if resolver(domain) else ("www." + domain if resolver("www." + domain) else None)
        if not host:
            trail.append({"candidate": domain, "method": method, "result": "no_dns"})
            continue
        fetches += 1
        source_type = {"registry_email_domain": "registry_email_domain_candidate",
                       "subunit_registry_website": "subunit_registry_website_candidate"}.get(method, "name_derived_domain_candidate")
        # https only: an http:// fallback recovered fetches on the 300 sample but proved no site and doubled p95.
        # A guessed host that resolves but does not answer within PROBE_TIMEOUT is not worth the run's p95
        # (on the smoke batch such hosts sat at the 15 s socket timeout and set p95 at 6-8 s).
        record, fetched = fetch("https://" + host + "/", source_type=source_type, probe=True,
                                probe_terms=None if method in {"registry_email_domain", "subunit_registry_website"} else terms, timeout=PROBE_TIMEOUT,
                                proof_org=str(profile.get("organisation_number") or "") or None)
        spend(fetched)
        gated = gate(profile, record)
        assessment = gated.get("assessment") or {}
        entry = {"candidate": domain, "method": method, "result": record.get("status"), "note": record.get("note") if record.get("status") != "available" else None,
                 "identity_score": assessment.get("score"),
                 "reasons": assessment.get("reasons"), "group_site": assessment.get("group_site"),
                 "pages_checked": [page.get("url") for page in ((record.get("value") or {}).get("pages") or [])],
                 "final_url": (record.get("value") or {}).get("final_url"), "content_sha256": record.get("content_sha256"),
                 "retrieved_at": record.get("retrieved_at")}
        trail.append(entry)
        # v6: no candidate here is the registry's own website entry (e-mail domain, name variants on .no or .com,
        # sub-unit names): only the exact organisation number printed on the site proves it. A name, address or
        # phone match never publishes a domain we chose ourselves (v5 shipped isabellacafe.com, a US cafe, as a lead).
        def proven(result: dict[str, Any]) -> bool:
            if result.get("group_site") or result.get("foreign_country_signals") and float(result.get("score") or 0) < 1.0:
                return False
            return bool(result.get("publishable")) and float(result.get("score") or 0) >= 1.0

        if record.get("status") != "available" or not proven(assessment):
            continue
        # Proven: crawl the accepted site normally (news/careers/people pages) and keep the probe's identity pages.
        probe_value = record.get("value") or {}
        full, fetched = fetch(probe_value.get("final_url") or "https://" + domain + "/", source_type=source_type)
        spend(fetched)
        if full.get("status") == "available":
            value = full.get("value") or {}
            urls = {page.get("url") for page in value.get("pages") or []}
            value["pages"] = (value.get("pages") or []) + [page for page in probe_value.get("pages") or [] if page.get("url") not in urls]
            value["requested_url"] = probe_value.get("requested_url")
            full_gated = gate(profile, full)
            full_assessment = full_gated.get("assessment") or {}
            if proven(full_assessment):
                entry["result"] = "published"
                return full_gated["website"], metrics, trail
        entry["result"] = "published_probe_only"
        return gated["website"], metrics, trail
    return None, metrics, trail
