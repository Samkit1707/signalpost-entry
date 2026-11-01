"""Contract-format claims and evidence (OUTPUT_CONTRACT.md), derived from the module evidence records.

Added alongside the starter envelope fields; nothing existing is removed. Missing values stay None with a
non-available state, never 0.

v4: every claim has its own evidence entry whose `claim_span` contains that claim's value exactly (registry/API
JSON and bulk CSV: field path and value; web pages: quoted text or attribute) and whose `content_sha256` is the hash
of the response the span was read from. A value that cannot be cited that way is withheld (`uncited_withheld`;
the aggregate claim stays as `ambiguous` with a `note`), never published. Claims without a value cite what was checked (`span_kind: "absence"`) when a response exists.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .citations import HASH, csv_span, json_span
from .confidence import LEVEL_SCORE, discovery_method, website_confidence
from .facts import fact_claims, live_as_bulk_row, row_citer
from .identity import _fold, full_legal_name_words, org_number_present
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
# Modules whose findings come only from the identity-verified company website.
SITE_MODULES = {"company_activity", "company_jobs", "dated_news"}
BULK_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"

Built = tuple  # (field, value, span[, source override {source_url, content_sha256, retrieved_at, span_kind}])


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


def _registry_claims(record: dict[str, Any]) -> list[Built]:
    row = _row(record)
    cite = row_citer(row)
    industry = None
    if _clean(row.get("naeringskode1.kode")):
        industry = {"code": row["naeringskode1.kode"], "description": _clean(row.get("naeringskode1.beskrivelse"))}
    address = ("adresse", "postnummer", "poststed")
    return [
        ("legal_name", _clean(row.get("navn")), cite("navn")),
        ("legal_form", _clean(row.get("organisasjonsform.kode")), cite("organisasjonsform.kode", "organisasjonsform.beskrivelse")),
        ("industry", industry, cite("naeringskode1.kode", "naeringskode1.beskrivelse")),
        ("business_address", _address(row, "forretningsadresse"), cite(*[f"forretningsadresse.{key}" for key in address])),
        ("postal_address", _address(row, "postadresse"), cite(*[f"postadresse.{key}" for key in address])),
    ]


def _live_address(value: Any) -> str | None:
    value = value if isinstance(value, dict) else {}
    street = ", ".join(part.strip() for part in value.get("adresse") or [] if part and part.strip())
    place = " ".join(part for part in (_clean(value.get("postnummer")), _clean(value.get("poststed"))) if part)
    return ", ".join(part for part in (street, place) if part) or None


def _live_address_span(value: dict[str, Any], key: str) -> str:
    address = value.get(key) if isinstance(value.get(key), dict) else {}
    pairs = [(f"$.{key}.adresse[{index}]", line) for index, line in enumerate(address.get("adresse") or [])]
    pairs += [(f"$.{key}.{part}", address.get(part)) for part in ("postnummer", "poststed")]
    return json_span(pairs) if address else json_span([(f"$.{key}", None)])


def _registry_claims_from_live(record: dict[str, Any]) -> list[Built]:
    """The same registry fields, from the live Enhetsregisteret entity when the bulk row is missing."""
    value = _row(record)
    raw_industry = value.get("industry") if isinstance(value.get("industry"), dict) else {}
    industry = None
    if _clean(raw_industry.get("kode")):
        industry = {"code": raw_industry["kode"], "description": _clean(raw_industry.get("beskrivelse"))}
    business, postal = _live_address(value.get("business_address")), _live_address(value.get("postal_address"))
    renamed = {"forretningsadresse": value.get("business_address"), "postadresse": value.get("postal_address")}
    return [
        ("legal_name", _clean(value.get("name")), json_span([("$.organisasjonsnummer", value.get("organisation_number")), ("$.navn", value.get("name"))])),
        ("legal_form", _clean(value.get("legal_form")), json_span([("$.organisasjonsform.kode", value.get("legal_form"))])),
        ("industry", industry, json_span([("$.naeringskode1.kode", raw_industry.get("kode")), ("$.naeringskode1.beskrivelse", raw_industry.get("beskrivelse"))])),
        ("business_address", business, _live_address_span(renamed, "forretningsadresse")),
        ("postal_address", postal, _live_address_span(renamed, "postadresse")),
    ]


def _registry_live_claims(record: dict[str, Any]) -> list[Built]:
    value = _row(record)
    if not value:
        return [("registry_status", None, "")]
    if value.get("deleted"):
        # A deleted entity is never presented as an active company.
        status = {"active": False, "deleted": True, "deleted_date": value.get("deleted_date")}
        return [("registry_status", status, json_span([("$.slettedato", value.get("deleted_date"))]))]
    status = {key: value.get(key) for key in ("bankrupt", "liquidating") if value.get(key) is not None}
    status["deleted"] = False
    status["active"] = not (value.get("bankrupt") or value.get("liquidating"))
    return [("registry_status", status, json_span([("$.konkurs", value.get("bankrupt")), ("$.underAvvikling", value.get("liquidating"))]))]


def _financial_claims(record: dict[str, Any]) -> list[Built]:
    latest = newest_filed_record(_row(record).get("records") or [])
    if not latest:
        return [("latest_annual_accounts", None, "")]
    spans = [latest.get("period_span"), *(latest.get("figure_spans") or {}).values()]
    value = {key: item for key, item in latest.items() if key not in {"period_span", "figure_spans", "json_index"}}
    return [("latest_annual_accounts", value, "; ".join(span for span in spans if span) if latest.get("period_span") else "")]


def _roles_claims(record: dict[str, Any]) -> list[Built]:
    active = [item for item in _row(record).get("roles") or [] if not item.get("inactive")]
    roles = [{"name": item.get("name"), "organisation_number": item.get("organisation_number"), "role": item.get("role"),
              "role_code": item.get("role_code")} for item in active]
    spans = [item.get("claim_span") for item in active]
    return [("roles", roles or None, "; ".join(spans) if spans and all(spans) else "")]


def _group_claims(record: dict[str, Any]) -> list[Built]:
    """Every other entity in the corporate group tree (rooted at the top parent), with its relation."""
    related = group_related(record)
    return [("group_structure", [{key: item[key] for key in item if key != "claim_span"} for item in related] or None,
             "; ".join(item["claim_span"] for item in related))]


def group_related(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Group members with their JSON-path spans (for the fact-level group_relationship claims)."""
    value = _row(record)
    if not value:
        return []
    own = (re.search(r"(\d{9})/?$", str(record.get("source_url") or "")) or [None, None])[1]
    related: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], relation: str | None, path: str) -> None:
        if node.get("organisasjonsnummer") and node.get("organisasjonsnummer") != own:
            pairs = [(f"{path}.organisasjonsnummer", node.get("organisasjonsnummer")), (f"{path}.navn", node.get("navn"))]
            if path != "$":
                pairs.append((f"{path}.knytningsform.beskrivelse", (node.get("knytningsform") or {}).get("beskrivelse")))
            related.append({"organisation_number": node.get("organisasjonsnummer"), "name": node.get("navn"),
                            "relation": relation, "share": node.get("grunnlag"), "claim_span": json_span(pairs)})
        for index, child in enumerate(node.get("children") or []):
            walk(child, (child.get("knytningsform") or {}).get("beskrivelse"), f"{path}.children[{index}]")

    walk(value, "group top parent", "$")
    return related


