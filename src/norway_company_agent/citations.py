"""Exact citation spans and the run-level citation, state and site-ownership checks (v4).

Every evidence entry carries the SHA-256 of the response it was read from and a `claim_span`:
- `field_path` (registry bulk CSV, registry/API JSON, Wikidata SPARQL JSON): the exact column or JSON path and its
  value as returned, e.g. `row[organisasjonsnummer=123456789].navn="EXAMPLE AS"` or `$.konkurs=false`;
- `quote`: text copied from the fetched page (visible text with whitespace collapsed, or the raw attribute such as
  `href="mailto:post@example.no"`, or a JSON-LD path and value);
- `absence`: for a claim with no value, what was checked and found empty.

`check_envelopes` fails the run report when a published claim lacks a hash or a span, when a span does not contain
the claim's value, when a state and a value disagree, or when anything from a site that is not the company's own
(group/parent or unverified site) is attached to the company.
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
from typing import Any, Iterable

HASH = re.compile(r"^[0-9a-f]{64}$")
SPAN_KINDS = {"field_path", "quote", "absence"}
NOT_AVAILABLE = {"not_available", "unavailable", "ambiguous", "blocked", "not_applicable", "failed"}
# Claims a site that is not the company's own must never produce (module, fields).
SITE_DERIVED = {"site_email", "site_phone", "site_address", "site_logo", "careers_page", "job_posting", "company_jobs",
                "company_activity", "dated_news"}
# Raw site content that must not stay attached to a website record that is not the company's own.
QUARANTINED_KEYS = ("social_links", "discovered_social_links", "social_link_assessments", "site_contact",
                    "structured_organisations", "job_postings", "news_items", "feed_links", "declared_dates",
                    "description", "main_text_excerpt")
# Derived classifications: the span cites the inputs, the value is a label not printed by the source.
DERIVED_FIELDS = {"accounting_obligation", "registry_status", "vat_registered", "register_membership", "official_website"}


def collapse(text: Any) -> str:
    return " ".join(str(text or "").split())


def _literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def csv_span(org: str, pairs: Iterable[tuple[str, Any]]) -> str:
    """Columns of one bulk-CSV row, as raw cell text: row[organisasjonsnummer=X].col="raw"."""
    return "; ".join(f"row[organisasjonsnummer={org}].{column}={_literal('' if raw is None else str(raw))}" for column, raw in pairs)


def json_span(pairs: Iterable[tuple[str, Any]]) -> str:
    """JSON paths and the values found there, exactly as parsed from the response."""
    return "; ".join(f"{path}={_literal(value)}" for path, value in pairs)


def quote_around(text: str, start: int, end: int, before: int = 80, after: int = 60) -> str:
    return collapse(text[max(0, start - before):end + after])


def _fold(text: Any) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return text


def _tokens(text: Any) -> list[str]:
    return re.findall(r"\w+", _fold(text))


def _numbers(text: str) -> set[float]:
    found = set()
    for raw in re.findall(r"-?\d+(?:\.\d+)?", text):
        try:
            found.add(float(raw))
        except ValueError:
            pass
    return found


def _digits(text: Any) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _handle(url: str) -> str:
    parts = [part for part in urllib.parse.urlparse(str(url or "")).path.split("/") if part]
    return urllib.parse.unquote(parts[-1]).lstrip("@") if parts else str(url or "")


def _text_terms(value: Any, limit: int = 120) -> list[str]:
    return _tokens(collapse(value)[:limit])


def expected(field: str, value: Any, organisation_number: str = "", legal_name: str = "") -> dict[str, Any]:
    """What a span must contain to support this claim value: tokens, numbers and/or digit strings."""
    need: dict[str, Any] = {"tokens": [], "numbers": [], "digits": [], "any_digits": []}
    item = value if isinstance(value, dict) else {}
    if field in {"legal_name", "legal_form", "business_address", "postal_address", "registry_email", "site_email",
                 "site_address", "registered_purpose", "registered_activity", "site_logo"}:
        need["tokens"] = _text_terms(value)
    elif field in {"industry", "nace_code", "institutional_sector"}:
        need["tokens"] = _tokens(item.get("code"))
    elif field in {"founding_date", "registration_date"}:
        need["tokens"] = _tokens(value)
    elif field == "auditor_opt_out":
        need["tokens"] = _tokens(item.get("date"))
    elif field == "share_capital":
        need["numbers"] = [item.get("amount")]
    elif field in {"annual_revenue", "operating_result", "annual_result", "equity", "total_assets"}:
        need["numbers"] = [item.get("amount")]
    elif field == "latest_annual_accounts":
        need["numbers"] = [item.get(key) for key in ("revenue", "operating_result", "annual_result", "equity", "assets")
                           if item.get(key) is not None]
    elif field == "registered_employees":
        need["numbers"] = [value]
    elif field in {"registry_phone", "site_phone"}:
        need["digits"] = [_digits(item.get("number") if isinstance(value, dict) else value)]
    elif field == "parent_entity":
        need["digits"] = [item.get("organisation_number")]
    elif field in {"role"}:
        need["tokens"] = _text_terms(item.get("name"))
    elif field == "roles":
        need["tokens"] = [token for role in value or [] for token in _text_terms(" ".join(role.get("name"))
                                                                              if isinstance(role.get("name"), list) else role.get("name"))]
    elif field == "related_website":
        need["digits"] = [item.get("subunit_organisation_number")]
        need["tokens"] = [token for token in _tokens(urllib.parse.urlparse(str(item.get("url"))).hostname) if token != "www"]
    elif field == "registry_event":
        need["digits"] = [item.get("organisation_number")]
        field = str(item.get("field") or "").split(".")[0]
        need["tokens"] = _tokens(field) if field and item.get("entity") != "roles" else []  # role events carry dates only
    elif field == "registered_location":
        need["digits"] = [item.get("organisation_number")]
    elif field == "registered_locations":
        need["digits"] = [entry.get("organisation_number") for entry in value or []]
    elif field == "group_relationship":
        need["digits"] = [item.get("organisation_number")]
    elif field == "group_structure":
        need["digits"] = [entry.get("organisation_number") for entry in value or []]
    elif field == "group_website":
        need["any_digits"] = list(item.get("group_organisation_numbers") or [])
    elif field == "wikidata_item":
        need["tokens"] = _tokens(_handle(item.get("item")))
    elif field == "external_profile":
        need["tokens"] = _tokens(_handle(item.get("url")))
    elif field in {"external_profiles", "social_profiles"}:
        need["tokens"] = [token for entry in value or [] for token in _tokens(_handle(entry.get("url")))]
    elif field == "careers_page":
        need["tokens"] = _text_terms(item.get("title")) or _tokens(urllib.parse.urlparse(str(item.get("url"))).path)
    elif field == "job_posting":
        need["tokens"] = _text_terms(item.get("title"))
    elif field == "dated_news":
        need["tokens"] = _text_terms(item.get("title"))
    elif field == "company_activity":
        need["tokens"] = [token for entry in value or [] for token in _tokens(entry.get("latest_declared_date"))]
    elif field == "company_jobs":
        need["tokens"] = [token for entry in item.get("job_postings") or [] for token in _text_terms(entry.get("title"))]
    elif field == "official_website":
        need["any_digits"] = [organisation_number]
        need["name_tokens"] = _tokens(legal_name)
    elif field == "registry_status":
        need["tokens"] = ["slettedato"] if item.get("deleted") else ["konkurs", "underavvikling"]
    elif field in {"vat_registered", "register_membership"}:
        need["tokens"] = [str(item.get("registered")).lower()]
    return need


def span_supports(field: str, value: Any, spans: list[str], organisation_number: str = "", legal_name: str = "") -> bool:
    """True when the joined spans of a claim's evidence contain the claim's value."""
    if field == "accounting_obligation":
        return bool(spans)
    need = expected(field, value, organisation_number, legal_name)
    text = " ".join(spans)
    text = f"{text} {urllib.parse.unquote(text)}"  # hrefs keep percent-encoding (J%C3%86DER) of the decoded value
    tokens, numbers, digits = set(_tokens(text)), _numbers(text.replace(" ", "")) | _numbers(text), _digits(text)
    folded = _fold(text)
    if need["tokens"] and not all(token in tokens or token in folded for token in need["tokens"]):
        return False
    if need["numbers"] and not all(number is not None and float(number) in numbers for number in need["numbers"]):
        return False
    if need["digits"] and not all(entry and str(entry) in digits for entry in need["digits"]):
        return False
    if need["any_digits"]:
        if any(entry and str(entry) in digits for entry in need["any_digits"]):
            return True
        # Sites verified by name: the quoted text holds the legal name without its legal-form word (AS, ASA, ...),
        # compared the way the identity gate compares names (ø/å/æ folded).
        from .identity import _tokens as name_tokens
        name = name_tokens(legal_name)
        return bool(name) and set(name) <= set(name_tokens(text))
    return True


def _empty(value: Any) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return True
    return False


def _website_owned(envelope: dict[str, Any]) -> bool:
    record = ((envelope.get("profile") or {}).get("evidence") or {}).get("website") or {}
    identity = (record.get("value") or {}).get("identity_assessment") or {}
    return record.get("status") == "available" and bool(identity.get("publishable"))


def envelope_problems(envelope: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    org = envelope.get("organisation_number")
    profile = envelope.get("profile") or {}
    evidence = {item.get("id"): item for item in envelope.get("evidence") or []}
    problems: dict[str, list[dict[str, Any]]] = {"citation": [], "state": [], "site_ownership": []}
    legal_name = profile.get("name") or ""

    def flag(kind: str, claim: dict[str, Any] | None, problem: str, **extra: Any) -> None:
        if claim is not None and "claim_index" not in extra:
            index = next((i for i, item in enumerate(envelope.get("claims") or []) if item is claim), None)
            if index is not None:
                extra["claim_index"] = index
        problems[kind].append({"organisation_number": org, "field": (claim or {}).get("field"), "problem": problem, **extra})

    owned = _website_owned(envelope)
    for index, claim in enumerate(envelope.get("claims") or []):
        state, value, published = claim.get("availability"), claim.get("value"), False
        if state == "available" and _empty(value):
            flag("state", claim, "available_without_value")
        elif state != "available" and not _empty(value):
            flag("state", claim, f"{state}_with_value")
        elif state == "available":
            published = True
        ids = claim.get("evidence_ids") or []
        if published and not ids:
            flag("citation", claim, "no_evidence")
        spans = []
        for evidence_id in ids:
            item = evidence.get(evidence_id)
            if item is None:
                flag("citation", claim, "dangling_evidence_id", evidence_id=evidence_id)
                continue
            if not HASH.match(str(item.get("content_sha256") or "")):
                flag("citation", claim, "missing_hash", evidence_id=evidence_id, published=published)
            if not collapse(item.get("claim_span")):
                flag("citation", claim, "missing_span", evidence_id=evidence_id, published=published)
            elif published and item.get("span_kind") == "absence":
                flag("citation", claim, "published_claim_cites_absence", evidence_id=evidence_id)
            else:
                spans.append(str(item.get("claim_span")))
        if published and spans and not span_supports(claim["field"], value, spans, str(org or ""), legal_name):
            flag("citation", claim, "span_does_not_contain_value", evidence_ids=ids)
        if published and not owned and (claim.get("field") in SITE_DERIVED or (
                claim.get("field") in {"external_profile", "external_profiles", "social_profiles"}
                and any("wikidata" not in str((evidence.get(i) or {}).get("source_url") or "") for i in ids))):
            flag("site_ownership", claim, "claim_from_site_that_is_not_the_companys_own")
    for module, record in (profile.get("evidence") or {}).items():
        status, value = record.get("status"), record.get("value")
        if status != "available" and not _empty(value):
            flag("state", {"field": module}, f"record_{status}_with_value")
        if status == "available" and isinstance(value, dict) and value and all(_empty(item) for item in value.values()):
            flag("state", {"field": module}, "record_available_but_empty")
    website = (profile.get("evidence") or {}).get("website") or {}
    if website.get("status") == "available" and not owned:
        value = website.get("value") or {}
        leaked = [key for key in QUARANTINED_KEYS if not _empty(value.get(key)) and key != "description"]
        leaked += [f"pages[{index}].{key}" for index, page in enumerate(value.get("pages") or [])
                   for key in ("site_contact", "job_postings", "news_items", "main_text_excerpt") if not _empty(page.get(key))]
        if leaked:
            flag("site_ownership", {"field": "website"}, "content_kept_from_site_that_is_not_the_companys_own", keys=leaked)
    return problems


CONTRACT_STATES = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}


def repair_records(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Make module records honest before states are derived: a non-available record never carries a value (moved to
    `checked`), and an available record with nothing in it becomes not_found. Returns what was changed."""
    changed = []
    for module, record in (profile.get("evidence") or {}).items():
        status, value = record.get("status"), record.get("value")
        if status != "available" and not _empty(value):
            record["checked"], record["value"] = record.get("checked") or value, None
            changed.append({"module": module, "problem": f"record_{status}_with_value", "action": "value moved to checked"})
        elif status == "available" and (_empty(value) or (isinstance(value, dict) and all(_empty(item) for item in value.values()))):
            record["status"], record["value"] = "not_found", None
            record["note"] = record.get("note") or "source answered with no content"
            changed.append({"module": module, "problem": "record_available_but_empty", "action": "status set to not_found"})
    return changed


