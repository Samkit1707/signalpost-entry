"""Fact-level claims: one claim per individual fact, so fact recall is measurable fact by fact.

Recall is scored as company coverage plus individual facts, so list-valued module records are also emitted as
one claim per item (each role, each registered sub-unit, each filed year's figures, each profile, each job posting)
and the registry snapshot's attributes as separate claims. The aggregate claims of v1/v2 stay unchanged next to
these. Every value comes from the same module record (official registry, accounts register, identity-gated site);
null figures are omitted, never turned into 0.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .citations import HASH, csv_span, json_span
from .synthesis import fiscal_year

# Account figures emitted per filed year (field name, record key).
ACCOUNT_FIGURES = (("annual_revenue", "revenue"), ("operating_result", "operating_result"),
                   ("annual_result", "annual_result"), ("equity", "equity"), ("total_assets", "assets"))
REGISTERS = (("foretaksregisteret", "registrertIForetaksregisteret", "registreringsdatoForetaksregisteret"),
             ("merverdiavgiftsregisteret", "registrertIMvaRegisteret", "registreringsdatoMerverdiavgiftsregisteret"),
             ("frivillighetsregisteret", "registrertIFrivillighetsregisteret", "registreringsdatoFrivillighetsregisteret"),
             ("stiftelsesregisteret", "registrertIStiftelsesregisteret", None),
             ("partiregisteret", "registrertIPartiregisteret", "registreringsdatoPartiregisteret"))
# Registry e-mail and phone are company contact data; for sole proprietorships they are a private person's, so skipped.
PERSONAL_FORMS = {"ENK"}

Fact = tuple  # (field, value, claim span, as_of[, source override {source_url, retrieved_at, content_sha256, source_class}])


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        value = " ".join(value.split())
        return value or None
    return value


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    return True if text == "true" else False if text == "false" else None


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(" ", "")) if _clean(str(value)) is not None and value is not None else None
    except ValueError:
        return None


def live_as_bulk_row(value: dict[str, Any]) -> dict[str, Any]:
    """The live Enhetsregisteret entity (registry_live `extras`) flattened to the bulk CSV's column names."""
    extras = value.get("extras") or {}
    row: dict[str, Any] = {"organisasjonsnummer": value.get("organisation_number"), "organisasjonsform.kode": value.get("legal_form")}
    # bulk column -> (JSON path in the live /enheter response, value as returned) for exact citation spans
    paths: dict[str, tuple[str, Any]] = {"organisasjonsform.kode": ("$.organisasjonsform.kode", value.get("legal_form"))}
    for key, column in (("stiftelsesdato", "stiftelsesdato"), ("registreringsdatoEnhetsregisteret", "registreringsdatoenhetsregisteret"),
                        ("registrertIMvaregisteret", "registrertIMvaRegisteret"), ("registrertIForetaksregisteret", "registrertIForetaksregisteret"),
                        ("registrertIFrivillighetsregisteret", "registrertIFrivillighetsregisteret"),
                        ("registrertIStiftelsesregisteret", "registrertIStiftelsesregisteret"), ("overordnetEnhet", "overordnetEnhet"),
                        ("epostadresse", "epostadresse"), ("telefon", "telefon"), ("mobil", "mobil"),
                        ("fravalgRevisjonDato", "fravalgRevisjonDato")):
        row[column] = extras.get(key)
        paths[column] = (f"$.{key}", extras.get(key))
    for key in ("naeringskode2", "naeringskode3", "institusjonellSektorkode"):
        item = extras.get(key) or {}
        row[f"{key}.kode"], row[f"{key}.beskrivelse"] = item.get("kode"), item.get("beskrivelse")
        paths[f"{key}.kode"], paths[f"{key}.beskrivelse"] = (f"$.{key}.kode", item.get("kode")), (f"$.{key}.beskrivelse", item.get("beskrivelse"))
    capital = extras.get("kapital") or {}
    for key in ("belop", "antallAksjer", "type", "valuta", "innfortDato"):
        row[f"kapital.{key}"] = capital.get(key)
        paths[f"kapital.{key}"] = (f"$.kapital.{key}", capital.get(key))
    for key in ("vedtektsfestetFormaal", "aktivitet"):
        raw = extras.get(key)
        row[key] = " ".join(raw) if isinstance(raw, list) else raw
        paths[key] = (f"$.{key}", raw)
    industry = value.get("industry") if isinstance(value.get("industry"), dict) else {}
    row["naeringskode1.kode"], row["naeringskode1.beskrivelse"] = industry.get("kode"), industry.get("beskrivelse")
    paths["naeringskode1.kode"], paths["naeringskode1.beskrivelse"] = ("$.naeringskode1.kode", industry.get("kode")), ("$.naeringskode1.beskrivelse", industry.get("beskrivelse"))
    row["_json_paths"] = paths
    return row


