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


def _pct(old: float, new: float) -> float | None:
    return (new - old) / abs(old) * 100 if old else None


def _result_phrase(old: float, new: float, currency: str) -> str:
    if old == 0 or new == 0:
        word = "broke even" if new == 0 else "moved from break-even"
        return f"the annual result {word} ({money(old, currency)} → {money(new, currency)})"
    if old > 0 and new > 0:
        return f"the annual result {'rose' if new > old else 'fell' if new < old else 'was unchanged'} ({money(old, currency)} → {money(new, currency)})"
    if old < 0 and new < 0:
        return f"the loss {'narrowed' if new > old else 'widened' if new < old else 'was unchanged'} ({money(old, currency)} → {money(new, currency)})"
    return ""


def why_it_matters(latest: dict[str, Any], previous: dict[str, Any] | None) -> str:
    """One sentence on what the year-on-year change means, from the two filed years' figures only (no forecast)."""
    year = fiscal_year(latest)
    currency = latest.get("currency") or "NOK"
    if not previous:
        return f"Why it matters: no year-on-year change can be judged, because FY{year} is the only filed year."
    prev = fiscal_year(previous)
    rev_old, rev_new = previous.get("revenue"), latest.get("revenue")
    res_old, res_new = previous.get("annual_result"), latest.get("annual_result")
    equity = latest.get("equity")
    parts: list[str] = []
    if res_old is not None and res_new is not None and res_old * res_new < 0:  # profit <-> loss (zero is break-even, not a flip)
        parts.append(f"the company went from {'a profit' if res_old > 0 else 'a loss'} of {money(abs(res_old), currency)} in FY{prev} "
                     f"to {'a loss' if res_new < 0 else 'a profit'} of {money(abs(res_new), currency)} in FY{year}, so FY{year} was "
                     f"{'loss-making after a profitable year' if res_new < 0 else 'profitable after a loss-making year'}")
    elif rev_old is not None and rev_new is not None and res_old is not None and res_new is not None and rev_old != rev_new:
        pct = _pct(rev_old, rev_new)
        size = f" ({pct:+.1f}%)" if pct is not None else ""
        sales = f"revenue {'rose' if rev_new > rev_old else 'fell'}{size}"
        result = _result_phrase(res_old, res_new, currency) or f"the annual result moved ({money(res_old, currency)} → {money(res_new, currency)})"
        better = res_new > res_old
        if (rev_new > rev_old) == better:
            meaning = ("both revenue and the result grew" if better else "both revenue and the result shrank") if res_new != res_old \
                else "the result held steady while revenue changed"
        else:
            gained = "lost less" if res_new < 0 else "earned more"
            meaning = (f"it {gained} on lower revenue" if better else "higher revenue did not lift the result")
        parts.append(f"{sales} {'and' if (rev_new > rev_old) == better else 'while'} {result}: {meaning}")
    elif rev_old is not None and rev_new is not None and rev_old != rev_new:
        pct = _pct(rev_old, rev_new)
        if pct is not None and pct >= 100:
            parts.append(f"revenue more than doubled ({money(rev_old, currency)} → {money(rev_new, currency)}), a step change in the size of the business")
        elif pct is not None and pct <= -50:
            parts.append(f"revenue more than halved ({money(rev_old, currency)} → {money(rev_new, currency)}), a sharp contraction of the business")
        else:
            parts.append(f"revenue {'rose' if rev_new > rev_old else 'fell'}{f' ({pct:+.1f}%)' if pct is not None else ''}; "
                         "the annual result is not reported for both years, so the effect on profit is unknown")
    elif res_old is not None and res_new is not None and res_old != res_new:
        parts.append(f"{_result_phrase(res_old, res_new, currency)}; revenue is not reported for both years")
    if equity is not None and equity < 0:
        parts.append(f"equity was negative ({money(equity, currency)}) at the end of FY{year}, meaning reported liabilities exceeded reported assets")
    if not parts:
        return f"Why it matters: no year-on-year change can be judged, because FY{prev} and FY{year} do not both report revenue or annual result, or they are unchanged."
    text = "; ".join(parts)
    return f"Why it matters (FY{prev} → FY{year}): {text[0].upper()}{text[1:]}."


def short_money(value: float, currency: str = "NOK") -> str:
    """NOK 17.4m / NOK 912k / NOK 640 (sign kept)."""
    number = float(value)
    sign, size = ("-" if number < 0 else ""), abs(number)
    if size >= 1e9:
        text = f"{size / 1e9:.1f}bn"
    elif size >= 1e6:
        text = f"{size / 1e6:.1f}m"
    elif size >= 1e4:
        text = f"{size / 1e3:.0f}k"
    else:
        text = f"{size:,.0f}"
    return f"{currency} {sign}{text}"


