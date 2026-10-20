"""Short, sourced company summary for each envelope (template-based, no LLM).

Every sentence is built only from the envelope's own claims and carries the evidence ids it rests on;
a sentence without evidence is dropped. Rules:
- "latest" financials are the newest filed fiscal year (period end), company accounts before group accounts;
- a trend is stated only when two filed years of the same account type both report the figure;
- a missing figure is written as "not reported", never as 0;
- what is unknown is stated as unknown (missing is not zero).
"""
from __future__ import annotations

from typing import Any

LEGAL_FORMS = {
    "AS": "private limited company (AS)", "ASA": "public limited company (ASA)", "ENK": "sole proprietorship (ENK)",
    "NUF": "Norwegian-registered foreign company (NUF)", "DA": "partnership with shared liability (DA)",
    "ANS": "general partnership (ANS)", "SA": "cooperative (SA)", "STI": "foundation (STI)",
    "FLI": "association (FLI)", "KS": "limited partnership (KS)", "BA": "company with limited liability (BA)",
    "SPA": "savings bank (SPA)", "ESEK": "condominium owners' association (ESEK)", "BRL": "housing cooperative (BRL)", "KF": "municipal enterprise (KF)", "IKS": "inter-municipal company (IKS)",
}
UNKNOWN_LABELS = {
    "official_website": "an identity-verified website",
    "dated_news": "dated news on its own website",
    "company_jobs": "job postings or a careers page",
    "external_profiles": "company-owned social profiles",
    "registered_employees": "a registered employee count",
    "roles": "registered roles",
}
FAILED_LABELS = {"blocked": "blocked (robots.txt or policy)", "failed": "source error or run budget reached"}


def fiscal_year(record: dict[str, Any]) -> str | None:
    end = str(((record or {}).get("period") or {}).get("tilDato") or "")
    return end[:4] if len(end) >= 4 and end[:4].isdigit() else None