def row_citer(row: dict[str, Any]) -> Callable[..., str]:
    """span(columns...) for one registry row: bulk-CSV cells, or the live JSON paths the row was flattened from."""
    org = str(row.get("organisasjonsnummer") or "")
    paths = row.get("_json_paths")

    def cite(*columns: str) -> str:
        if paths is not None:
            return json_span(paths.get(column, (f"$.{column}", row.get(column))) for column in columns)
        return csv_span(org, ((column, row.get(column)) for column in columns))
    return cite


def registry_facts(row: dict[str, Any]) -> list[Fact]:
    facts: list[Fact] = []
    cite = row_citer(row)
    for rank in (1, 2, 3):
        code = _clean(row.get(f"naeringskode{rank}.kode"))
        if code:
            description = _clean(row.get(f"naeringskode{rank}.beskrivelse"))
            facts.append(("nace_code", {"code": code, "description": description, "rank": rank},
                          cite(f"naeringskode{rank}.kode", f"naeringskode{rank}.beskrivelse"), None))
    for field, column in (("founding_date", "stiftelsesdato"), ("registration_date", "registreringsdatoenhetsregisteret")):
        date = _clean(row.get(column))
        if date:
            facts.append((field, date, cite(column), date))
    amount = _number(row.get("kapital.belop"))
    if amount is not None:
        capital = {"amount": amount, "currency": _clean(row.get("kapital.valuta")) or "NOK", "type": _clean(row.get("kapital.type")),
                   "shares": int(_number(row.get("kapital.antallAksjer"))) if _number(row.get("kapital.antallAksjer")) is not None else None,
                   "registered_date": _clean(row.get("kapital.innfortDato"))}
        facts.append(("share_capital", capital, cite("kapital.belop", "kapital.valuta", "kapital.type", "kapital.antallAksjer", "kapital.innfortDato"),
                      capital["registered_date"]))
    for register, flag_column, date_column in REGISTERS:
        flag = _flag(row.get(flag_column))
        if flag is None or (not flag and register not in {"merverdiavgiftsregisteret", "foretaksregisteret"}):
            continue  # VAT and Foretaksregisteret status are facts either way; other registers only when registered
        date = _clean(row.get(date_column)) if date_column and flag else None
        field = "vat_registered" if register == "merverdiavgiftsregisteret" else "register_membership"
        facts.append((field, {"register": register, "registered": flag, "registered_date": date},
                      cite(flag_column, *([date_column] if date else [])), date))
    sector = _clean(row.get("institusjonellSektorkode.kode"))
    if sector:
        facts.append(("institutional_sector", {"code": sector, "description": _clean(row.get("institusjonellSektorkode.beskrivelse"))},
                      cite("institusjonellSektorkode.kode", "institusjonellSektorkode.beskrivelse"), None))
    parent = re.sub(r"\D", "", str(row.get("overordnetEnhet") or ""))
    if len(parent) == 9:
        facts.append(("parent_entity", {"organisation_number": parent, "relationship": "registered parent unit (overordnet enhet)"},
                      cite("overordnetEnhet"), None))
    for field, column in (("registered_purpose", "vedtektsfestetFormaal"), ("registered_activity", "aktivitet")):
        text = _clean(row.get(column))
        if text:
            facts.append((field, text[:1000], cite(column), None))
    audit = _clean(row.get("fravalgRevisjonDato"))
    if audit:
        facts.append(("auditor_opt_out", {"opted_out": True, "date": audit}, cite("fravalgRevisjonDato"), audit))
    if str(_clean(row.get("organisasjonsform.kode")) or "").upper() not in PERSONAL_FORMS:
        email = _clean(row.get("epostadresse"))
        if email and "@" in email:
            facts.append(("registry_email", email.casefold(), cite("epostadresse"), None))
        for column in ("telefon", "mobil"):
            phone = _clean(row.get(column))
            if phone and len(re.sub(r"\D", "", phone)) >= 8:
                facts.append(("registry_phone", {"number": phone, "kind": "mobile" if column == "mobil" else "phone"}, cite(column), None))
    return facts