def _locations_claims(record: dict[str, Any]) -> list[Built]:
    locations, spans = [], []
    for item in _row(record).get("locations") or []:
        address = item.get("address") or {}
        street = ", ".join(address.get("adresse") or [])
        place = " ".join(part for part in (address.get("postnummer"), address.get("poststed")) if part)
        locations.append({"organisation_number": item.get("organisation_number"), "name": item.get("name"),
                          "address": ", ".join(part for part in (street, place) if part) or None})
        spans.append(item.get("claim_span"))
    return [("registered_locations", locations or None, "; ".join(spans) if spans and all(spans) else "")]


def _workforce_claims(record: dict[str, Any], records: dict[str, Any]) -> list[Built]:
    """Employee count, cited in the bulk row it came from, or in the live entity when the bulk row lacks it."""
    employees = _row(record).get("registered_employees")
    bulk = records.get("registry") or {}
    raw = _row(bulk)
    org = str(raw.get("organisasjonsnummer") or bulk.get("source_row_key") or "")
    if employees is None:
        if not raw:
            return [("registered_employees", None, "")]
        return [("registered_employees", None, csv_span(org, [("antallAnsatte", raw.get("antallAnsatte"))]),
                 {"source_url": bulk.get("source_url"), "content_sha256": bulk.get("content_sha256"), "retrieved_at": bulk.get("retrieved_at")})]
    if raw and _clean(raw.get("antallAnsatte")) is not None and str(raw.get("antallAnsatte")).strip() == str(employees):
        columns = [("antallAnsatte", raw.get("antallAnsatte"))]
        if _clean(raw.get("registreringsdatoAntallAnsatteEnhetsregisteret")):
            columns.append(("registreringsdatoAntallAnsatteEnhetsregisteret", raw.get("registreringsdatoAntallAnsatteEnhetsregisteret")))
        return [("registered_employees", employees, csv_span(org, columns),
                 {"source_url": bulk.get("source_url"), "content_sha256": bulk.get("content_sha256"), "retrieved_at": bulk.get("retrieved_at")})]
    live = records.get("registry_live") or {}
    if live.get("status") == "available" and _row(live).get("employees") == employees:
        return [("registered_employees", employees, json_span([("$.antallAnsatte", employees)]),
                 {"source_url": live.get("source_url"), "content_sha256": live.get("content_sha256"), "retrieved_at": live.get("retrieved_at")})]
    return [("registered_employees", employees, "")]