def newest_filed_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest filed year (latest period end); company accounts win over group accounts in the same year."""
    dated = [item for item in records or [] if fiscal_year(item)]
    if not dated:
        return None
    return max(dated, key=lambda item: ((item.get("period") or {}).get("tilDato") or "", item.get("account_type") != "KONSERN"))


def previous_filed_record(records: list[dict[str, Any]], latest: dict[str, Any]) -> dict[str, Any] | None:
    year = fiscal_year(latest)
    same_type = [item for item in records or [] if item.get("account_type") == latest.get("account_type")
                 and fiscal_year(item) and fiscal_year(item) < year]
    return newest_filed_record(same_type)


def money(value: Any, currency: str | None = "NOK") -> str:
    if value is None:
        return "not reported"
    number = float(value)
    text = f"{abs(number):,.0f}"
    return f"{currency or 'NOK'} {'-' if number < 0 else ''}{text}"


def org_display(org: str) -> str:
    return f"{org[:3]} {org[3:6]} {org[6:]}" if len(org) == 9 else org


def _name(value: Any) -> str:
    """Role holders that are organisations come as a list of name lines."""
    if isinstance(value, list):
        return " ".join(str(part) for part in value if part).strip()
    return str(value or "").strip()


def _first(claims: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    return next((claim for claim in claims if claim.get("field") == field), None)


def _value(claim: dict[str, Any] | None) -> Any:
    return claim.get("value") if claim and claim.get("availability") == "available" else None


def build_synthesis(profile: dict[str, Any], claims: list[dict[str, Any]], evidence_list: list[dict[str, Any]]) -> dict[str, Any]:
    known_ids = {item["id"] for item in evidence_list}
    sentences: list[dict[str, Any]] = []

    def say(topic: str, text: str, *sources: Any) -> None:
        ids: list[str] = []
        for source in sources:
            for evidence_id in (source or {}).get("evidence_ids", []) if isinstance(source, dict) else source or []:
                if evidence_id in known_ids and evidence_id not in ids:
                    ids.append(evidence_id)
        if ids:
            sentences.append({"topic": topic, "text": " ".join(text.split()), "evidence_ids": ids})

    org = str(profile.get("organisation_number") or "")
    name_claim = _first(claims, "legal_name")
    name = _value(name_claim) or profile.get("name") or f"Organisation {org_display(org)}"

    # What the company is and does.
    form_claim, industry_claim, address_claim = _first(claims, "legal_form"), _first(claims, "industry"), _first(claims, "business_address")
    form, industry, address = _value(form_claim), _value(industry_claim), _value(address_claim)
    parts = [f"{name} (org. no. {org_display(org)}) is registered as a {LEGAL_FORMS.get(str(form), f'legal form {form}') if form else 'legal entity of unreported form'}"]
    if industry:
        parts.append(f"with main industry {industry.get('code')} {industry.get('description') or ''}".rstrip())
    if address:
        parts.append(f"and business address {address}")
    say("what_it_does", " ".join(parts) + ".",
        *[claim for claim in (name_claim, form_claim, industry_claim, address_claim) if _value(claim) is not None])

    activity = _first(claims, "registered_activity") or _first(claims, "registered_purpose")
    if _value(activity):
        text = str(_value(activity))
        say("what_it_does", f"Its registered activity: “{text[:220]}{'…' if len(text) > 220 else ''}”.", activity)
    founded, registered = _first(claims, "founding_date"), _first(claims, "registration_date")
    if _value(founded):
        extra = f", registered {_value(registered)}" if _value(registered) else ""
        say("what_it_does", f"It was founded {_value(founded)}{extra}.", founded, registered if _value(registered) else None)
    subunits = [claim for claim in claims if claim.get("field") == "registered_location" and claim.get("availability") == "available"]
    if subunits:
        places = sorted({str(claim["value"].get("municipality") or "").title() for claim in subunits if claim["value"].get("municipality")})
        where = f" ({', '.join(places[:4])}{'…' if len(places) > 4 else ''})" if places else ""
        say("what_it_does", f"It has {len(subunits)} registered sub-unit{'s' if len(subunits) != 1 else ''}{where}.", *subunits[:5])

    website_claim = _first(claims, "official_website")
    website_url = _value(website_claim)
    if website_url:
        site = ((profile.get("evidence") or {}).get("website") or {}).get("value") or {}
        blurb = " ".join(str(site.get("description") or "").split())[:220]
        title = " ".join(str(site.get("title") or "").split())[:160]
        if blurb:
            say("what_it_does", f"Its identity-verified website {website_url} describes it as “{blurb}”.", website_claim)
        elif title:
            say("what_it_does", f"Its identity-verified website {website_url} is titled “{title}”.", website_claim)
        else:
            say("what_it_does", f"Its identity-verified website is {website_url}.", website_claim)

    employees_claim = _first(claims, "registered_employees")
    if _value(employees_claim) is not None:
        say("what_it_does", f"The register lists {_value(employees_claim)} employees (as reported to the register, not live headcount).", employees_claim)
    roles_claim = _first(claims, "roles")
    roles = _value(roles_claim) or []
    if roles:
        shown = "; ".join(f"{item.get('role')}: {_name(item.get('name'))}" for item in roles[:3] if _name(item.get("name")))
        if shown:
            say("what_it_does", f"Registered roles include {shown}.", roles_claim)
    profiles_claim = _first(claims, "external_profiles")
    platforms = sorted({str(item.get("platform")) for item in _value(profiles_claim) or [] if item.get("platform")})
    if platforms:
        say("what_it_does", f"Company-owned profiles found: {', '.join(platforms)}.", profiles_claim)
    jobs_claim = _first(claims, "company_jobs")
    jobs = _value(jobs_claim) or {}
    if jobs:
        postings = len(jobs.get("job_postings") or [])
        text = (f"Its verified website publishes {postings} structured job posting{'s' if postings != 1 else ''}."
                if postings else "Its verified website has a careers page; no structured job posting was found, so open vacancies are unknown.")
        say("what_it_does", text, jobs_claim)

    # Latest financials (newest filed year) and what changed.
    financial_claim = _first(claims, "latest_annual_accounts")
    records = ((((profile.get("evidence") or {}).get("financials") or {}).get("value") or {}).get("records") or []) \
        if (financial_claim or {}).get("availability") == "available" else []
    latest = newest_filed_record(records)
    if latest:
        year, currency = fiscal_year(latest), latest.get("currency") or "NOK"
        kind = "group accounts" if latest.get("account_type") == "KONSERN" else "company accounts"
        figures = [("revenue", "revenue"), ("operating_result", "operating result"), ("annual_result", "annual result"), ("equity", "equity")]
        if all(latest.get(key) is None for key, _ in figures):
            say("financials", f"The newest filed annual accounts are for FY{year} ({kind}), but they report no revenue, result or equity figures.", financial_claim)
        else:
            listed = ", ".join(f"{label} {money(latest.get(key), currency)}" for key, label in figures)
            say("financials", f"Latest filed annual accounts (FY{year}, {kind}): {listed}.", financial_claim)
        previous = previous_filed_record(records, latest)
        if previous:
            prev_year = fiscal_year(previous)
            changes = []
            for key, label in (("revenue", "revenue"), ("annual_result", "annual result")):
                old, new = previous.get(key), latest.get(key)
                if old is None or new is None:
                    continue
                direction = "rose" if new > old else "fell" if new < old else "was unchanged"
                pct = f" ({(new - old) / old * 100:+.1f}%)" if old > 0 and new != old else ""
                changes.append(f"{label} {direction} from {money(old, currency)} in FY{prev_year} to {money(new, currency)} in FY{year}{pct}")
            if changes:
                text = "; ".join(changes)
                say("changes", text[0].upper() + text[1:] + ".", financial_claim)
            else:
                say("unknown", f"No financial trend is stated: FY{prev_year} and FY{year} do not both report revenue or annual result.", financial_claim)
        else:
            say("unknown", f"Only one filed year (FY{year}) is available, so no financial trend is stated.", financial_claim)
    elif financial_claim:
        say("financials", "No annual accounts were returned by the Accounts Register for this organisation number.", financial_claim)

    status_claim = _first(claims, "registry_status")
    status = _value(status_claim) or {}
    if status.get("deleted"):
        say("changes", f"The live register lists the entity as deleted{(' on ' + str(status['deleted_date'])) if status.get('deleted_date') else ''}.", status_claim)
    elif status.get("bankrupt"):
        say("changes", "The live register lists the entity as bankrupt.", status_claim)
    elif status.get("liquidating"):
        say("changes", "The live register lists the entity as under liquidation.", status_claim)
    elif status:
        say("changes", "The live register shows no bankruptcy, liquidation or deletion.", status_claim)

    news_claims = [claim for claim in claims if claim.get("field") == "dated_news" and claim.get("availability") == "available"]
    if news_claims:
        newest = max(news_claims, key=lambda claim: claim["value"]["published_at"])
        more = f" ({len(news_claims)} dated items found)" if len(news_claims) > 1 else ""
        say("changes", f"Latest dated news on its own website: “{newest['value']['title']}” published {newest['value']['published_at']}{more}.", newest)

    # What is unknown.
    missing = [(field, claim) for field in UNKNOWN_LABELS for claim in [_first(claims, field)]
               if claim and claim.get("availability") == "not_available"]
    if missing:
        say("unknown", "Not found in the sources checked (missing is not zero): " + ", ".join(UNKNOWN_LABELS[field] for field, _ in missing) + ".",
            *[claim for _, claim in missing])
    if (website_claim or {}).get("availability") == "ambiguous":
        say("unknown", "A registry-linked website was fetched but did not pass the exact-identity check, so nothing from it is used.", website_claim)
    for state, label in FAILED_LABELS.items():
        failed = [claim for claim in claims if claim.get("availability") == state]
        fields = sorted({claim["field"] for claim in failed})
        if fields:
            say("unknown", f"Could not be checked this run ({label}): {', '.join(fields)}.", *failed)

    narrative = " ".join(f"{item['text']} [{', '.join(item['evidence_ids'])}]" for item in sentences)
    return {"method": "template_from_claims_v1", "sentences": sentences, "narrative": narrative,
            "latest_fiscal_year": fiscal_year(latest) if latest else None}
