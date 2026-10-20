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
    for key, column in (("stiftelsesdato", "stiftelsesdato"), ("registreringsdatoEnhetsregisteret", "registreringsdatoenhetsregisteret"),
                        ("registrertIMvaregisteret", "registrertIMvaRegisteret"), ("registrertIForetaksregisteret", "registrertIForetaksregisteret"),
                        ("registrertIFrivillighetsregisteret", "registrertIFrivillighetsregisteret"),
                        ("registrertIStiftelsesregisteret", "registrertIStiftelsesregisteret"), ("overordnetEnhet", "overordnetEnhet"),
                        ("epostadresse", "epostadresse"), ("telefon", "telefon"), ("mobil", "mobil"),
                        ("fravalgRevisjonDato", "fravalgRevisjonDato")):
        row[column] = extras.get(key)
    for key in ("naeringskode2", "naeringskode3", "institusjonellSektorkode"):
        item = extras.get(key) or {}
        row[f"{key}.kode"], row[f"{key}.beskrivelse"] = item.get("kode"), item.get("beskrivelse")
    capital = extras.get("kapital") or {}
    for key in ("belop", "antallAksjer", "type", "valuta", "innfortDato"):
        row[f"kapital.{key}"] = capital.get(key)
    for key in ("vedtektsfestetFormaal", "aktivitet"):
        raw = extras.get(key)
        row[key] = " ".join(raw) if isinstance(raw, list) else raw
    return row


def registry_facts(row: dict[str, Any]) -> list[Fact]:
    facts: list[Fact] = []
    for rank in (1, 2, 3):
        code = _clean(row.get(f"naeringskode{rank}.kode"))
        if code:
            description = _clean(row.get(f"naeringskode{rank}.beskrivelse"))
            facts.append(("nace_code", {"code": code, "description": description, "rank": rank},
                          f"naeringskode{rank}={code} {description or ''}".strip(), None))
    for field, column in (("founding_date", "stiftelsesdato"), ("registration_date", "registreringsdatoenhetsregisteret")):
        date = _clean(row.get(column))
        if date:
            facts.append((field, date, f"{column}={date}", date))
    amount = _number(row.get("kapital.belop"))
    if amount is not None:
        capital = {"amount": amount, "currency": _clean(row.get("kapital.valuta")) or "NOK", "type": _clean(row.get("kapital.type")),
                   "shares": int(_number(row.get("kapital.antallAksjer"))) if _number(row.get("kapital.antallAksjer")) is not None else None,
                   "registered_date": _clean(row.get("kapital.innfortDato"))}
        facts.append(("share_capital", capital, f"kapital={row.get('kapital.belop')} {capital['currency']}; antallAksjer={row.get('kapital.antallAksjer')}",
                      capital["registered_date"]))
    for register, flag_column, date_column in REGISTERS:
        flag = _flag(row.get(flag_column))
        if flag is None or (not flag and register not in {"merverdiavgiftsregisteret", "foretaksregisteret"}):
            continue  # VAT and Foretaksregisteret status are facts either way; other registers only when registered
        date = _clean(row.get(date_column)) if date_column and flag else None
        field = "vat_registered" if register == "merverdiavgiftsregisteret" else "register_membership"
        facts.append((field, {"register": register, "registered": flag, "registered_date": date},
                      f"{flag_column}={str(flag).lower()}" + (f"; {date_column}={date}" if date else ""), date))
    sector = _clean(row.get("institusjonellSektorkode.kode"))
    if sector:
        facts.append(("institutional_sector", {"code": sector, "description": _clean(row.get("institusjonellSektorkode.beskrivelse"))},
                      f"institusjonellSektorkode={sector}", None))
    parent = re.sub(r"\D", "", str(row.get("overordnetEnhet") or ""))
    if len(parent) == 9:
        facts.append(("parent_entity", {"organisation_number": parent, "relationship": "registered parent unit (overordnet enhet)"},
                      f"overordnetEnhet={parent}", None))
    for field, column in (("registered_purpose", "vedtektsfestetFormaal"), ("registered_activity", "aktivitet")):
        text = _clean(row.get(column))
        if text:
            facts.append((field, text[:1000], f"{column}={text[:160]}", None))
    audit = _clean(row.get("fravalgRevisjonDato"))
    if audit:
        facts.append(("auditor_opt_out", {"opted_out": True, "date": audit}, f"fravalgRevisjonDato={audit}", audit))
    if str(_clean(row.get("organisasjonsform.kode")) or "").upper() not in PERSONAL_FORMS:
        email = _clean(row.get("epostadresse"))
        if email and "@" in email:
            facts.append(("registry_email", email.casefold(), f"epostadresse={email}", None))
        for column in ("telefon", "mobil"):
            phone = _clean(row.get(column))
            if phone and len(re.sub(r"\D", "", phone)) >= 8:
                facts.append(("registry_phone", {"number": phone, "kind": "mobile" if column == "mobil" else "phone"}, f"{column}={phone}", None))
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
                      f"{item.get('role')}: {name}", item.get("last_changed")))
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
        facts.append(("registered_location", location,
                      f"underenhet {location['organisation_number']} {location['name'] or ''} {location['address'] or ''}".strip(),
                      item.get("start_date")))
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
                          f"regnskap {record.get('record_id')} {record.get('account_type')} {period.get('fraDato')}..{period.get('tilDato')} {key}={amount}",
                          period.get("tilDato")))
    return facts


