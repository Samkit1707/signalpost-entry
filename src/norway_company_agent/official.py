from __future__ import annotations

import threading
import time
from typing import Any, Callable

import json

from .citations import csv_span, json_span
from .evidence import evidence
from .http import FetchResult, fetch_json

ACCOUNTING_OBLIGATION_SOURCE = "https://www.brreg.no/en/submission-of-annual-accounts/reporting-obligations-to-the-register-of-company-accounts/who-has-an-accounting-obligation/"
ACCOUNTING_RULESET_VERSION = "brreg_accounting_obligation_rules_2024-08-09_v1"
ALWAYS_ACCOUNTING_OBLIGED_FORMS = {"AS", "ASA", "BRL", "BBL", "STI", "SF", "VPFO"}
THRESHOLD_OR_ACTIVITY_FORMS = {"ENK", "ANS", "DA", "SA", "FLI", "ESEK", "NUF", "UTLA", "ORGL", "SAM", "SPA", "KS", "BO"}

BRREG_ENTITY = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
BRREG_ROLES = BRREG_ENTITY + "/roller"
BRREG_GROUP = "https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{org}"
BRREG_SUBUNITS = "https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=1000"
BRREG_ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap/{org}"
BRREG_ACCOUNT_YEARS = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/aar"
BRREG_ACCOUNT_PDF = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/{year}"

_history_lock = threading.Lock()
_history_last_request = 0.0


def accounting_obligation_assessment(profile: dict[str, Any], *, raw: dict[str, Any] | None = None,
                                    snapshot_sha256: str | None = None, retrieved_at: str | None = None) -> dict[str, Any]:
    """Classify the rule path without inventing a definitive exemption.

    Brreg's public rule is categorical for some forms and threshold/activity dependent for others.
    Registry employee counts are not treated as equivalent to statutory man-years or asset/revenue tests.
    The evidence is the bulk-registry row the classification reads (its columns quoted), not the rule page,
    which is not fetched: the rule set is named in `ruleset_version` and `rule_source`.
    """
    legal_form = str(profile.get("legal_form") or "").upper()
    latest = profile.get("latest_submitted_accounts")
    raw = raw or {}
    org = str(profile.get("organisation_number") or "")
    if latest:
        classification = "filing_observed"
        reason = "The registry snapshot reports a latest submitted annual-account year."
        inputs = [("sisteInnsendteAarsregnskap", raw.get("sisteInnsendteAarsregnskap", latest))]
    elif legal_form in ALWAYS_ACCOUNTING_OBLIGED_FORMS:
        classification = "required_by_legal_form"
        reason = f"Brreg lists organisation form {legal_form} in an always-obliged category."
        inputs = [("organisasjonsform.kode", raw.get("organisasjonsform.kode", legal_form)), ("sisteInnsendteAarsregnskap", raw.get("sisteInnsendteAarsregnskap", ""))]
    elif legal_form in THRESHOLD_OR_ACTIVITY_FORMS:
        classification = "threshold_or_activity_dependent"
        reason = "Obligation depends on statutory size, partner, activity, tax or supervision tests not fully present in the open entity row."
        inputs = [("organisasjonsform.kode", raw.get("organisasjonsform.kode", legal_form)), ("sisteInnsendteAarsregnskap", raw.get("sisteInnsendteAarsregnskap", ""))]
    else:
        classification = "special_rule_or_review_required"
        reason = "The open entity row is insufficient for a definitive accounting-obligation decision."
        inputs = [("organisasjonsform.kode", raw.get("organisasjonsform.kode", legal_form))]
    return evidence(
        "accounting_obligation",
        "available",
        "official_registry_bulk",
        "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv",
        value={
            "classification": classification,
            "legal_form": legal_form or None,
            "latest_submitted_accounts": latest or None,
            "reason": reason,
            "ruleset_version": ACCOUNTING_RULESET_VERSION,
            "rule_source": ACCOUNTING_OBLIGATION_SOURCE,
            "claim_span": csv_span(org, inputs),
        },
        as_of="2024-08-09",
        retrieved_at=retrieved_at,
        content_sha256=snapshot_sha256,
        source_row_key=org or None,
    )


