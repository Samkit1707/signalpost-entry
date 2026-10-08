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
