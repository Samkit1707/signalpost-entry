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
    """A guessed domain needs the exact org number (score 1.0), or the full legal name together with at least two
    independent registry details (postcode+city, street address, phone) in the site's own footer/contact block."""
    if float(assessment.get("score") or 0) >= 1.0:
        return True
    proof = assessment.get("registry_detail_proof") or {}
    return bool(assessment.get("publishable") and proof.get("name_with_detail")
                and len(set(proof.get("details") or [])) >= MIN_GUESS_DETAILS)


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
MAX_CANDIDATE_FETCHES = 6


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


def name_labels(name: str | None) -> list[str]:
    """Legal name without legal-form suffix: joined, hyphenated, first two and first distinctive tokens."""
    tokens = _tokens(name)
    if not tokens or any(token in SKIP_NAME_TOKENS for token in tokens) or all(token in GENERIC_LABEL_TOKENS for token in tokens):
        return []
    tokens = tokens[:4]
    labels = ["".join(tokens)]
    if len(tokens) > 1:
        labels.append("-".join(tokens))
    if len(tokens) > 2:
        labels.append("".join(tokens[:2]))
    distinctive = [token for token in tokens if token not in GENERIC_LABEL_TOKENS and not token.isdigit()]
    if len(tokens) > 1 and distinctive and len(distinctive[0]) >= 5:
        labels.append(distinctive[0])
    return [label for index, label in enumerate(labels) if 3 <= len(label) <= 63 and label not in labels[:index]]


def subunit_labels(profile: dict[str, Any]) -> list[str]:
    """Trading names of registered sub-units that differ from the legal name (often the brand on the sign)."""
    own = "".join(_tokens(profile.get("name")))
    labels = []
    for item in (((profile.get("evidence") or {}).get("locations") or {}).get("value") or {}).get("locations") or []:
        for label in name_labels(item.get("name"))[:1]:
            if label != own and label not in labels:
                labels.append(label)
    return labels[:2]


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
    labels = name_labels(profile.get("name"))
    for label in labels:
        add(f"{label}.no", "name_variant")
    for label in subunit_labels(profile):
        add(f"{label}.no", "subunit_name")
    for label in labels[:2]:
        add(f"{label}.com", "name_variant")
    return ordered


PROBE_TIMEOUT = 5.0  # seconds per socket operation for a guessed-domain probe (registry-listed sites keep 15 s)


def discover_website(profile: dict[str, Any], fetch, gate, *, resolver=resolves, tried: set[str] | None = None,
                     keep_going=lambda: True, max_fetches: int = MAX_CANDIDATE_FETCHES) -> tuple[dict[str, Any] | None, dict[str, Any], list[dict[str, Any]]]:
    """Probe candidates in order; publish only the exact org number on the homepage/contact/about/legal page (single
    number in that block), or the full legal name with two registry details in the site's own footer/contact block.
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
        source_type = "registry_email_domain_candidate" if method == "registry_email_domain" else "name_derived_domain_candidate"
        # https only: an http:// fallback recovered fetches on the 300 sample but proved no site and doubled p95.
        # A guessed host that resolves but does not answer within PROBE_TIMEOUT is not worth the run's p95
        # (on the smoke batch such hosts sat at the 15 s socket timeout and set p95 at 6-8 s).
        record, fetched = fetch("https://" + host + "/", source_type=source_type, probe=True,
                                probe_terms=None if method == "registry_email_domain" else terms, timeout=PROBE_TIMEOUT)
        spend(fetched)
        gated = gate(profile, record)
        assessment = gated.get("assessment") or {}
        entry = {"candidate": domain, "method": method, "result": record.get("status"), "identity_score": assessment.get("score"),
                 "reasons": assessment.get("reasons"), "group_site": assessment.get("group_site"),
                 "pages_checked": [page.get("url") for page in ((record.get("value") or {}).get("pages") or [])],
                 "final_url": (record.get("value") or {}).get("final_url"), "content_sha256": record.get("content_sha256"),
                 "retrieved_at": record.get("retrieved_at")}
        trail.append(entry)
        # Name-derived .com domains are often a foreign parent's multi-country site: only the printed org number proves them.
        org_number_only = method != "registry_email_domain" and not domain.endswith(".no")

        def proven(result: dict[str, Any]) -> bool:
            if result.get("group_site"):
                return False
            return float(result.get("score") or 0) >= 1.0 if org_number_only else guessed_domain_proven(result)

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