def role_facts(value: dict[str, Any]) -> list[Fact]:
    facts = []
    for item in value.get("roles") or []:
        if item.get("inactive"):
            continue
        name = item.get("name")
        name = " ".join(str(part) for part in name if part) if isinstance(name, list) else _clean(name)
        if not name:
            continue
        holder = "organisation" if item.get("organisation_number") else "person"
        facts.append(("role", {"name": name, "role": item.get("role"), "role_code": item.get("role_code"), "holder_type": holder,
                               "organisation_number": item.get("organisation_number"),
                               "role_group_last_changed": item.get("last_changed")},
                      item.get("claim_span"), item.get("last_changed")))
    return facts


def _address_text(address: Any) -> str | None:
    address = address if isinstance(address, dict) else {}
    street = ", ".join(part.strip() for part in address.get("adresse") or [] if part and part.strip())
    place = " ".join(part for part in (_clean(address.get("postnummer")), _clean(address.get("poststed"))) if part)
    return ", ".join(part for part in (street, place) if part) or None


def location_facts(value: dict[str, Any]) -> list[Fact]:
    facts = []
    for item in value.get("locations") or []:
        industry = item.get("industry") or {}
        location = {"organisation_number": item.get("organisation_number"), "name": _clean(item.get("name")),
                    "address": _address_text(item.get("address")), "municipality": (item.get("address") or {}).get("kommune"),
                    "nace_code": industry.get("kode"), "nace_description": industry.get("beskrivelse"),
                    "employees": item.get("employees"), "start_date": item.get("start_date")}
        if item.get("website"):
            # the sub-unit's own website entry in the registry (ambiguous domains were removed before this point)
            location["subunit_website"] = item["website"]
            location["subunit_website_note"] = (
                "the sub-unit's own website entry in the Brønnøysund register; the same domain as the company's verified website"
                if item.get("website_relation") == "same_as_verified_site" else
                "the sub-unit's own website entry in the Brønnøysund register (a domain no other company files); "
                "labelled as the sub-unit's site, not the company's own")
        facts.append(("registered_location", location, item.get("claim_span"), item.get("start_date")))
    return facts


def related_website_facts(value: dict[str, Any]) -> list[Fact]:
    """A sub-unit's registered website on a domain only that filer uses: the sub-unit's site, never the company's own."""
    facts = []
    for item in value.get("locations") or []:
        if item.get("website") and item.get("website_relation") == "subunit_site":
            raw = str(item["website"]).strip()
            url = raw if "://" in raw else "https://" + raw
            facts.append(("related_website", {
                "url": url, "relationship": "registered website of a sub-unit (underenhet); not the company's own website",
                "subunit_organisation_number": item.get("organisation_number"), "subunit_name": _clean(item.get("name")),
                "label": f"Website of the sub-unit {_clean(item.get('name')) or ''} ({item.get('organisation_number')})".replace("  ", " "),
                **({"site_proof": item["website_site_proof"]} if item.get("website_site_proof") else {})},
                item.get("claim_span"), None))
    return facts


MAX_EVENT_CLAIMS = 30