def _accounting_claims(record: dict[str, Any]) -> list[Built]:
    value = _row(record)
    return [("accounting_obligation", _clean(value.get("classification")), value.get("claim_span") or "")]


def _wikidata_claims(record: dict[str, Any]) -> list[Built]:
    value = _row(record)
    item = value.get("wikidata_item")
    claim = {"item": item, "wikipedia": value.get("wikipedia") or []} if item else None
    return [("wikidata_item", claim, value.get("claim_span") or "")]


def _website_proof(profile: dict[str, Any], value: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """(quoted page text that proved identity, URL of that page, that page's hash): org number first, then the name."""
    org = str(profile.get("organisation_number") or "")
    homepage = (value.get("final_url"), value.get("content_sha256"))
    parts = [(value.get(key), *homepage) for key in ("identity_text_excerpt", "title", "description", "main_text_excerpt")]
    parts += [(page.get(key), page.get("url"), page.get("content_sha256")) for page in value.get("pages", [])
              for key in ("identity_text_excerpt", "main_text_excerpt", "title")]
    for text, url, sha in parts:
        text = " ".join(str(text or "").split())
        if text and org_number_present(text, org):
            match = re.search(r"(?<!\d)" + r"[\s. -]?".join([org[:3], org[3:6], org[6:]]) + r"(?!\d)", text)
            if match:
                return text[max(0, match.start() - 80):match.end() + 40].strip(), url, sha
    words = full_legal_name_words(profile.get("name"))
    for text, url, sha in parts:
        text = " ".join(str(text or "").split())
        if not text or not words:
            continue
        # Match word by word with the identity gate's folding (æ -> ae, ø -> o, å -> a) and quote the original text.
        tokens = [(match.start(), match.end(), _fold(match.group(0))) for match in re.finditer(r"[^\W_]+", text)]
        tokens = [(start, end, folded) for start, end, folded in tokens if folded]
        for index in range(len(tokens) - len(words) + 1):
            if [folded for _, _, folded in tokens[index:index + len(words)]] == words:
                start, end = tokens[index][0], tokens[index + len(words) - 1][1]
                return text[max(0, start - 60):end + 60].strip(), url, sha
    return "", None, None


def _website_claims(record: dict[str, Any], profile: dict[str, Any]) -> list[Built]:
    value = _row(record)
    identity = value.get("identity_assessment") or {}
    if not identity.get("publishable"):
        return [("official_website", None, "")]
    span, url, sha = _website_proof(profile, value)
    return [("official_website", value.get("final_url"), span, {"source_url": url, "content_sha256": sha, "span_kind": "quote"})]


def _profiles_claims(record: dict[str, Any]) -> tuple[list[Built], list[dict[str, Any]]]:
    profiles = _row(record).get("profiles") or []
    claim = [{"platform": item.get("platform"), "url": item.get("url")} for item in profiles]
    return [("external_profiles", claim or None, "")], [source for item in profiles for source in item.get("sources") or []]


def _activity_claims(record: dict[str, Any]) -> tuple[list[Built], list[dict[str, Any]]]:
    items = _row(record).get("items") or []
    claim = [{"url": item.get("url"), "title": item.get("title"), "page_type": item.get("page_type"),
              "latest_declared_date": item.get("latest_declared_date")} for item in items]
    sources = [{"url": item.get("url"), "content_sha256": item.get("content_sha256"), "claim_span": item.get("claim_span")} for item in items]
    return [("company_activity", claim or None, "")], sources


def _jobs_claims(record: dict[str, Any]) -> tuple[list[Built], list[dict[str, Any]]]:
    value = _row(record)
    pages = value.get("careers_pages") or []
    postings = value.get("job_postings") or []
    # No posting count: a careers page without structured postings does not prove zero vacancies.
    claim = {"careers_pages": [{"url": item.get("url"), "title": item.get("title")} for item in pages],
             "job_postings": [{key: item for key, item in job.items() if key != "claim_span"} for job in postings]} if pages or postings else None
    sources = [{"url": item.get("url"), "content_sha256": item.get("content_sha256"), "claim_span": item.get("claim_span")} for item in pages]
    sources += [{"url": job.get("source_url"), "content_sha256": job.get("content_sha256"), "claim_span": job.get("claim_span")} for job in postings]
    return [("company_jobs", claim, "")], sources


SIMPLE: dict[str, Callable[[dict[str, Any]], list[Built]]] = {
    "registry": _registry_claims,
    "registry_live": _registry_live_claims,
    "financials": _financial_claims,
    "roles": _roles_claims,
    "group": _group_claims,
    "locations": _locations_claims,
    "accounting_obligation": _accounting_claims,
    "wikidata_footprint": _wikidata_claims,
}
MULTI_SOURCE = {"external_profiles": _profiles_claims, "company_activity": _activity_claims, "company_jobs": _jobs_claims}
FIELD_PATH_MODULES = {"registry", "registry_live", "financials", "roles", "group", "locations", "workforce",
                      "accounting_obligation", "wikidata_footprint"}


def _confidence(module: str, record: dict[str, Any], profile: dict[str, Any], state: str) -> float | None:
    if state != "available":
        return None
    if module == "website":
        return float(((_row(record).get("identity_assessment") or {}).get("score")) or 0) or None
    if module in MULTI_SOURCE:
        website = (profile.get("evidence", {}).get("website") or {}).get("value") or {}
        score = (website.get("identity_assessment") or {}).get("score")
        return float(score) if score else 0.99  # Wikidata-only profiles rest on the exact P2333 match
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


def _absence_span(module: str, record: dict[str, Any], records: dict[str, Any], state: str) -> str | None:
    """What a check without a value looked at, in words; only used when the response it describes has a hash."""
    note = " ".join(str(record.get("note") or "").split())
    if module == "dated_news" and record.get("checked"):
        checked = (record.get("checked") or {}).get("sources_checked") or []
        listed = ", ".join(f"{item.get('url')} ({item.get('status')})" for item in checked[:4])
        return f"{note or 'no dated news item found'}; checked: {listed or 'homepage and crawled pages'}"
    if module == "website":
        identity = (_row(record).get("identity_assessment") or {})
        if identity:
            return f"site fetched, exact identity not proven: {'; '.join((identity.get('reasons') or [])[-2:])}"
    return note or (f"module state {state}" if record else None)


def contract_fields(profile: dict[str, Any], module_states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Contract claims[] and evidence[] for one profile: one evidence entry per claim (or per source of a claim)."""
    claims: list[dict[str, Any]] = []
    evidence_list: list[dict[str, Any]] = []
    withheld: list[dict[str, Any]] = []
    records = profile.get("evidence", {})
    org = str(profile.get("organisation_number") or "")

    def add_evidence(module: str, entry: dict[str, Any]) -> str:
        evidence_id = f"ev-{module}-{sum(1 for item in evidence_list if item['module'] == module) + 1}"
        evidence_list.append({"id": evidence_id, "module": module, **entry})
        return evidence_id

    def entry_for(record: dict[str, Any], module: str, span: str | None, kind: str, override: dict[str, Any] | None = None) -> dict[str, Any]:
        entry = {"source_url": record.get("source_url"), "source_class": _source_class(record),
                 "retrieved_at": record.get("retrieved_at"), "content_sha256": record.get("content_sha256"),
                 "claim_span": span or None, "span_kind": kind}
        entry.update({key: item for key, item in (override or {}).items()
                      if item and (key != "source_url" or str(item).startswith(("http://", "https://")))})
        return entry

    def cited(entry: dict[str, Any]) -> bool:
        return bool(entry.get("claim_span")) and bool(HASH.match(str(entry.get("content_sha256") or "")))

    def absence_ids(module: str, record: dict[str, Any], state: str, span: str | None) -> list[str]:
        if module in SITE_MODULES | {"external_profiles"}:
            website_claim = next((claim for claim in claims if claim["field"] == "official_website"), None)
            site = _row(records.get("website") or {})
            if not (site.get("identity_assessment") or {}).get("publishable"):
                # nothing to read without a verified site: the website check is the evidence
                return list((website_claim or {}).get("evidence_ids") or [])
            if not record.get("content_sha256"):
                # the verified site was crawled and had none: cite the crawled homepage and the pages read
                pages = ", ".join(str(page.get("url")) for page in site.get("pages") or [] if page.get("url")) or site.get("final_url")
                entry = entry_for(records["website"], module, f"{record.get('note') or 'none found'}; pages read: {pages}", "absence",
                                  {"source_url": site.get("final_url"), "content_sha256": site.get("content_sha256")})
                return [add_evidence(module, entry)] if cited(entry) else []
        if module == "website" and not record.get("content_sha256") and _row(records.get("registry") or {}):
            raw = _row(records["registry"])
            entry = entry_for(records["registry"], module, csv_span(org, [("hjemmeside", raw.get("hjemmeside"))]), "absence")
            return [add_evidence(module, entry)]
        entry = entry_for(record, module, span or _absence_span(module, record, records, state), "absence")
        return [add_evidence(module, entry)] if cited(entry) else []

    def publish(module: str, record: dict[str, Any], state: str, built: list[Built], kind: str,
                sources: list[dict[str, Any]] | None = None) -> None:
        for field, value, span, *override in built:
            value = _clean(value)
            claim_state = state if (value is not None or state != "available") else "not_available"
            reason = None
            if claim_state == "available":
                if sources is not None:
                    entries = [entry_for(record, module, source.get("claim_span"), "field_path" if "wikidata" in str(source.get("source_url") or "") else "quote",
                                         {"source_url": source.get("source_url") or source.get("url"), "content_sha256": source.get("content_sha256"),
                                          "retrieved_at": source.get("retrieved_at"),
                                          "source_class": "open_knowledge_base" if "wikidata" in str(source.get("source_url") or "") else "company_owned"})
                               for source in sources]
                    # an item without an exact citation is dropped from the aggregate's evidence and reported
                    if not entries or not all(cited(entry) for entry in entries):
                        reason = "a source without exact span or hash"
                    else:
                        ids = [add_evidence(module, entry) for entry in entries]
                else:
                    entry = entry_for(record, module, span, kind, override[0] if override else None)
                    if not cited(entry):
                        reason = "no exact span" if not span else "no response hash"
                    else:
                        ids = [add_evidence(module, entry)]
                if reason:
                    # A value that cannot be cited exactly is not published: the field stays, ambiguous, with no value.
                    withheld.append({"field": field, "module": module, "reason": reason})
                    claims.append({"field": field, "value": None, "availability": "ambiguous", "confidence": None, "as_of": None,
                                   "evidence_ids": [], "module": module, "note": f"withheld: value found but {reason}"})
                    continue
            elif override and span:
                entry = entry_for(record, module, span, "absence", override[0])
                ids = [add_evidence(module, entry)] if cited(entry) else []
            else:
                ids = absence_ids(module, record, claim_state, span if span and record.get("status") == "available" else None)
            claims.append({
                "field": field,
                "value": value if claim_state == "available" else None,
                "availability": claim_state,
                "confidence": _confidence(module, record, profile, claim_state),
                "as_of": _as_of(field, value, record) if claim_state == "available" else None,
                "evidence_ids": ids,
                "module": module,
            })

    ordered = sorted(module_states, key=lambda module: module in SITE_MODULES | {"external_profiles"})  # website claim first
    for module in ordered:
        record = records.get(module) or {}
        state = availability(module_states[module]["state"])
        kind = "field_path" if module in FIELD_PATH_MODULES else "quote"
        if module == "dated_news":
            website_score = ((_row(records.get("website") or {}).get("identity_assessment") or {}).get("score"))
            _dated_news_claims(record, state, float(website_score) if website_score else None, claims, add_evidence,
                               entry_for, cited, withheld, absence_ids)
            continue
        if module in MULTI_SOURCE:
            built, sources = MULTI_SOURCE[module](record if state == "available" else {})
            publish(module, record, state, built, kind, sources if state == "available" else None)
        elif module == "website":
            identity = (_row(record).get("identity_assessment") or {})
            if state == "available" and not identity.get("publishable"):
                state = "ambiguous"  # a site was found, but exact identity is review/uncertain
            built = _website_claims(record, profile) if state == "available" else [("official_website", None, "")]
            publish(module, record, state, built, kind)
        elif module == "workforce":
            publish(module, record, state, _workforce_claims(record if state == "available" else {}, records), kind)
        elif module == "registry" and state != "available" and _live_fallback(records):
            # Bulk snapshot missing or without this row: the live registry entity carries the same fields.
            publish(module, records["registry_live"], "available", _registry_claims_from_live(records["registry_live"]), kind)
        else:
            builder = SIMPLE.get(module)
            # Non-available modules keep the same field names, with no value; a bulk row cites its empty cells.
            if builder and state != "available" and module == "registry" and _row(record):
                built = [(field, None, span) for field, _value, span in builder(record)]
            else:
                built = builder(record if state == "available" else {}) if builder else [(module, None, "")]
            publish(module, record, state, built, kind)
    # Group sites are labelled as a relationship, never as the company's own website.
    website_record = records.get("website") or {}
    group_sites = []
    group_site = (_row(website_record).get("identity_assessment") or {}).get("group_site")
    if group_site and website_record.get("status") == "available":
        group_sites.append((_row(website_record).get("final_url"), group_site, website_record.get("retrieved_at")))
    for item in (profile.get("website_discovery") or {}).get("candidates") or []:
        if item.get("group_site") and item.get("final_url"):
            group_sites.append((item["final_url"], item["group_site"], item.get("retrieved_at")))
    for url, label, retrieved in {url: (url, label, retrieved) for url, label, retrieved in group_sites}.values():
        entry = {"source_url": label.get("page_url") or url, "source_class": "company_owned", "retrieved_at": retrieved,
                 "content_sha256": label.get("content_sha256"), "claim_span": label.get("claim_span"), "span_kind": "quote"}
        if not cited(entry):
            withheld.append({"field": "group_website", "module": "website", "reason": "group number not quoted with a page hash"})
            continue
        claims.append({"field": "group_website", "value": {"url": url, "relationship": label["relationship"],
                                                           "group_organisation_numbers": label["organisation_numbers"]},
                       "availability": "available", "confidence": 0.9, "as_of": None, "evidence_ids": [add_evidence("website", entry)],
                       "module": "website", "fact_level": True})
    # Fact-level claims (one per role, sub-unit, filed figure, registry attribute, profile, posting, site contact).
    registry_record, registry_row = None, None
    if (module_states.get("registry") or {}).get("state") == "complete":
        registry_record = records.get("registry")
        registry_row = _row(registry_record)
    elif "registry" in module_states and _live_fallback(records):
        registry_record = records["registry_live"]
        registry_row = live_as_bulk_row(_row(registry_record))
    related = group_related(records["group"]) if (records.get("group") or {}).get("status") == "available" else None
    website_score = ((_row(records.get("website") or {}).get("identity_assessment") or {}).get("score"))
    claims.extend(fact_claims(profile, records, module_states, add_evidence, float(website_score) if website_score else None,
                              registry_row, registry_record, related, withheld))
    label_link_claims(profile, records, claims, withheld)
    claims.extend(social_profiles_claims(claims))
    return {"claims": claims, "evidence": evidence_list, "uncited_withheld": withheld}


# Claims whose value is a link or a fact read from the identity-gated site: each gets confidence_level + proof.
SITE_FACT_FIELDS = {"site_address", "site_phone", "site_email", "site_logo"}
LINK_FIELDS = {"official_website", "external_profile", "external_profiles", "dated_news", "company_activity", "careers_page",
               "job_posting", "company_jobs", "group_website"} | SITE_FACT_FIELDS


def label_link_claims(profile: dict[str, Any], records: dict[str, Any], claims: list[dict[str, Any]],
                      withheld: list[dict[str, Any]]) -> None:
    """v5: `confidence_level` (high/medium), numeric `confidence` to match, and `proof` on every published link claim.
    A link claim with no level is not published: it becomes not_available with a note. Candidates that failed a
    family's rule are listed on that family's claim as `not_published` (URL and reason; never a value)."""
    site_level, site_reason = website_confidence(profile)
    site_url = (_row(records.get("website") or {})).get("final_url")
    profiles = {item.get("url"): item for item in (_row(records.get("external_profiles") or {})).get("profiles") or []}
    news = {item.get("url"): item for item in (_row(records.get("dated_news") or {})).get("items") or []}
    activity = (_row(records.get("company_activity") or {})).get("items") or []
    jobs = _row(records.get("company_jobs") or {})
    careers = {item.get("url"): item for item in jobs.get("careers_pages") or []}
    postings = {(item.get("source_url"), item.get("title")): item for item in jobs.get("job_postings") or []}

    def rejected(module: str) -> list[dict[str, Any]]:
        record = records.get(module) or {}
        return list(_row(record).get("not_published") or (record.get("checked") or {}).get("not_published") or [])

    def worst(levels: list[str | None]) -> str | None:
        return None if not levels or None in levels else "medium" if "medium" in levels else "high"

    for claim in claims:
        field = claim.get("field")
        if field not in LINK_FIELDS:
            continue
        if field in {"external_profiles", "dated_news"} and claim.get("availability") != "available":
            missed = rejected(field)
            if missed:
                claim["not_published"] = missed[:10]
                claim.setdefault("note", f"{len(missed)} candidate(s) found but not published: " + "; ".join(
                    f"{item.get('url')}: {item.get('reason')}" for item in missed[:3]))
        if claim.get("availability") != "available":
            continue
        value = claim.get("value")
        level, proof = None, None
        if field == "official_website":
            level, proof = site_level, site_reason
            claim["discovery_method"] = discovery_method(profile)
        elif field == "external_profile":
            item = profiles.get((value or {}).get("url")) or {}
            level, proof = item.get("confidence"), item.get("proof")
        elif field == "external_profiles":
            items = [profiles.get(entry.get("url")) or {} for entry in value or []]
            level, proof = worst([item.get("confidence") for item in items]), "; ".join(f"{item.get('url')}: {item.get('proof')}" for item in items)
            for entry, item in zip(value or [], items):
                entry.update({"confidence": item.get("confidence"), "proof": item.get("proof")})
            if rejected("external_profiles"):
                claim["not_published"] = rejected("external_profiles")[:10]
        elif field == "dated_news":
            item = news.get((value or {}).get("url")) or {}
            level = item.get("confidence")
            proof = (f"{item.get('article_evidence')} on the verified site {site_url}; date from {item.get('date_from')}; "
                     f"names the company ({str(item.get('company_named') or '').replace('_', ' ')}): “{item.get('company_mention')}”") if level else None
        elif field == "company_activity":
            level = worst([item.get("confidence") for item in activity])
            proof = "; ".join(f"{item.get('url')}: “{item.get('company_mention')}”" for item in activity)
        elif field == "careers_page":
            item = careers.get((value or {}).get("url")) or {}
            level, proof = item.get("confidence"), item.get("proof")
        elif field == "job_posting":
            item = postings.get(((value or {}).get("source_url"), (value or {}).get("title"))) or {}
            level, proof = item.get("confidence"), item.get("proof")
        elif field == "company_jobs":
            levels = [item.get("confidence") for item in [*careers.values(), *postings.values()]]
            level, proof = worst(levels), f"careers pages and JSON-LD JobPosting on the verified site {site_url}"
        elif field == "group_website":
            level, proof = "medium", "group site: the page quotes this company's group organisation numbers; not this company's own site"
        elif field in SITE_FACT_FIELDS:
            level, proof = site_level, f"stated on the verified site {site_url}"
        if level is None:
            withheld.append({"field": field, "module": claim.get("module"), "reason": "no confidence level"})
            claim.update({"value": None, "availability": "not_available", "confidence": None, "as_of": None,
                          "note": "withheld: link found but its confidence could not be established"})
            continue
        claim.update({"confidence_level": level, "confidence": LEVEL_SCORE[level], "proof": proof})


SOCIAL_PLATFORMS = {"facebook", "linkedin", "instagram", "youtube", "x", "tiktok"}


def social_profiles_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The company's social profiles under a plainly named field, `social_profiles`: the same links, confidence, proof
    and evidence as the `external_profiles` claim (kept as it was), without non-social entries such as Wikipedia."""
    aggregate = next((claim for claim in claims if claim.get("field") == "external_profiles"), None)
    if aggregate is None:
        return []
    claim = {**aggregate, "field": "social_profiles"}
    claim.pop("fact_level", None)
    social = [dict(entry) for entry in aggregate.get("value") or [] if entry.get("platform") in SOCIAL_PLATFORMS]
    if aggregate.get("availability") == "available" and social:
        levels = [entry.get("confidence") for entry in social]
        level = "medium" if "medium" in levels else "high"
        claim.update({"value": social, "confidence_level": level, "confidence": LEVEL_SCORE[level],
                      "proof": "; ".join(f"{entry.get('url')}: {entry.get('proof')}" for entry in social)})
    elif aggregate.get("availability") == "available":
        claim.update({"value": None, "availability": "not_available", "confidence": None, "as_of": None,
                      "note": "no social profile (Facebook, LinkedIn, Instagram, YouTube, X, TikTok) passed the identity rule; "
                              "only non-social profiles were published"})
        for key in ("confidence_level", "proof"):
            claim.pop(key, None)
    return [claim]


def _dated_news_claims(record: dict[str, Any], state: str, confidence: float | None,
                       claims: list[dict[str, Any]], add_evidence: Callable[[str, dict[str, Any]], str],
                       entry_for: Callable[..., dict[str, Any]], cited: Callable[[dict[str, Any]], bool],
                       withheld: list[dict[str, Any]], absence_ids: Callable[..., list[str]]) -> None:
    """One `dated_news` claim per item: title, direct URL and publication date, each with its own evidence."""
    items = _row(record).get("items") or [] if state == "available" else []
    published = 0
    for item in items:
        entry = entry_for(record, "dated_news", item.get("claim_span"), "quote", {
            "source_url": item.get("source_url"), "source_class": "company_owned", "retrieved_at": item.get("retrieved_at"),
            "content_sha256": item.get("content_sha256")})
        entry.update({"item_url": item.get("url"), "published_at": item.get("published_at"), "source_kind": item.get("source_kind"),
                      "company_match": item.get("company_match")})
        if not cited(entry):
            withheld.append({"field": "dated_news", "module": "dated_news", "reason": "no exact span or hash"})
            continue
        claims.append({"field": "dated_news", "availability": "available", "confidence": confidence,
                       "value": {key: item.get(key) for key in ("title", "url", "published_at", "source_kind")},
                       "as_of": item.get("published_at"), "evidence_ids": [add_evidence("dated_news", entry)], "module": "dated_news"})
        published += 1
    if published:
        return
    empty_state = "not_available" if state == "available" else state
    claims.append({"field": "dated_news", "value": None, "availability": empty_state, "confidence": None, "as_of": None,
                   "evidence_ids": absence_ids("dated_news", record, empty_state, None), "module": "dated_news"})


def _live_fallback(records: dict[str, Any]) -> bool:
    live = records.get("registry_live") or {}
    return live.get("status") == "available" and bool(_row(live).get("name"))