def _get(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


# Normalized figure -> path inside one Regnskapsregisteret record.
FIGURE_PATHS = {
    "revenue": ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
    "operating_result": ("resultatregnskapResultat", "driftsresultat", "driftsresultat"),
    "profit_before_tax": ("resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad"),
    "annual_result": ("resultatregnskapResultat", "aarsresultat"),
    "assets": ("eiendeler", "sumEiendeler"),
    "equity": ("egenkapitalGjeld", "egenkapital", "sumEgenkapital"),
    "debt": ("egenkapitalGjeld", "gjeldOversikt", "sumGjeld"),
}


def normalize_financials(body: Any) -> dict[str, Any]:
    records = body if isinstance(body, list) else []
    if not records:
        return {"records": []}
    normalized = []
    for index, item in enumerate(records[:3]):
        record = {
            "json_index": index,
            "record_id": item.get("id"),
            "account_type": item.get("regnskapstype"),
            "period": item.get("regnskapsperiode"),
            "currency": item.get("valuta"),
            **{key: _get(item, *path) for key, path in FIGURE_PATHS.items()},
        }
        period = item.get("regnskapsperiode") or {}
        record["period_span"] = json_span([(f"$[{index}].id", item.get("id")), (f"$[{index}].regnskapstype", item.get("regnskapstype")),
                                           (f"$[{index}].regnskapsperiode.fraDato", period.get("fraDato")),
                                           (f"$[{index}].regnskapsperiode.tilDato", period.get("tilDato"))])
        record["figure_spans"] = {key: json_span([(f"$[{index}]." + ".".join(path), record[key])])
                                  for key, path in FIGURE_PATHS.items() if record[key] is not None}
        normalized.append(record)
    return {"records": normalized}


def normalize_financial_history(body: Any, org: str) -> dict[str, Any]:
    years = sorted({str(year) for year in body if str(year).isdigit()}) if isinstance(body, list) else []
    return {
        "years": years,
        "pdfs": [
            {"year": year, "url": BRREG_ACCOUNT_PDF.format(org=org, year=year)}
            for year in reversed(years)
        ],
    }


def _reserve_history_slot(clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep) -> None:
    """Reserve starts at least 2.1 seconds apart without serializing response time."""
    global _history_last_request
    with _history_lock:
        delay = 2.1 - (clock() - _history_last_request)
        if delay > 0:
            sleeper(delay)
        _history_last_request = clock()


def _fetch_history(url: str) -> FetchResult:
    """Keep this endpoint below its observed 30-request-starts/minute allowance."""
    _reserve_history_slot()
    return fetch_json(url)


def normalize_roles(body: Any) -> dict[str, Any]:
    roles = []
    for group_index, group in enumerate(body.get("rollegrupper", []) if isinstance(body, dict) else []):
        changed = group.get("sistEndret")
        for role_index, item in enumerate(group.get("roller", [])):
            person = item.get("person") or {}
            name = person.get("navn") or {}
            entity = item.get("enhet") or {}
            display_name = " ".join(filter(None, [name.get("fornavn"), name.get("mellomnavn"), name.get("etternavn")])) or entity.get("navn")
            path = f"$.rollegrupper[{group_index}].roller[{role_index}]"
            pairs = [(f"{path}.type.beskrivelse", _get(item, "type", "beskrivelse"))]
            if entity:
                pairs += [(f"{path}.enhet.organisasjonsnummer", entity.get("organisasjonsnummer")), (f"{path}.enhet.navn", entity.get("navn"))]
            else:
                pairs += [(f"{path}.person.navn.{key}", name.get(key)) for key in ("fornavn", "mellomnavn", "etternavn") if name.get(key)]
            if changed:
                pairs.append((f"$.rollegrupper[{group_index}].sistEndret", changed))
            roles.append({
                "claim_span": json_span(pairs),
                "name": display_name or None,
                "organisation_number": entity.get("organisasjonsnummer"),
                "role_code": _get(item, "type", "kode"),
                "role": _get(item, "type", "beskrivelse"),
                "group_code": _get(group, "type", "kode"),
                "group": _get(group, "type", "beskrivelse"),
                "last_changed": changed,
                "inactive": bool(item.get("avregistrert")),
            })
    return {"roles": roles}


def normalize_locations(body: Any) -> dict[str, Any]:
    rows = _get(body, "_embedded", "underenheter") or []
    total = _get(body, "page", "totalElements")
    return {"total_elements": total, "locations": [{
        "claim_span": json_span([(f"$._embedded.underenheter[{index}].organisasjonsnummer", item.get("organisasjonsnummer")),
                                 (f"$._embedded.underenheter[{index}].navn", item.get("navn")),
                                 *[(f"$._embedded.underenheter[{index}].{kind}.{key}", (item.get(kind) or {}).get(key))
                                   for kind in [("beliggenhetsadresse" if item.get("beliggenhetsadresse") else "postadresse")]
                                   for key in ("adresse", "postnummer", "poststed") if (item.get(kind) or {}).get(key)]]),
        "organisation_number": item.get("organisasjonsnummer"),
        "name": item.get("navn"),
        "address": item.get("beliggenhetsadresse") or item.get("postadresse"),
        "industry": item.get("naeringskode1"),
        "employees": item.get("antallAnsatte"),
        "start_date": item.get("oppstartsdato"),
    } for index, item in enumerate(rows)]}


def normalize_entity(body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    return {
        "organisation_number": body.get("organisasjonsnummer"),
        "name": body.get("navn"),
        "legal_form": _get(body, "organisasjonsform", "kode"),
        "employees": body.get("antallAnsatte"),
        "bankrupt": body.get("konkurs"),
        "liquidating": body.get("underAvvikling"),
        "website": body.get("hjemmeside"),
        "industry": body.get("naeringskode1"),
        "business_address": body.get("forretningsadresse"),
        "postal_address": body.get("postadresse"),
        "latest_submitted_accounts": body.get("sisteInnsendteAarsregnskap"),
        # Deleted entities still answer HTTP 200, as respons_klasse "SlettetEnhet" with a slettedato.
        "deleted": body.get("respons_klasse") == "SlettetEnhet" or bool(body.get("slettedato")),
        "deleted_date": body.get("slettedato"),
        # Further registry attributes for fact-level claims when the bulk row is missing (see facts.live_as_bulk_row).
        "extras": {key: body.get(key) for key in (
            "stiftelsesdato", "registreringsdatoEnhetsregisteret", "registrertIMvaregisteret", "registrertIForetaksregisteret",
            "registrertIFrivillighetsregisteret", "registrertIStiftelsesregisteret", "overordnetEnhet", "epostadresse", "telefon",
            "mobil", "fravalgRevisjonDato", "naeringskode2", "naeringskode3", "institusjonellSektorkode", "kapital",
            "vedtektsfestetFormaal", "aktivitet") if body.get(key) not in (None, "", [], {})},
    }


# A 200 answer with nothing in it: checked and none found (not_found), never "complete" with an empty value.
EMPTY_ANSWER = {
    "financials": lambda value: not (value or {}).get("records") and "$ = [] (no annual accounts returned)",
    "roles": lambda value: not (value or {}).get("roles") and "$.rollegrupper = [] (no registered roles)",
    "locations": lambda value: not (value or {}).get("locations")
                 and f"$.page.totalElements={json.dumps((value or {}).get('total_elements', 0))} (no registered sub-units)",
}


def _classified(field: str, source_type: str, result: FetchResult, value: Any = None) -> dict[str, Any]:
    empty = EMPTY_ANSWER.get(field)
    if result.status == 200 and empty and empty(value):
        return evidence(field, "not_found", source_type, result.url, note=empty(value), content_sha256=result.content_sha256,
                        retrieved_at=result.retrieved_at, effective_at=result.effective_at)
    if result.status == 200:
        return evidence(field, "available", source_type, result.url, value=result.body if value is None else value, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at)
    if result.status in {404, 410}:
        return evidence(field, "not_found", source_type, result.url, note=result.error, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at)
    return evidence(field, "source_error", source_type, result.url, note=result.error, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at)


def fetch_official_modules(org: str, modules: set[str], fetcher: Callable[[str], FetchResult] = fetch_json) -> tuple[dict[str, Any], list[FetchResult]]:
    records: dict[str, Any] = {}
    metrics: list[FetchResult] = []
    endpoints = {
        "registry_live": (BRREG_ENTITY.format(org=org), "official_registry_live"),
        "financials": (BRREG_ACCOUNTS.format(org=org), "official_annual_accounts"),
        "financial_history": (BRREG_ACCOUNT_YEARS.format(org=org), "official_annual_account_copies"),
        "roles": (BRREG_ROLES.format(org=org), "official_roles"),
        "group": (BRREG_GROUP.format(org=org), "official_group_structure"),
        "locations": (BRREG_SUBUNITS.format(org=org), "official_subunits"),
    }
    for module, (url, source_type) in endpoints.items():
        if module not in modules:
            continue
        result = _fetch_history(url) if module == "financial_history" and fetcher is fetch_json else fetcher(url)
        metrics.append(result)
        normalized = None
        if result.status == 200:
            normalized = normalize_entity(result.body) if module == "registry_live" else normalize_financials(result.body) if module == "financials" else normalize_financial_history(result.body, org) if module == "financial_history" else normalize_roles(result.body) if module == "roles" else normalize_locations(result.body) if module == "locations" else result.body
        records[module] = _classified(module, source_type, result, value=normalized)
    return records, metrics