def registry_event_facts(value: dict[str, Any]) -> list[Fact]:
    """One dated claim per registry change (newest first, capped), each cited in the update-feed response it came from."""
    facts = []
    for item in (value.get("events") or [])[:MAX_EVENT_CLAIMS]:
        facts.append(("registry_event", {key: item.get(key) for key in (
            "date", "type", "entity", "organisation_number", "field", "field_label", "old", "old_stated", "new", "value_note")
            if key != "value_note" or item.get(key)},
            item.get("claim_span"), item.get("date"),
            {"source_url": item.get("source_url"), "content_sha256": item.get("content_sha256"),
             "retrieved_at": item.get("retrieved_at"), "span_kind": "field_path"}))
    return facts


def account_facts(value: dict[str, Any]) -> list[Fact]:
    facts = []
    for record in value.get("records") or []:
        year = fiscal_year(record)
        if not year:
            continue
        period = record.get("period") or {}
        for field, key in ACCOUNT_FIGURES:
            amount = record.get(key)
            if amount is None:
                continue  # not reported is not zero
            facts.append((field, {"amount": amount, "currency": record.get("currency") or "NOK", "fiscal_year": int(year),
                                  "period_start": period.get("fraDato"), "period_end": period.get("tilDato"),
                                  "account_type": record.get("account_type")},
                          "; ".join(part for part in ((record.get("figure_spans") or {}).get(key), record.get("period_span")) if part) or None,
                          period.get("tilDato")))
    return facts


def group_facts(value: list[dict[str, Any]] | None) -> list[Fact]:
    return [("group_relationship", {"organisation_number": item.get("organisation_number"), "name": item.get("name"),
                                    "relationship": item.get("relation") or "member of the same corporate group", "share": item.get("share")},
             item.get("claim_span"), None)
            for item in value or [] if item.get("organisation_number")]


def _span_kind(source_url: Any) -> str:
    return "field_path" if "wikidata" in str(source_url or "") else "quote"


def profile_facts(value: dict[str, Any]) -> list[Fact]:
    facts = []
    for item in value.get("profiles") or []:
        source = (item.get("sources") or [{}])[0]
        facts.append(("external_profile", {"platform": item.get("platform"), "url": item.get("url"),
                                           "identity_proof": (item.get("identity_proof") or [None])[0]},
                      source.get("claim_span"), None,
                      {"source_url": source.get("source_url"), "retrieved_at": source.get("retrieved_at"),
                       "content_sha256": source.get("content_sha256"), "span_kind": _span_kind(source.get("source_url")),
                       "source_class": "open_knowledge_base" if "wikidata" in str(source.get("source_url") or "") else "company_owned"}))
    return facts


def job_facts(value: dict[str, Any]) -> list[Fact]:
    facts: list[Fact] = [("careers_page", {"url": item.get("url"), "title": item.get("title"), "open_postings": item.get("open_postings") or 0},
                          item.get("claim_span"), None,
                          {"source_url": item.get("url"), "content_sha256": item.get("content_sha256"), "span_kind": "quote"})
                         for item in value.get("careers_pages") or []]
    for job in value.get("job_postings") or []:
        if not _clean(job.get("title")):
            continue
        date = (job.get("date_posted") or "")[:10] or None
        facts.append(("job_posting", {"title": _clean(job.get("title")), "url": job.get("url") or job.get("source_url"),
                                      "published_date": date, "date_posted": job.get("date_posted"),
                                      "valid_through": job.get("valid_through"), "employment_type": job.get("employment_type"),
                                      "source_url": job.get("source_url"), "company_match": job.get("company_match")},
                      job.get("claim_span"), (job.get("date_posted") or "")[:10] or None,
                      {"source_url": job.get("source_url"), "content_sha256": job.get("content_sha256"), "span_kind": "quote"}))
    return facts


