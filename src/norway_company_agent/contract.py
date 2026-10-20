"""Contract-format claims and evidence (OUTPUT_CONTRACT.md), derived from the module evidence records.

Added alongside the starter envelope fields; nothing existing is removed. Missing values stay None with a
non-available state, never 0.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .identity import full_legal_name_words, org_number_present
from .synthesis import newest_filed_record

# Module terminal state -> contract availability.
AVAILABILITY = {
    "complete": "available",
    "not_found": "not_available",
    "blocked_robots": "blocked",
    "blocked_policy": "blocked",
    "not_applicable": "not_applicable",
    "source_error": "failed",
    "budget_exhausted": "failed",
    "submission_error": "failed",
}

SOURCE_CLASS = {
    "official_registry_bulk": "official_registry",
    "official_registry_live": "official_registry",
    "official_annual_accounts": "official_registry",
    "official_roles": "official_registry",
    "official_group_structure": "official_registry",
    "official_subunits": "official_registry",
    "wikidata_org_number_match": "open_knowledge_base",
}


def availability(state: str) -> str:
    return AVAILABILITY.get(state, "failed")


def _source_class(record: dict[str, Any]) -> str:
    source = record.get("source_class") or record.get("source_type") or ""
    if source in SOURCE_CLASS:
        return SOURCE_CLASS[source]
    if "website" in source or "company" in source or "domain" in source:
        return "company_owned"
    if source.startswith("official"):
        return "official_registry"
    return source or "unknown"


def _row(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("value") or {}


def _clean(value: Any) -> Any:
    """Drop empty strings so a blank registry cell never reads as a value."""
    if isinstance(value, str):
        return value.strip() or None
    return value


def _address(row: dict[str, Any], prefix: str) -> str | None:
    street = _clean(row.get(f"{prefix}.adresse"))
    place = " ".join(part for part in (_clean(row.get(f"{prefix}.postnummer")), _clean(row.get(f"{prefix}.poststed"))) if part)
    text = ", ".join(part for part in (street, place) if part)
    return text or None


def _registry_claims(record: dict[str, Any]) -> list[tuple[str, Any, str]]:
    row = _row(record)
    org = _clean(row.get("organisasjonsnummer"))
    industry = None
    if _clean(row.get("naeringskode1.kode")):
        industry = {"code": row["naeringskode1.kode"], "description": _clean(row.get("naeringskode1.beskrivelse"))}
    return [
        ("legal_name", _clean(row.get("navn")), f"organisasjonsnummer={org}; navn={row.get('navn')}"),
        ("legal_form", _clean(row.get("organisasjonsform.kode")), f"organisasjonsform={row.get('organisasjonsform.kode')} {row.get('organisasjonsform.beskrivelse') or ''}".strip()),
        ("industry", industry, f"naeringskode1={row.get('naeringskode1.kode')} {row.get('naeringskode1.beskrivelse') or ''}".strip()),
        ("business_address", _address(row, "forretningsadresse"), f"forretningsadresse={_address(row, 'forretningsadresse')}"),
        ("postal_address", _address(row, "postadresse"), f"postadresse={_address(row, 'postadresse')}"),
    ]


def _live_address(value: Any) -> str | None:
    value = value if isinstance(value, dict) else {}
    street = ", ".join(part.strip() for part in value.get("adresse") or [] if part and part.strip())
    place = " ".join(part for part in (_clean(value.get("postnummer")), _clean(value.get("poststed"))) if part)
    return ", ".join(part for part in (street, place) if part) or None


def _registry_claims_from_live(record: dict[str, Any]) -> list[tuple[str, Any, str]]:
    """The same registry fields, from the live Enhetsregisteret entity when the bulk row is missing."""
    value = _row(record)
    raw_industry = value.get("industry") if isinstance(value.get("industry"), dict) else {}
    industry = None
    if _clean(raw_industry.get("kode")):
        industry = {"code": raw_industry["kode"], "description": _clean(raw_industry.get("beskrivelse"))}
    business, postal = _live_address(value.get("business_address")), _live_address(value.get("postal_address"))
    return [
        ("legal_name", _clean(value.get("name")), f"organisasjonsnummer={value.get('organisation_number')}; navn={value.get('name')}"),
        ("legal_form", _clean(value.get("legal_form")), f"organisasjonsform={value.get('legal_form')}"),
        ("industry", industry, f"naeringskode1={raw_industry.get('kode')} {raw_industry.get('beskrivelse') or ''}".strip()),
        ("business_address", business, f"forretningsadresse={business}"),
        ("postal_address", postal, f"postadresse={postal}"),
    ]


def _registry_live_claims(record: dict[str, Any]) -> list[tuple[str, Any, str]]:
    value = _row(record)
    if not value:
        return [("registry_status", None, "")]
    if value.get("deleted"):
        # A deleted entity is never presented as an active company.
        status = {"active": False, "deleted": True, "deleted_date": value.get("deleted_date")}
        return [("registry_status", status, f"respons_klasse=SlettetEnhet; slettedato={value.get('deleted_date')}")]
    status = {key: value.get(key) for key in ("bankrupt", "liquidating") if value.get(key) is not None}
    status["deleted"] = False
    status["active"] = not (value.get("bankrupt") or value.get("liquidating"))
    return [("registry_status", status, "konkurs={bankrupt}; underAvvikling={liquidating}".format(
        bankrupt=value.get("bankrupt"), liquidating=value.get("liquidating")))]


def _financial_claims(record: dict[str, Any]) -> list[tuple[str, Any, str]]:
    latest = newest_filed_record(_row(record).get("records") or [])
    if not latest:
        return [("latest_annual_accounts", None, "")]
    period = latest.get("period") or {}
    span = f"regnskap record {latest.get('record_id')} {latest.get('account_type')} {period.get('fraDato')}..{period.get('tilDato')}"
    return [("latest_annual_accounts", latest, span)]


def _roles_claims(record: dict[str, Any]) -> list[tuple[str, Any, str]]:
    roles = [{"name": item.get("name"), "organisation_number": item.get("organisation_number"), "role": item.get("role"),
              "role_code": item.get("role_code")} for item in _row(record).get("roles") or [] if not item.get("inactive")]
    span = "; ".join(f"{item['role']}: {item['name']}" for item in roles[:5])
    return [("roles", roles or None, span)]


def _group_claims(record: dict[str, Any]) -> list[tuple[str, Any, str]]:
    """Every other entity in the corporate group tree (rooted at the top parent), with its relation."""
    value = _row(record)
    own = (re.search(r"(\d{9})/?$", str(record.get("source_url") or "")) or [None, None])[1]
    related = []

    def walk(node: dict[str, Any], relation: str | None) -> None:
        if node.get("organisasjonsnummer") and node.get("organisasjonsnummer") != own:
            related.append({"organisation_number": node.get("organisasjonsnummer"), "name": node.get("navn"),
                            "relation": relation, "share": node.get("grunnlag")})
        for child in node.get("children") or []:
            walk(child, (child.get("knytningsform") or {}).get("beskrivelse"))

    if value:
        walk(value, "group top parent")
    span = "; ".join(f"{item['relation']}: {item['name']} ({item['organisation_number']})" for item in related[:5])
    return [("group_structure", related or None, span)]


def _locations_claims(record: dict[str, Any]) -> list[tuple[str, Any, str]]:
    locations = []
    for item in _row(record).get("locations") or []:
        address = item.get("address") or {}
        street = ", ".join(address.get("adresse") or [])
        place = " ".join(part for part in (address.get("postnummer"), address.get("poststed")) if part)
        locations.append({"organisation_number": item.get("organisation_number"), "name": item.get("name"),
                          "address": ", ".join(part for part in (street, place) if part) or None})
    span = "; ".join(f"{item['name']} {item['address'] or ''}".strip() for item in locations[:5])
    return [("registered_locations", locations or None, span)]


def _workforce_claims(record: dict[str, Any]) -> list[tuple[str, Any, str]]:
    value = _row(record)
    employees = value.get("registered_employees")
    return [("registered_employees", employees, f"antallAnsatte={employees}" if employees is not None else "")]


def _accounting_claims(record: dict[str, Any]) -> list[tuple[str, Any, str]]:
    value = _row(record)
    return [("accounting_obligation", _clean(value.get("classification")), value.get("reason") or "")]


def _wikidata_claims(record: dict[str, Any]) -> list[tuple[str, Any, str]]:
    value = _row(record)
    item = value.get("wikidata_item")
    claim = {"item": item, "wikipedia": value.get("wikipedia") or []} if item else None
    return [("wikidata_item", claim, value.get("identity_proof") or "")]


def _website_span(profile: dict[str, Any], value: dict[str, Any]) -> str:
    """The page text that proved identity: org number first, then the legal name."""
    org = str(profile.get("organisation_number") or "")
    parts = [value.get("identity_text_excerpt"), value.get("title"), value.get("description"), value.get("main_text_excerpt")]
    parts += [page.get("identity_text_excerpt") for page in value.get("pages", [])]
    for text in parts:
        if text and org_number_present(text, org):
            match = re.search(r"[\s. -]?".join([org[:3], org[3:6], org[6:]]), text)
            if match:
                return " ".join(text[max(0, match.start() - 80):match.end() + 40].split())
    words = full_legal_name_words(profile.get("name"))
    pattern = r"\W+".join(re.escape(word) for word in words)
    for text in parts:
        if not text or not words:
            continue
        folded = text.translate(str.maketrans("øØåÅæÆ", "oOaAaA")).casefold()
        match = re.search(pattern, folded)
        if match:
            return " ".join(text[max(0, match.start() - 60):match.end() + 60].split())
    return " ".join(str(value.get("title") or "").split())[:160]


def _website_claims(record: dict[str, Any], profile: dict[str, Any]) -> list[tuple[str, Any, str]]:
    value = _row(record)
    identity = value.get("identity_assessment") or {}
    url = value.get("final_url") if identity.get("publishable") else None
    return [("official_website", url, _website_span(profile, value) if value else "")]


def _items_evidence(module: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"source_url": item.get("source_url") or item.get("url"), "content_sha256": item.get("content_sha256"),
             "retrieved_at": item.get("retrieved_at"), "claim_span": item.get("claim_span")} for item in entries]


def _profiles_claims(record: dict[str, Any]) -> tuple[list[tuple[str, Any, str]], list[dict[str, Any]]]:
    profiles = _row(record).get("profiles") or []
    claim = [{"platform": item.get("platform"), "url": item.get("url")} for item in profiles]
    sources = [{**source, "claim_span": f"{item.get('platform')}: {item.get('url')} ({source.get('method') or 'link'})"}
               for item in profiles for source in item.get("sources") or []]
    return [("external_profiles", claim or None, "")], sources


def _activity_claims(record: dict[str, Any]) -> tuple[list[tuple[str, Any, str]], list[dict[str, Any]]]:
    items = _row(record).get("items") or []
    claim = [{"url": item.get("url"), "title": item.get("title"), "page_type": item.get("page_type"),
              "latest_declared_date": item.get("latest_declared_date")} for item in items]
    sources = [{"url": item.get("url"), "content_sha256": item.get("content_sha256"),
                "claim_span": " ".join(part for part in (item.get("title"), item.get("latest_declared_date")) if part)} for item in items]
    return [("company_activity", claim or None, "")], sources


def _jobs_claims(record: dict[str, Any]) -> tuple[list[tuple[str, Any, str]], list[dict[str, Any]]]:
    value = _row(record)
    pages = value.get("careers_pages") or []
    postings = value.get("job_postings") or []
    # No posting count: a careers page without structured postings does not prove zero vacancies.
    claim = {"careers_pages": [{"url": item.get("url"), "title": item.get("title")} for item in pages],
             "job_postings": postings} if pages or postings else None
    sources = [{"url": item.get("url"), "content_sha256": item.get("content_sha256"), "claim_span": item.get("title")} for item in pages]
    return [("company_jobs", claim, "")], sources


SIMPLE: dict[str, Callable[[dict[str, Any]], list[tuple[str, Any, str]]]] = {
    "registry": _registry_claims,
    "registry_live": _registry_live_claims,
    "financials": _financial_claims,
    "roles": _roles_claims,
    "group": _group_claims,
    "locations": _locations_claims,
    "workforce": _workforce_claims,
    "accounting_obligation": _accounting_claims,
    "wikidata_footprint": _wikidata_claims,
}
MULTI_SOURCE = {"external_profiles": _profiles_claims, "company_activity": _activity_claims, "company_jobs": _jobs_claims}


def _confidence(module: str, record: dict[str, Any], profile: dict[str, Any], state: str) -> float | None:
    if state != "available":
        return None
    if module == "website":
        return float(((_row(record).get("identity_assessment") or {}).get("score")) or 0) or None
    if module in MULTI_SOURCE:
        website = (profile.get("evidence", {}).get("website") or {}).get("value") or {}
        score = (website.get("identity_assessment") or {}).get("score")
        return float(score) if score else 0.99  # Wikidata-only profiles rest on the exact P2333 match
    if module == "wikidata_footprint":
        return 0.99
    return 0.99


def _as_of(field: str, value: Any, record: dict[str, Any]) -> str | None:
    """The date a fact refers to (fiscal-year end, registration date, publication date), when the source states one."""
    if field == "latest_annual_accounts" and isinstance(value, dict):
        return (value.get("period") or {}).get("tilDato")
    if field == "registered_employees":
        return _row(record).get("registered_at") or record.get("as_of")
    if field == "registry_status" and isinstance(value, dict) and value.get("deleted_date"):
        return value.get("deleted_date")
    return record.get("effective_at") or None


def _dated_news_claims(record: dict[str, Any], state: str, confidence: float | None,
                       add_evidence: Callable[[str, dict[str, Any]], str], base: dict[str, Any]) -> list[dict[str, Any]]:
    """One `dated_news` claim per item: title, direct URL and publication date, each with its own evidence."""
    items = _row(record).get("items") or [] if state == "available" else []
    claims = []
    for item in items:
        evidence_id = add_evidence("dated_news", {
            "source_url": item.get("source_url"), "source_class": "company_owned", "retrieved_at": item.get("retrieved_at"),
            "content_sha256": item.get("content_sha256"), "claim_span": f"{item.get('title')} — {item.get('published_at')}",
            "item_url": item.get("url"), "published_at": item.get("published_at"), "source_kind": item.get("source_kind"),
            "company_match": item.get("company_match"),
        })
        claims.append({"field": "dated_news", "availability": "available", "confidence": confidence,
                       "value": {key: item.get(key) for key in ("title", "url", "published_at", "source_kind")},
                       "as_of": item.get("published_at"), "evidence_ids": [evidence_id], "module": "dated_news"})
    if claims:
        return claims
    ids = [add_evidence("dated_news", {**base, "claim_span": None})] if record else []
    empty_state = "not_available" if state == "available" else state
    return [{"field": "dated_news", "value": None, "availability": empty_state, "confidence": None, "as_of": None,
             "evidence_ids": ids, "module": "dated_news"}]


def _live_fallback(records: dict[str, Any]) -> bool:
    live = records.get("registry_live") or {}
    return live.get("status") == "available" and bool(_row(live).get("name"))


def contract_fields(profile: dict[str, Any], module_states: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Contract claims[] and evidence[] for one profile, one evidence entry per source record."""
    claims: list[dict[str, Any]] = []
    evidence_list: list[dict[str, Any]] = []
    records = profile.get("evidence", {})

    def add_evidence(module: str, entry: dict[str, Any]) -> str:
        evidence_id = f"ev-{module}-{sum(1 for item in evidence_list if item['module'] == module) + 1}"
        evidence_list.append({"id": evidence_id, "module": module, **entry})
        return evidence_id

    for module, state_info in module_states.items():
        record = records.get(module) or {}
        state = availability(state_info["state"])
        base = {"source_url": record.get("source_url"), "source_class": _source_class(record),
                "retrieved_at": record.get("retrieved_at"), "content_sha256": record.get("content_sha256")}
        if module == "dated_news":
            website_score = ((_row(records.get("website") or {}).get("identity_assessment") or {}).get("score"))
            claims.extend(_dated_news_claims(record, state, float(website_score) if website_score else None, add_evidence, base))
            continue
        if module in MULTI_SOURCE:
            module_claims, sources = MULTI_SOURCE[module](record if state == "available" else {})
            ids = [add_evidence(module, {**base, "source_url": source.get("source_url") or source.get("url"),
                                         "retrieved_at": source.get("retrieved_at") or base["retrieved_at"],
                                         "content_sha256": source.get("content_sha256"),
                                         "source_class": "open_knowledge_base" if "wikidata" in str(source.get("source_url") or "") else "company_owned",
                                         "claim_span": source.get("claim_span")})
                   for source in sources] if state == "available" else []
            if not ids and record:
                ids = [add_evidence(module, {**base, "claim_span": None})]
        elif module == "website":
            module_claims = _website_claims(record, profile)
            ids = [add_evidence(module, {**base, "claim_span": module_claims[0][2] or None})] if record else []
            identity = (_row(record).get("identity_assessment") or {})
            if state == "available" and not identity.get("publishable"):
                state = "ambiguous"  # a site was found, but exact identity is review/uncertain
        elif module == "registry" and state != "available" and _live_fallback(records):
            # Bulk snapshot missing or without this row: the live registry entity carries the same fields.
            live = records["registry_live"]
            state = "available"
            module_claims = _registry_claims_from_live(live)
            ids = [add_evidence(module, {"source_url": live.get("source_url"), "source_class": "official_registry",
                                         "retrieved_at": live.get("retrieved_at"), "content_sha256": live.get("content_sha256"),
                                         "claim_span": module_claims[0][2]})]
        else:
            builder = SIMPLE.get(module)
            # Non-available modules keep the same field names, with no value.
            module_claims = builder(record if state == "available" else {}) if builder else [(module, None, "")]
            ids = [add_evidence(module, {**base, "claim_span": module_claims[0][2] or None})] if record else []
        for field, value, _span in module_claims:
            value = _clean(value)
            claim_state = state if (value is not None or state != "available") else "not_available"
            claims.append({
                "field": field,
                "value": value if claim_state == "available" else None,
                "availability": claim_state,
                "confidence": _confidence(module, record, profile, claim_state),
                "as_of": _as_of(field, value, record) if claim_state == "available" else None,
                "evidence_ids": ids,
                "module": module,
            })
    return {"claims": claims, "evidence": evidence_list}
