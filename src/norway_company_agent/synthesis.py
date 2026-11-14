"""Short, sourced company summary for each envelope (template-based, no LLM).

Sections (sentence topics): headline = the buyer fact first; why_it_matters = one plain line on what the year-on-year
change means, from the two cited filed years only; what_it_does = one plain register sentence plus the verified
site's own description, quoted and attributed; changes = filed-year changes with % and direction, register status, dated
news and job postings; signal = 2–3 labelled observations from sourced numbers; unknown = each missing family with the
reason; financials and profile = full figures and registry details.

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
    if old == new:
        return f"the annual result was unchanged at {money(new, currency)}"
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
                else "the change in revenue did not move the result"
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
                    change = f", {'up' if pct > 0 else 'down'} {abs(pct):,.0f}% from FY{prev_year}" if round(abs(pct)) >= 1 else f", flat on FY{prev_year}"
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




# --- Sectioned summary: buyer fact, what it does, what changed, what is unknown, signals -------------------------

FIGURE_FIELDS = {"revenue": "annual_revenue", "operating_result": "operating_result", "annual_result": "annual_result",
                 "equity": "equity", "assets": "total_assets"}


def _available(claims: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    return [claim for claim in claims if claim.get("field") == field and claim.get("availability") == "available"]


def figure_claim(claims: list[dict[str, Any]], key: str, record: dict[str, Any] | None) -> dict[str, Any] | None:
    """The fact-level claim (own evidence id) for one figure of one filed year and account type."""
    if not record or key not in FIGURE_FIELDS:
        return None
    year = fiscal_year(record)
    for claim in _available(claims, FIGURE_FIELDS[key]):
        value = claim.get("value") or {}
        if str(value.get("fiscal_year")) == year and value.get("account_type") == record.get("account_type"):
            return claim
    return None


def _plain_activity(text: str, limit: int = 180) -> str:
    text = " ".join(str(text).split()).rstrip(".")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def _host(url: str) -> str:
    host = str(url).split("//", 1)[-1].split("/", 1)[0]
    return host[4:] if host.startswith("www.") else host


def change_phrase(key: str, old: float, new: float, currency: str) -> str:
    """Direction and size of a filed-year change, e.g. 'up 12% (NOK 4.1m → NOK 4.6m)'; sign flips are named."""
    fmt = short_money if short_money(old, currency) != short_money(new, currency) else money
    span = f"({fmt(old, currency)} → {fmt(new, currency)})"
    if new == old:
        return f"unchanged at {short_money(new, currency)}"
    if key in {"annual_result", "operating_result"} and old * new < 0:
        return f"turned from {'a loss' if old < 0 else 'a profit'} to {'a profit' if new > 0 else 'a loss'} {span}"
    if key == "equity" and old * new < 0:
        return f"turned {'negative' if new < 0 else 'positive'} {span}"
    if old == 0:
        return f"{'up' if new > old else 'down'} from zero {span}"
    pct = (new - old) / abs(old) * 100
    size = f"{abs(pct):,.0f}%" if round(abs(pct)) >= 1 else "less than 1%"
    return f"{'up' if new > old else 'down'} {size} {span}"


def website_unknown_reason(profile: dict[str, Any], claim: dict[str, Any]) -> str:
    state = claim.get("availability")
    if state == "blocked":
        return "No verified website: the site's robots.txt or policy did not allow reading it."
    if state == "failed":
        return "No verified website: the check could not finish this run (source error or run budget)."
    if state == "ambiguous":
        return ("No verified website: the registry-listed site was read but did not prove it is this company's own "
                "(no matching org number or full legal name), so nothing from it is used.")
    candidates = ((profile.get("website_discovery") or {}).get("candidates")) or []
    read = sum(1 for item in candidates if item.get("result") in {"available", "published", "not_found", "blocked"})
    if profile.get("website"):
        return "No verified website: the registry lists a site, but it did not load or did not prove it is this company's own."
    if read:
        return (f"No verified website: the registry lists none, and none of the {read} candidate domain{'s' if read != 1 else ''} "
                "read (from the name or registry e-mail) printed the org number.")
    if candidates:
        return (f"No verified website: the registry lists none, and the {len(candidates)} candidate domain{'s' if len(candidates) != 1 else ''} "
                "built from the name or registry e-mail do not exist or could not be loaded.")
    return "No verified website: the registry lists none."


def unknown_items(profile: dict[str, Any], claims: list[dict[str, Any]], has_site: bool) -> list[tuple[str, str, dict[str, Any] | None]]:
    """(family, reason sentence, claim) for every family that is missing, each with why it is missing."""
    items: list[tuple[str, str, dict[str, Any] | None]] = []
    failed = {"blocked": "the source's robots.txt or policy did not allow it", "failed": "a source error or the run budget stopped the check"}
    no_site = "there is no verified website to read it from"

    def missing(field: str) -> dict[str, Any] | None:
        claim = _first(claims, field)
        return claim if claim and claim.get("availability") != "available" else None

    claim = missing("official_website")
    if claim:
        items.append(("website", website_unknown_reason(profile, claim), claim))
    for family, field, label, site_reason in (
        ("news", "dated_news", "No dated news", "the verified site was checked (feed, news page, sitemap) and no dated article naming the company was found"),
        ("jobs", "company_jobs", "No job postings", "the verified site shows no careers page or structured job posting"),
        ("profiles", "external_profiles", "No social profiles", "the verified site's header, footer and structured data link no company-owned profile"),
    ):
        claim = missing(field)
        if not claim:
            continue
        state = claim.get("availability")
        reason = failed.get(state) or (site_reason if has_site else no_site if family != "profiles"
                                       else "there is no verified website or exact Wikidata match to take company-owned links from")
        items.append((family, f"{label}: {reason}.", claim))
    jobs = _value(_first(claims, "company_jobs")) or {}
    if jobs and not jobs.get("job_postings"):
        items.append(("jobs", "Open vacancies: the careers page lists no structured job posting, so the number of openings is unknown.",
                      _first(claims, "company_jobs")))
    claim = missing("registered_employees")
    if claim:
        items.append(("employees", f"No employee count: {failed.get(claim.get('availability')) or 'the register holds none for this entity'}.", claim))
    else:
        employees = _first(claims, "registered_employees")
        items.append(("employees", f"Employee change: the register holds one current count ({_value(employees)}) and filed accounts carry "
                                   "no headcount, so no change is stated.", employees))
    claim = missing("roles")
    if claim:
        items.append(("roles", f"No registered roles: {failed.get(claim.get('availability')) or 'the register returned none'}.", claim))
    claim = missing("latest_annual_accounts")
    if claim:
        items.append(("financials", f"No filed accounts: {failed.get(claim.get('availability')) or 'the Accounts Register returned none for this org number'}.", claim))
    status = _first(claims, "registry_status") or _first(claims, "legal_name")
    if status:
        items.append(("registry_history", "Registry changes: this run reads the current register only, so changes to roles, address or "
                                          "status since an earlier date are not known.", status))
    return items


def signal_items(claims: list[dict[str, Any]], latest: dict[str, Any] | None, previous: dict[str, Any] | None,
                 financial_claim: dict[str, Any] | None) -> list[tuple[str, str, list[Any]]]:
    """2–3 labelled observations from sourced numbers only (no forecasts, no adjectives without a number)."""
    found: list[tuple[str, str, list[Any]]] = []
    status_claim = _first(claims, "registry_status")
    status = _value(status_claim) or {}
    if status.get("bankrupt") or status.get("liquidating") or status.get("deleted"):
        label = "Bankrupt" if status.get("bankrupt") else "Under liquidation" if status.get("liquidating") else "Deleted from the register"
        found.append((label, f"{label} according to the live Brønnøysund register.", [status_claim]))
    if latest:
        year, currency = fiscal_year(latest), latest.get("currency") or "NOK"
        cite = lambda *keys: [figure_claim(claims, key, record) or financial_claim for key, record in keys]  # noqa: E731
        equity = latest.get("equity")
        if equity is not None and equity < 0:
            found.append((f"Negative equity in FY{year}", f"Equity was {short_money(equity, currency)} at the end of FY{year}: "
                          "reported liabilities exceeded reported assets.", cite(("equity", latest))))
        res_new = latest.get("annual_result")
        res_old = (previous or {}).get("annual_result")
        prev_year = fiscal_year(previous) if previous else None
        if res_new is not None and res_old is not None:
            pair = cite(("annual_result", previous), ("annual_result", latest))
            amounts = f"{short_money(res_old, currency)} in FY{prev_year}, {short_money(res_new, currency)} in FY{year}"
            if res_new > 0 and res_old > 0:
                found.append(("Profitable two years running", f"Annual result positive in both filed years ({amounts}).", pair))
            elif res_new < 0 and res_old < 0:
                found.append(("Loss-making two years running", f"Annual result negative in both filed years ({amounts}).", pair))
            elif res_new > 0 > res_old:
                found.append((f"Turned profitable in FY{year}", f"Annual result turned positive ({amounts}).", pair))
            elif res_new < 0 < res_old:
                found.append((f"Into loss in FY{year}", f"Annual result turned negative ({amounts}).", pair))
        elif res_new is not None and res_new != 0:
            found.append((f"{'Profitable' if res_new > 0 else 'Loss-making'} in FY{year}",
                          f"Annual result {short_money(res_new, currency)} in FY{year}, the only filed year with a result.", cite(("annual_result", latest))))
        rev_new, rev_old = latest.get("revenue"), (previous or {}).get("revenue")
        if rev_new is not None and rev_old and rev_old > 0:
            pct = (rev_new - rev_old) / rev_old * 100
            if abs(pct) >= 10:
                found.append((f"Revenue {'up' if pct > 0 else 'down'} {abs(pct):,.0f}% in FY{year}",
                              f"Revenue {short_money(rev_old, currency)} in FY{prev_year} → {short_money(rev_new, currency)} in FY{year}.",
                              cite(("revenue", previous), ("revenue", latest))))
        elif rev_new == 0 and (latest.get("operating_result") or latest.get("annual_result")):
            found.append((f"No operating revenue in FY{year}", f"Filed revenue was {short_money(0, currency)} in FY{year} "
                          "while a result was reported.", cite(("revenue", latest))))
        assets = latest.get("assets")
        if len(found) < 2 and equity is not None and equity >= 0 and assets and assets > 0:
            found.append((f"Equity ratio {equity / assets * 100:.0f}% in FY{year}",
                          f"Equity {short_money(equity, currency)} of total assets {short_money(assets, currency)} at the end of FY{year}.",
                          cite(("equity", latest), ("assets", latest))))
    postings = _available(claims, "job_posting")
    if postings:
        found.append((f"Hiring: {len(postings)} open posting{'s' if len(postings) != 1 else ''}",
                      f"{len(postings)} structured job posting{'s' if len(postings) != 1 else ''} on its verified website.", postings[:3]))
    news = _available(claims, "dated_news")
    if news:
        newest = max(news, key=lambda claim: claim["value"]["published_at"])
        found.append((f"{len(news)} dated news item{'s' if len(news) != 1 else ''}",
                      f"Newest on its own website: {newest['value']['published_at']}.", [newest]))
    return found[:3]


def build_synthesis(profile: dict[str, Any], claims: list[dict[str, Any]], evidence_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Topics: headline (the buyer fact), what_it_does, financials, changes, signal, unknown, profile.
    Every sentence carries the evidence ids it rests on; a sentence without evidence is dropped."""
    known_ids = {item["id"] for item in evidence_list}
    sentences: list[dict[str, Any]] = []

    def say(topic: str, text: str, *sources: Any, **extra: Any) -> None:
        ids: list[str] = []
        for source in sources:
            for evidence_id in (source or {}).get("evidence_ids", []) if isinstance(source, dict) else source or []:
                if evidence_id in known_ids and evidence_id not in ids:
                    ids.append(evidence_id)
        if ids:
            sentences.append({"topic": topic, "text": " ".join(text.split()), "evidence_ids": ids, **extra})

    org = str(profile.get("organisation_number") or "")
    name_claim = _first(claims, "legal_name")
    name = _value(name_claim) or profile.get("name") or f"Organisation {org_display(org)}"
    financial_claim = _first(claims, "latest_annual_accounts")
    records = ((((profile.get("evidence") or {}).get("financials") or {}).get("value") or {}).get("records") or []) \
        if (financial_claim or {}).get("availability") == "available" else []
    latest = newest_filed_record(records)
    previous = previous_filed_record(records, latest) if latest else None

    # Buyer fact first.
    lead = headline(claims, latest, previous)
    if lead:
        text, sources = lead
        if latest and text.startswith("Revenue ") and latest.get("annual_result") is not None:
            text = text[:-1].replace(" (Brønnøysund", f"; annual result {short_money(latest['annual_result'], latest.get('currency') or 'NOK')} (Brønnøysund")
            text += "."
        say("headline", text, *sources)

    # What it does: one plain sentence from the register, then the verified site's own words, attributed.
    form_claim, industry_claim, address_claim = _first(claims, "legal_form"), _first(claims, "industry"), _first(claims, "business_address")
    form, industry, address = _value(form_claim), _value(industry_claim), _value(address_claim)
    activity_claim = _first(claims, "registered_activity") if _value(_first(claims, "registered_activity")) else _first(claims, "registered_purpose")
    activity = _value(activity_claim)
    place = str(address).rsplit(" ", 1)[-1].title() if address else None
    what = f"{name} (org. no. {org_display(org)}) is a {LEGAL_FORMS.get(str(form), f'legal form {form}') if form else 'legal entity of unreported form'}"
    if place:
        what += f" in {place}"
    if activity:
        what += f" registered for “{_plain_activity(activity)}”"
    if industry and str(industry.get("code")) not in {"00.000", "", "None"}:
        what += f" (NACE {industry.get('code')} {industry.get('description') or ''})".replace(" )", ")")
    elif industry:
        what += " (no industry code stated in the register)"
    say("what_it_does", what + ".", *[claim for claim in (name_claim, form_claim, address_claim, activity_claim, industry_claim) if _value(claim)])
    website_claim = _first(claims, "official_website")
    website_url = _value(website_claim)
    if website_url:
        site = ((profile.get("evidence") or {}).get("website") or {}).get("value") or {}
        blurb = _plain_activity(site.get("description") or "", 220)
        title = " ".join(str(site.get("title") or "").split())[:160]
        if blurb:
            say("what_it_does", f"Its verified website ({_host(website_url)}) describes it: “{blurb}”.", website_claim)
        elif title:
            say("what_it_does", f"Its verified website ({_host(website_url)}) is titled “{title}”.", website_claim)

    # Latest filed figures (full amounts).
    if latest:
        year, currency = fiscal_year(latest), latest.get("currency") or "NOK"
        kind = "group accounts" if latest.get("account_type") == "KONSERN" else "company accounts"
        figures = [("revenue", "revenue"), ("operating_result", "operating result"), ("annual_result", "annual result"), ("equity", "equity")]
        if all(latest.get(key) is None for key, _ in figures):
            say("financials", f"The newest filed annual accounts are for FY{year} ({kind}), but they report no revenue, result or equity figures.", financial_claim)
        else:
            listed = ", ".join(f"{label} {money(latest.get(key), currency)}" for key, label in figures)
            say("financials", f"Latest filed annual accounts (FY{year}, {kind}): {listed}.", financial_claim,
                *[figure_claim(claims, key, latest) for key, _ in figures])
    elif financial_claim:
        say("financials", "No annual accounts were returned by the Accounts Register for this organisation number.", financial_claim)

    # Why the year-on-year change matters: one plain line from the two cited filed years only (shown after the buyer fact).
    if latest:
        years_cited = [figure_claim(claims, key, record) for record in (latest, previous) if record
                       for key in ("revenue", "annual_result", "equity")]
        say("why_it_matters", why_it_matters(latest, previous), financial_claim, *years_cited)
    elif financial_claim:
        say("why_it_matters", "Why it matters: no year-on-year change can be judged, because no filed annual accounts were returned.",
            financial_claim)

    # What changed: filed-year changes, the register's current status, dated news and job postings.
    if latest and previous:
        year, prev_year, currency = fiscal_year(latest), fiscal_year(previous), latest.get("currency") or "NOK"
        stated = False
        for key, label in (("revenue", "Revenue"), ("annual_result", "Annual result"), ("equity", "Equity")):
            old, new = previous.get(key), latest.get(key)
            if old is None or new is None:
                continue
            stated = True
            say("changes", f"{label} FY{prev_year} → FY{year}: {change_phrase(key, old, new, currency)}.",
                figure_claim(claims, key, previous) or financial_claim, figure_claim(claims, key, latest) or financial_claim)
        if not stated:
            say("unknown", f"No financial trend is stated: FY{prev_year} and FY{year} do not both report revenue, annual result or equity.",
                financial_claim, family="financial_trend")
    elif latest:
        say("unknown", f"Only one filed year (FY{fiscal_year(latest)}) is available, so no financial trend is stated.", financial_claim,
            family="financial_trend")
    status_claim = _first(claims, "registry_status")
    status = _value(status_claim) or {}
    if status.get("deleted"):
        say("changes", f"Register status: deleted{(' on ' + str(status['deleted_date'])) if status.get('deleted_date') else ''} (live register).", status_claim)
    elif status.get("bankrupt"):
        say("changes", "Register status: bankrupt (live register).", status_claim)
    elif status.get("liquidating"):
        say("changes", "Register status: under liquidation (live register).", status_claim)
    elif status:
        say("changes", "Register status: active, with no bankruptcy, liquidation or deletion (live register).", status_claim)
    news = sorted(_available(claims, "dated_news"), key=lambda claim: claim["value"]["published_at"], reverse=True)
    if news:
        shown = "; ".join(f"“{claim['value']['title']}” ({claim['value']['published_at']})" for claim in news[:3])
        more = f" — {len(news)} dated items in all" if len(news) > 3 else ""
        say("changes", f"News on its own website, newest first: {shown}{more}.", *news[:3])
    postings = _available(claims, "job_posting")
    if postings:
        shown = "; ".join(f"“{claim['value'].get('title')}”" + (f" (posted {str(claim['value']['date_posted'])[:10]})" if claim["value"].get("date_posted") else "")
                          for claim in postings[:3])
        say("changes", f"Job postings on its verified website: {shown}.", *postings[:3])

    # Signals.
    for label, text, sources in signal_items(claims, latest, previous, financial_claim):
        say("signal", f"{label}: {text}", *sources, label=label)

    # What is unknown, each with the reason.
    for family, text, claim in unknown_items(profile, claims, bool(website_url)):
        say("unknown", text, claim, family=family)

    # Registry profile details (below the summary block).
    founded, registered = _first(claims, "founding_date"), _first(claims, "registration_date")
    if _value(founded):
        extra = f", registered {_value(registered)}" if _value(registered) else ""
        say("profile", f"Founded {_value(founded)}{extra}.", founded, registered if _value(registered) else None)
    employees_claim = _first(claims, "registered_employees")
    if _value(employees_claim) is not None:
        say("profile", f"The register lists {_value(employees_claim)} employees (as reported to the register, not live headcount).", employees_claim)
    roles_claim = _first(claims, "roles")
    roles = _value(roles_claim) or []
    shown = "; ".join(f"{item.get('role')}: {_name(item.get('name'))}" for item in roles[:3] if _name(item.get("name")))
    if shown:
        say("profile", f"Registered roles include {shown}.", roles_claim)
    subunits = _available(claims, "registered_location")
    if subunits:
        places = sorted({str(claim["value"].get("municipality") or "").title() for claim in subunits if claim["value"].get("municipality")})
        where = f" ({', '.join(places[:4])}{'…' if len(places) > 4 else ''})" if places else ""
        say("profile", f"{len(subunits)} registered sub-unit{'s' if len(subunits) != 1 else ''}{where}.", *subunits[:5])
    platforms = sorted({str(item.get("platform")) for item in _value(_first(claims, "external_profiles")) or [] if item.get("platform")})
    if platforms:
        say("profile", f"Company-owned profiles: {', '.join(platforms)}.", _first(claims, "external_profiles"))

    narrative = " ".join(f"{item['text']} [{', '.join(item['evidence_ids'])}]" for item in sentences)
    why = next((item["text"] for item in sentences if item["topic"] == "why_it_matters"), None)
    return {"method": "template_from_claims_sections", "headline": sentences[0]["text"] if sentences and sentences[0]["topic"] == "headline" else None,
            "sentences": sentences, "narrative": narrative, "why_it_matters": why,
            "signals": [{"label": item["label"], "text": item["text"], "evidence_ids": item["evidence_ids"]}
                        for item in sentences if item["topic"] == "signal"],
            "latest_fiscal_year": fiscal_year(latest) if latest else None}