def group_facts(value: list[dict[str, Any]] | None) -> list[Fact]:
    return [("group_relationship", {"organisation_number": item.get("organisation_number"), "name": item.get("name"),
                                    "relationship": item.get("relation") or "member of the same corporate group", "share": item.get("share")},
             f"{item.get('relation')}: {item.get('name')} ({item.get('organisation_number')})", None)
            for item in value or [] if item.get("organisation_number")]


def profile_facts(value: dict[str, Any]) -> list[Fact]:
    facts = []
    for item in value.get("profiles") or []:
        source = (item.get("sources") or [{}])[0]
        facts.append(("external_profile", {"platform": item.get("platform"), "url": item.get("url"),
                                           "identity_proof": (item.get("identity_proof") or [None])[0]},
                      f"{item.get('platform')}: {item.get('url')} ({source.get('method') or 'link'})", None,
                      {"source_url": source.get("source_url"), "retrieved_at": source.get("retrieved_at"),
                       "content_sha256": source.get("content_sha256"),
                       "source_class": "open_knowledge_base" if "wikidata" in str(source.get("source_url") or "") else "company_owned"}))
    return facts


def job_facts(value: dict[str, Any]) -> list[Fact]:
    facts: list[Fact] = [("careers_page", {"url": item.get("url"), "title": item.get("title")}, f"careers page {item.get('url')}", None)
                         for item in value.get("careers_pages") or []]
    for job in value.get("job_postings") or []:
        if not _clean(job.get("title")):
            continue
        facts.append(("job_posting", {"title": _clean(job.get("title")), "date_posted": job.get("date_posted"),
                                      "valid_through": job.get("valid_through"), "employment_type": job.get("employment_type"),
                                      "source_url": job.get("source_url")},
                      f"JobPosting: {job.get('title')} ({job.get('date_posted') or 'no date'})", (job.get("date_posted") or "")[:10] or None,
                      {"source_url": job.get("source_url"), "content_sha256": job.get("content_sha256")}))
    return facts


def site_contact_facts(value: dict[str, Any]) -> list[Fact]:
    """Contact facts the identity-gated site states about itself (JSON-LD Organization, own-domain mailto, tel links)."""
    contact = value.get("site_contact") or {}
    facts: list[Fact] = []
    for item in contact.get("organisations") or []:
        if item.get("address"):
            facts.append(("site_address", item["address"], f"JSON-LD address: {item['address']}", None))
        if item.get("telephone"):
            facts.append(("site_phone", item["telephone"], f"JSON-LD telephone: {item['telephone']}", None))
        if item.get("email"):
            facts.append(("site_email", item["email"], f"JSON-LD email: {item['email']}", None))
        if item.get("logo"):
            facts.append(("site_logo", item["logo"], f"JSON-LD logo: {item['logo']}", None))
    seen = {(field, str(value_)) for field, value_, _, _ in facts}
    for email in contact.get("emails") or []:
        if ("site_email", email) not in seen:
            facts.append(("site_email", email, f"mailto:{email}", None))
    for phone in contact.get("phones") or []:
        if ("site_phone", phone) not in seen:
            facts.append(("site_phone", phone, f"tel:{phone}", None))
    return facts


def fact_claims(profile: dict[str, Any], records: dict[str, Any], module_states: dict[str, dict[str, Any]],
                add_evidence: Callable[[str, dict[str, Any]], str], website_confidence: float | None,
                registry_row: dict[str, Any] | None, registry_record: dict[str, Any] | None,
                group_related: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Per-fact claims for every available module record, each with its own evidence span."""
    claims: list[dict[str, Any]] = []

    def available(module: str) -> dict[str, Any] | None:
        record = records.get(module) or {}
        if module in module_states and module_states[module]["state"] != "complete":
            return None
        return record if record.get("status") == "available" else None

    def emit(module: str, record: dict[str, Any], facts: list[Fact], confidence: float | None = 0.99,
             source_class: str = "official_registry") -> None:
        for field, value, span, as_of, *override in facts:
            entry = {"source_url": record.get("source_url"), "source_class": source_class,
                     "retrieved_at": record.get("retrieved_at"), "content_sha256": record.get("content_sha256")}
            entry.update({key: item for key, item in (override[0] if override else {}).items() if item})
            evidence_id = add_evidence(module, {**entry, "claim_span": span})
            claims.append({"field": field, "value": value, "availability": "available", "confidence": confidence,
                           "as_of": as_of, "evidence_ids": [evidence_id], "module": module, "fact_level": True})

    if registry_row and registry_record:
        emit("registry", registry_record, registry_facts(registry_row))
    for module, builder in (("roles", role_facts), ("locations", location_facts), ("financials", account_facts)):
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