def withhold_problem_claims(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Never fail a run on a claim: a claim with a citation, state or site-ownership problem is withheld in place
    (value removed; `ambiguous` when a value existed but could not be cited or is not provably this company's,
    `not_available` when it had no value) with a `note`, and the action is returned for the run report."""
    claims = envelope.get("claims") or []
    actions: list[dict[str, Any]] = []
    done: set[int] = set()
    for kind, items in envelope_problems(envelope).items():
        for item in items:
            index = item.get("claim_index")
            if index is None or index in done:
                continue
            done.add(index)
            claim = claims[index]
            had_value = not _empty(claim.get("value"))
            claim["availability"] = "ambiguous" if had_value else ("not_available" if claim.get("availability") == "available"
                                                                    else claim.get("availability"))
            if claim["availability"] not in CONTRACT_STATES:
                claim["availability"] = "ambiguous"
            claim["value"], claim["confidence"], claim["as_of"] = None, None, None
            if kind != "state":
                claim["evidence_ids"] = []  # the evidence did not support it; nothing is cited
            claim["note"] = f"withheld by the citation check: {item['problem']}"
            actions.append({"organisation_number": item.get("organisation_number"), "field": claim.get("field"),
                            "kind": kind, "problem": item["problem"], "new_state": claim["availability"]})
    for claim in claims:
        if claim.get("availability") not in CONTRACT_STATES:  # only the six contract states ever appear
            actions.append({"organisation_number": envelope.get("organisation_number"), "field": claim.get("field"),
                            "kind": "state", "problem": f"non_contract_state_{claim.get('availability')}", "new_state": "failed"})
            claim["availability"], claim["value"] = "failed", None
    return actions


def check_envelopes(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    """Run-level summary for the report; `passed` is False on any problem."""
    totals = {"citation": [], "state": [], "site_ownership": []}
    published = 0
    for envelope in envelopes:
        published += sum(1 for claim in envelope.get("claims") or [] if claim.get("availability") == "available")
        for kind, items in envelope_problems(envelope).items():
            totals[kind].extend(items)
    published_missing = [item for item in totals["citation"]
                         if item["problem"] in {"missing_hash", "missing_span"} and item.get("published")]
    bad_states = sum(1 for envelope in envelopes for claim in envelope.get("claims") or []
                     if claim.get("availability") not in CONTRACT_STATES)
    summary = {
        "published_claims": published,
        "claims_outside_the_six_contract_states": bad_states,
        "published_claims_missing_hash": len({(i["organisation_number"], i["field"], i.get("evidence_id")) for i in published_missing if i["problem"] == "missing_hash"}),
        "published_claims_missing_span": len({(i["organisation_number"], i["field"], i.get("evidence_id")) for i in published_missing if i["problem"] == "missing_span"}),
        "citation_problems": len(totals["citation"]),
        "state_problems": len(totals["state"]),
        "site_ownership_problems": len(totals["site_ownership"]),
        "examples": {kind: items[:10] for kind, items in totals.items() if items},
    }
    summary["passed"] = not any(totals.values()) and not bad_states
    return summary