def site_contact_facts(value: dict[str, Any]) -> list[Fact]:
    """Contact facts the identity-gated site states about itself (JSON-LD Organization, own-domain mailto, tel links),
    each cited on the page it was read from, with that page's hash and the exact attribute or JSON-LD field."""
    contact = value.get("site_contact") or {}
    sources = contact.get("sources") or {}
    pages = {page.get("url"): page.get("content_sha256") for page in value.get("pages") or []}
    facts: list[Fact] = []

    def where(page_url: Any, sha: Any) -> dict[str, Any]:
        return {"source_url": page_url, "content_sha256": sha or pages.get(page_url), "span_kind": "quote"}

    for item in contact.get("organisations") or []:
        spans = item.get("spans") or {}
        for field, key in (("site_address", "address"), ("site_phone", "telephone"), ("site_email", "email"), ("site_logo", "logo")):
            if item.get(key):
                facts.append((field, item[key], spans.get(key), None, where(item.get("page_url"), item.get("content_sha256"))))
    seen = {(fact[0], str(fact[1])) for fact in facts}
    for email in contact.get("emails") or []:
        if ("site_email", email) not in seen:
            source = sources.get(f"email:{email}") or {}
            facts.append(("site_email", email, source.get("raw"), None, where(source.get("page_url"), source.get("content_sha256"))))
    for phone in contact.get("phones") or []:
        if ("site_phone", phone) not in seen:
            source = sources.get(f"phone:{phone}") or {}
            facts.append(("site_phone", phone, source.get("raw"), None, where(source.get("page_url"), source.get("content_sha256"))))
    return facts


def fact_claims(profile: dict[str, Any], records: dict[str, Any], module_states: dict[str, dict[str, Any]],
                add_evidence: Callable[[str, dict[str, Any]], str], website_confidence: float | None,
                registry_row: dict[str, Any] | None, registry_record: dict[str, Any] | None,
                group_related: list[dict[str, Any]] | None = None,
                withheld: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Per-fact claims for every available module record, each with its own evidence span.
    Facts without an exact span or a response hash are appended to `withheld` instead of being published."""
    claims: list[dict[str, Any]] = []
    withheld = withheld if withheld is not None else []

    def available(module: str) -> dict[str, Any] | None:
        record = records.get(module) or {}
        if module in module_states and module_states[module]["state"] != "complete":
            return None
        return record if record.get("status") == "available" else None

    def emit(module: str, record: dict[str, Any], facts: list[Fact], confidence: float | None = 0.99,
             source_class: str = "official_registry") -> None:
        for field, value, span, as_of, *override in facts:
            entry = {"source_url": record.get("source_url"), "source_class": source_class,
                     "retrieved_at": record.get("retrieved_at"), "content_sha256": record.get("content_sha256"),
                     "span_kind": "field_path" if source_class == "official_registry" else "quote"}
            entry.update({key: item for key, item in (override[0] if override else {}).items() if item})
            if not span or not HASH.match(str(entry.get("content_sha256") or "")):
                withheld.append({"field": field, "module": module, "reason": "no exact span" if not span else "no response hash"})
                continue  # a fact is published with its exact citation or not at all
            evidence_id = add_evidence(module, {**entry, "claim_span": span})
            claims.append({"field": field, "value": value, "availability": "available", "confidence": confidence,
                           "as_of": as_of, "evidence_ids": [evidence_id], "module": module, "fact_level": True})

    if registry_row and registry_record:
        emit("registry", registry_record, registry_facts(registry_row))
    for module, builder in (("roles", role_facts), ("locations", location_facts), ("financials", account_facts),
                            ("locations", related_website_facts), ("registry_events", registry_event_facts)):
        record = available(module)
        if record:
            emit(module, record, builder(record.get("value") or {}))
    group = available("group")
    if group and group_related:
        emit("group", group, group_facts(group_related))
    website = available("website")
    publishable = bool(website and ((website.get("value") or {}).get("identity_assessment") or {}).get("publishable"))
    for module, builder in (("external_profiles", profile_facts), ("company_jobs", job_facts)):
        record = available(module)
        if record:
            emit(module, record, builder(record.get("value") or {}), website_confidence or 0.99, "company_owned")
    if publishable:
        emit("website", website, site_contact_facts(website.get("value") or {}), website_confidence, "company_owned")
    return claims