def headline(claims: list[dict[str, Any]], latest: dict[str, Any] | None, previous: dict[str, Any] | None) -> tuple[str, list[Any]] | None:
    """v6: the summary opens with one buyer-relevant fact and its source; the registry identity follows it.
    Order: a distress status, revenue (with the year-on-year change), the result when no revenue is filed, open job
    postings, the newest dated news, the registered headcount, a recent founding."""
    status_claim = _first(claims, "registry_status")
    status = _value(status_claim) or {}
    if status.get("bankrupt"):
        return "Registered as bankrupt (Brønnøysund register).", [status_claim]
    if status.get("liquidating"):
        return "Registered as under liquidation (Brønnøysund register).", [status_claim]
    if status.get("deleted"):
        return "Deleted from the register" + (f" on {status['deleted_date']}" if status.get("deleted_date") else "") + " (Brønnøysund register).", [status_claim]
    financial_claim = _first(claims, "latest_annual_accounts")
    if latest and financial_claim:
        year, currency = fiscal_year(latest), latest.get("currency") or "NOK"
        kind = " group" if latest.get("account_type") == "KONSERN" else ""
        prev_year = fiscal_year(previous) if previous else None
        for key, label in (("revenue", "Revenue"), ("operating_result", "Operating result"), ("annual_result", "Annual result")):
            new = latest.get(key)
            if new is None or (key == "revenue" and new == 0 and (latest.get("operating_result") or latest.get("annual_result"))):
                continue
            old = previous.get(key) if previous else None
            change = ""
            if old is not None and prev_year:
                if new == old:
                    change = f", unchanged from FY{prev_year}"
                elif key == "revenue" and old > 0:
                    pct = (new - old) / old * 100
                    change = f", {'up' if pct > 0 else 'down'} {abs(pct):.0f}% from FY{prev_year}" if round(abs(pct)) >= 1 else f", flat on FY{prev_year}"
                else:
                    change = f", {'up' if new > old else 'down'} from {short_money(old, currency)} in FY{prev_year}"
            return f"{label} {short_money(new, currency)} in FY{year}{change} (Brønnøysund{kind} accounts).", [financial_claim]
    postings = [claim for claim in claims if claim.get("field") == "job_posting" and claim.get("availability") == "available"]
    if postings:
        return (f"Hiring: {len(postings)} open job posting{'s' if len(postings) != 1 else ''} on its verified website, latest "
                f"“{postings[0]['value'].get('title')}” (company careers page).", postings[:3])
    news = [claim for claim in claims if claim.get("field") == "dated_news" and claim.get("availability") == "available"]
    if news:
        newest = max(news, key=lambda claim: claim["value"]["published_at"])
        return f"Latest news {newest['value']['published_at']}: “{newest['value']['title']}” (company website).", [newest]
    employees_claim = _first(claims, "registered_employees")
    if _value(employees_claim):
        return f"{_value(employees_claim)} registered employees (Brønnøysund register).", [employees_claim]
    founded = _first(claims, "founding_date")
    if _value(founded):
        return f"Founded {_value(founded)} (Brønnøysund register).", [founded]
    return None


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
        years = {year, fiscal_year(previous) if previous else None} - {None}
        figures = [claim for claim in claims if claim.get("field") in {"annual_revenue", "annual_result", "equity"}
                   and claim.get("availability") == "available" and str((claim.get("value") or {}).get("fiscal_year")) in years]
        say("why_it_matters", why_it_matters(latest, previous), financial_claim, *figures)
    elif financial_claim:
        say("financials", "No annual accounts were returned by the Accounts Register for this organisation number.", financial_claim)
        say("why_it_matters", "Why it matters: no year-on-year change can be judged, because no filed annual accounts were returned.", financial_claim)

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

    records_all = ((((profile.get("evidence") or {}).get("financials") or {}).get("value") or {}).get("records") or []) \
        if (_first(claims, "latest_annual_accounts") or {}).get("availability") == "available" else []
    newest = newest_filed_record(records_all)
    lead = headline(claims, newest, previous_filed_record(records_all, newest) if newest else None)
    if lead:
        before = len(sentences)
        say("headline", lead[0], *lead[1])
        if len(sentences) > before:
            sentences.insert(0, sentences.pop())  # the buyer-relevant fact first, registry identity after it
    narrative = " ".join(f"{item['text']} [{', '.join(item['evidence_ids'])}]" for item in sentences)
    why = next((item["text"] for item in sentences if item["topic"] == "why_it_matters"), None)
    return {"method": "template_from_claims_v3", "headline": sentences[0]["text"] if sentences and sentences[0]["topic"] == "headline" else None,
            "sentences": sentences, "narrative": narrative, "why_it_matters": why,
            "latest_fiscal_year": fiscal_year(latest) if latest else None}
