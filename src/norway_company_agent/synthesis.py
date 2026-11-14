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


def why_it_matters(latest: dict[str, Any], previous: dict[str, Any] | None) -> str:
    """One sentence on what the year-on-year change means, from the two filed years only (no forecast). It names no
    amount: the figures themselves appear once, in the buyer fact and the Latest financials section."""
    year = fiscal_year(latest)
    if not previous:
        return f"Why it matters: no year-on-year change can be judged, because FY{year} is the only filed year."
    prev = fiscal_year(previous)
    rev_old, rev_new = previous.get("revenue"), latest.get("revenue")
    res_old, res_new = previous.get("annual_result"), latest.get("annual_result")
    equity = latest.get("equity")
    parts: list[str] = []
    if res_old is not None and res_new is not None and res_old * res_new < 0:  # profit <-> loss (zero is break-even, not a flip)
        parts.append(f"the company went from {'a profit' if res_old > 0 else 'a loss'} in FY{prev} to {'a loss' if res_new < 0 else 'a profit'} "
                     f"in FY{year}, so FY{year} was {'loss-making after a profitable year' if res_new < 0 else 'profitable after a loss-making year'}")
    elif rev_old is not None and rev_new is not None and res_old is not None and res_new is not None and rev_old != rev_new:
        sales = f"revenue {'rose' if rev_new > rev_old else 'fell'}"
        if res_old == res_new:
            result, better = "the annual result was unchanged", None
        elif res_old == 0 or res_new == 0:
            result, better = f"the annual result {'broke even' if res_new == 0 else 'moved from break-even'}", res_new > res_old
        elif res_old < 0 and res_new < 0:
            result, better = f"the loss {'narrowed' if res_new > res_old else 'widened'}", res_new > res_old
        else:
            result, better = f"the annual result {'rose' if res_new > res_old else 'fell'}", res_new > res_old
        if better is None:
            meaning = "the change in revenue did not move the result"
        elif (rev_new > rev_old) == better:
            meaning = "both revenue and the result grew" if better else "both revenue and the result shrank"
        else:
            meaning = f"it {'lost less' if res_new < 0 else 'earned more'} on lower revenue" if better else "higher revenue did not lift the result"
        parts.append(f"{sales} {'and' if better is None or (rev_new > rev_old) == better else 'while'} {result}: {meaning}")
    elif rev_old is not None and rev_new is not None and rev_old != rev_new:
        pct = _pct(rev_old, rev_new)
        if pct is not None and pct >= 100:
            parts.append("revenue more than doubled, a step change in the size of the business")
        elif pct is not None and pct <= -50:
            parts.append("revenue more than halved, a sharp contraction of the business")
        else:
            parts.append(f"revenue {'rose' if rev_new > rev_old else 'fell'}; the annual result is not reported for both years, "
                         "so the effect on profit is unknown")
    elif res_old is not None and res_new is not None and res_old != res_new:
        if res_old == 0 or res_new == 0:
            moved = f"the annual result {'broke even' if res_new == 0 else 'moved from break-even'}"
        elif res_old < 0 and res_new < 0:
            moved = f"the loss {'narrowed' if res_new > res_old else 'widened'}"
        else:
            moved = f"the annual result {'rose' if res_new > res_old else 'fell'}"
        parts.append(f"{moved}; revenue is not reported for both years")
    if equity is not None and equity < 0:
        parts.append(f"equity was negative at the end of FY{year}, meaning reported liabilities exceeded reported assets")
    if not parts:
        return (f"Why it matters: no year-on-year change can be judged, because FY{prev} and FY{year} do not both report "
                "revenue or annual result, or they are unchanged.")
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




# --- Sectioned summary ---------------------------------------------------------------------------------------------
# Every sentence has a `section` (the five company-page sections) and a `topic`. Each figure appears once: the buyer
# fact names the lead figure, Latest financials the other figures of the newest filed year (with operating margin and
# equity ratio), What changed only the earlier year's figures and the direction; why_it_matters and the signals name
# no amount.

SECTIONS = ("brief", "financials", "people", "work", "activity")
SECTION_TITLES = {"brief": "Company brief", "financials": "Latest financials", "people": "Who runs it", "work": "Working here",
                  "activity": "Recent activity"}
FAMILY_SECTION = {"website": "brief", "profiles": "brief", "financials": "financials", "financial_trend": "financials",
                  "roles": "people", "jobs": "work", "employees": "work", "news": "activity", "registry_history": "activity"}
FIGURE_FIELDS = {"revenue": "annual_revenue", "operating_result": "operating_result", "annual_result": "annual_result",
                 "equity": "equity", "assets": "total_assets"}
FIGURE_LABELS = {"revenue": "revenue", "operating_result": "operating result", "annual_result": "annual result", "equity": "equity",
                 "assets": "total assets"}
# Registry change-feed fields worth a line in "What changed" (the claims list every event).
EVENT_FIELDS_SHOWN = {"navn", "forretningsadresse", "postadresse", "beliggenhetsadresse", "naeringskode1", "organisasjonsform",
                      "antallAnsatte", "kapital", "konkurs", "underAvvikling", "underTvangsavviklingEllerTvangsopplosning",
                      "hjemmeside", "sisteInnsendteAarsregnskap", "vedtektsfestetFormaal", "aktivitet", "registrertIMvaregisteret",
                      "registrertIForetaksregisteret", "overordnetEnhet", "slettedato", "epostadresse", "telefon"}
MAX_EVENT_LINES = 5


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
    """Direction and size of a filed-year change, with the earlier year's amount only (the newer amount is shown once,
    in Latest financials): 'up 12% on FY2024 (NOK 4.1m)'. Sign flips are named."""
    if new == old:
        return "unchanged"
    before = short_money(old, currency)
    if short_money(old, currency) == short_money(new, currency):
        before = money(old, currency)
    if key in {"annual_result", "operating_result"} and old * new < 0:
        return f"turned from {'a loss' if old < 0 else 'a profit'} to {'a profit' if new > 0 else 'a loss'} (was {before})"
    if key == "equity" and old * new < 0:
        return f"turned {'negative' if new < 0 else 'positive'} (was {before})"
    if old == 0:
        return f"{'up' if new > old else 'down'} from zero"
    pct = (new - old) / abs(old) * 100
    size = f"{abs(pct):,.0f}%" if round(abs(pct)) >= 1 else "less than 1%"
    return f"{'up' if new > old else 'down'} {size} (was {before})"


def website_unknown_reason(profile: dict[str, Any], claim: dict[str, Any]) -> str:
    state = claim.get("availability")
    if state == "blocked":
        return "Website: no verified site — its robots.txt or policy did not allow reading it."
    if state == "failed":
        return "Website: no verified site — the check could not finish this run (source error or run budget)."
    ambiguous = profile.get("registry_website_ambiguous")
    if ambiguous:
        return f"Website: no verified site — the registry-listed site could not be read and {ambiguous.get('reason')}, so it is not attributed."
    if profile.get("registry_website_unresolved"):
        return "Website: no verified site — the registry-listed domain does not resolve in DNS (it may have lapsed), so it is not published."
    if state == "ambiguous" or profile.get("website"):
        return "Website: no verified site — the registry-listed site did not prove it is this company's own (no matching org number)."
    candidates = ((profile.get("website_discovery") or {}).get("candidates")) or []
    read = sum(1 for item in candidates if item.get("result") in {"available", "published", "not_found", "blocked"})
    if read:
        return f"Website: no verified site — the registry lists none, and none of {read} candidate domain{'s' if read != 1 else ''} read printed the org number."
    if candidates:
        return f"Website: no verified site — the registry lists none, and the {len(candidates)} candidate domain{'s' if len(candidates) != 1 else ''} did not load."
    return "Website: no verified site — the registry lists none."


NONE_WORD = {"news": "no dated item", "jobs": "no posting", "profiles": "no company-owned profile"}


def unknown_items(profile: dict[str, Any], claims: list[dict[str, Any]], verified_site: bool) -> list[tuple[str, str, dict[str, Any] | None]]:
    """(family, one short line 'Label: reason.', claim) for every missing family: one line per family."""
    items: list[tuple[str, str, dict[str, Any] | None]] = []
    failed = {"blocked": "the source's robots.txt or policy did not allow it", "failed": "a source error or the run budget stopped the check"}
    no_site = "no verified website to read it from"

    def missing(field: str) -> dict[str, Any] | None:
        claim = _first(claims, field)
        return claim if claim and claim.get("availability") != "available" else None

    claim = missing("official_website")
    if claim:
        items.append(("website", website_unknown_reason(profile, claim), claim))
    for family, field, label, site_reason in (
        ("news", "dated_news", "News", "the verified site has no dated article naming the company (feed, news page, sitemap checked)"),
        ("jobs", "company_jobs", "Jobs", "the verified site shows no careers page or job posting"),
        ("profiles", "external_profiles", "Social profiles", "the verified site links no company-owned profile"),
    ):
        claim = missing(field)
        if claim:
            reason = failed.get(claim.get("availability")) or (site_reason if verified_site else
                                                               no_site if family != "profiles" else "no verified website or exact Wikidata match")
            items.append((family, f"{label}: {NONE_WORD[family]} — {reason}.", claim))
    if not missing("company_jobs") and not _available(claims, "job_posting") and _first(claims, "company_jobs"):
        items.append(("jobs", "Jobs: careers page found, but it lists no structured posting, so openings are unknown.", _first(claims, "company_jobs")))
    claim = missing("registered_employees")
    if claim:
        items.append(("employees", f"Employees: {failed.get(claim.get('availability')) or 'not registered'}.", claim))
    claim = missing("roles")
    if claim:
        items.append(("roles", f"Roles: {failed.get(claim.get('availability')) or 'not registered'}.", claim))
    claim = missing("latest_annual_accounts")
    if claim:
        items.append(("financials", f"Accounts: {failed.get(claim.get('availability')) or 'not filed in the Accounts Register'}.", claim))
    claim = missing("registry_events")
    if claim:
        skipped = "skipped to protect the run deadline" in str(claim.get("note") or "")
        items.append(("registry_history", "Register changes: not checked this run (skipped to protect the run deadline)." if skipped else
                      f"Register changes: {failed.get(claim.get('availability')) or 'the change feed lists no stated change'}.", claim))
    return items


def _event_value(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        if "kode" in value:
            return f"{value.get('kode')} {value.get('beskrivelse') or ''}".strip()
        if "adresse" in value or "poststed" in value:
            street = ", ".join(value.get("adresse") or []) if isinstance(value.get("adresse"), list) else str(value.get("adresse") or "")
            return ", ".join(part for part in (street, " ".join(str(v) for v in (value.get("postnummer"), value.get("poststed")) if v)) if part)
        return ", ".join(f"{key} {item}" for key, item in value.items() if not isinstance(item, (dict, list)))[:80]
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)[:80]
    return str(value)


def event_line(event: dict[str, Any]) -> str:
    """'2026-07-06: share capital 37618 → 38364' when the feed states both values; otherwise '…: e-mail updated' (the
    value is in the claim, and a current value such as the employee count is shown once, in its own section)."""
    value = event.get("value") or {}
    where = "" if value.get("entity") in {"main unit", "roles"} else f" (sub-unit {value.get('organisation_number')})"
    label = value.get("field_label") or "record"
    if value.get("type") == "registered":
        return f"{value.get('date')}: registered in the Central Coordinating Register{where}"
    if value.get("field") == "sisteInnsendteAarsregnskap" and value.get("new"):
        return f"{value.get('date')}: annual accounts for {value.get('new')} filed{where}"
    if value.get("old_stated") and value.get("new") is not None and not value.get("value_note"):
        return f"{value.get('date')}: {label} {_event_value(value.get('old'))} → {_event_value(value.get('new'))}{where}"
    return f"{value.get('date')}: {label} {'updated' if value.get('type') == 'changed' else value.get('type') or 'updated'}{where}"


def signal_items(claims: list[dict[str, Any]], latest: dict[str, Any] | None, previous: dict[str, Any] | None,
                 financial_claim: dict[str, Any] | None) -> list[tuple[str, str, list[Any]]]:
    """Up to 3 labelled observations from sourced facts; they name no amount (figures appear once elsewhere)."""
    found: list[tuple[str, str, list[Any]]] = []
    status_claim = _first(claims, "registry_status")
    status = _value(status_claim) or {}
    if status.get("bankrupt") or status.get("liquidating") or status.get("deleted"):
        label = "Bankrupt" if status.get("bankrupt") else "Under liquidation" if status.get("liquidating") else "Deleted from the register"
        found.append((label, f"{label} according to the live Brønnøysund register.", [status_claim]))
    if latest:
        year = fiscal_year(latest)
        cite = lambda *keys: [figure_claim(claims, key, record) or financial_claim for key, record in keys]  # noqa: E731
        equity = latest.get("equity")
        if equity is not None and equity < 0:
            found.append((f"Negative equity in FY{year}", f"Reported liabilities exceeded reported assets at the end of FY{year}.",
                          cite(("equity", latest))))
        res_new, res_old = latest.get("annual_result"), (previous or {}).get("annual_result")
        prev_year = fiscal_year(previous) if previous else None
        if res_new is not None and res_old is not None:
            pair = cite(("annual_result", previous), ("annual_result", latest))
            if res_new > 0 and res_old > 0:
                found.append(("Profitable two years running", f"Annual result positive in FY{prev_year} and FY{year}.", pair))
            elif res_new < 0 and res_old < 0:
                found.append(("Loss-making two years running", f"Annual result negative in FY{prev_year} and FY{year}.", pair))
            elif res_new > 0 > res_old:
                found.append((f"Turned profitable in FY{year}", f"Annual result negative in FY{prev_year}, positive in FY{year}.", pair))
            elif res_new < 0 < res_old:
                found.append((f"Into loss in FY{year}", f"Annual result positive in FY{prev_year}, negative in FY{year}.", pair))
        if latest.get("revenue") == 0 and (latest.get("operating_result") or latest.get("annual_result")):
            found.append((f"No operating revenue in FY{year}", f"No revenue filed for FY{year} while a result was reported.",
                          cite(("revenue", latest))))
    postings = _available(claims, "job_posting")
    if postings:
        found.append((f"Hiring: {len(postings)} open posting{'s' if len(postings) != 1 else ''}",
                      f"{len(postings)} structured job posting{'s' if len(postings) != 1 else ''} on its verified website.", postings[:3]))
    capital = [claim for claim in _available(claims, "registry_event") if str((claim.get("value") or {}).get("field") or "") == "kapital.belop"]
    if len(capital) >= 2:
        found.append((f"Share capital changed {len(capital)} times", f"Between {capital[-1]['value']['date']} and {capital[0]['value']['date']} "
                      "(Brønnøysund change feed).", [capital[0], capital[-1]]))
    news = _available(claims, "dated_news")
    if news:
        newest = max(news, key=lambda claim: claim["value"]["published_at"])
        found.append((f"{len(news)} dated news item{'s' if len(news) != 1 else ''}", f"Newest on its own website: {newest['value']['published_at']}.", [newest]))
    return found[:3]


def build_synthesis(profile: dict[str, Any], claims: list[dict[str, Any]], evidence_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Sentences grouped in five sections (brief, financials, people, work, activity); each carries the evidence ids it
    rests on, and a sentence without evidence is dropped."""
    known_ids = {item["id"] for item in evidence_list}
    sentences: list[dict[str, Any]] = []

    def say(section: str, topic: str, text: str, *sources: Any, **extra: Any) -> None:
        ids: list[str] = []
        for source in sources:
            for evidence_id in (source or {}).get("evidence_ids", []) if isinstance(source, dict) else source or []:
                if evidence_id in known_ids and evidence_id not in ids:
                    ids.append(evidence_id)
        if ids:
            sentences.append({"section": section, "topic": topic, "text": " ".join(text.split()), "evidence_ids": ids, **extra})

    org = str(profile.get("organisation_number") or "")
    name_claim = _first(claims, "legal_name")
    name = _value(name_claim) or profile.get("name") or f"Organisation {org_display(org)}"
    financial_claim = _first(claims, "latest_annual_accounts")
    records = ((((profile.get("evidence") or {}).get("financials") or {}).get("value") or {}).get("records") or []) \
        if (financial_claim or {}).get("availability") == "available" else []
    latest = newest_filed_record(records)
    previous = previous_filed_record(records, latest) if latest else None
    used: set[tuple[str, str]] = set()  # (figure, fiscal year) already named

    # Company brief: the buyer fact first, then why it matters, what it does, the signals.
    lead = headline(claims, latest, previous)
    if lead:
        text, sources = lead
        if latest and text.startswith(("Revenue ", "Operating result ", "Annual result ")):
            key = {"Revenue": "revenue", "Operating": "operating_result", "Annual": "annual_result"}[text.split(" ")[0]]
            used.add((key, fiscal_year(latest)))
            if previous and f"FY{fiscal_year(previous)}" in text:
                used.add((key, fiscal_year(previous)))
            sources = [figure_claim(claims, key, latest) or financial_claim,
                       *([figure_claim(claims, key, previous)] if previous and (key, fiscal_year(previous)) in used else [])]
        say("brief", "headline", text, *sources)
    form_claim, industry_claim, address_claim = _first(claims, "legal_form"), _first(claims, "industry"), _first(claims, "business_address")
    form, industry, address = _value(form_claim), _value(industry_claim), _value(address_claim)
    status_claim = _first(claims, "registry_status")
    status = _value(status_claim) or {}
    activity_claim = _first(claims, "registered_activity") if _value(_first(claims, "registered_activity")) else _first(claims, "registered_purpose")
    activity = _value(activity_claim)
    place = str(address).rsplit(" ", 1)[-1].title() if address else None
    active = status and not (status.get("bankrupt") or status.get("liquidating") or status.get("deleted"))
    kind = LEGAL_FORMS.get(str(form), f"legal form {form}") if form else "legal entity of unreported form"
    what = f"{name} (org. no. {org_display(org)}) is {'an active' if active else 'a'} {kind}"
    if place:
        what += f" in {place}"
    if activity:
        what += f" registered for “{_plain_activity(activity)}”"
    if industry and str(industry.get("code")) not in {"00.000", "", "None"}:
        what += f" (NACE {industry.get('code')} {industry.get('description') or ''})".replace(" )", ")")
    elif industry:
        what += " (no industry code stated in the register)"
    say("brief", "what_it_does", what + ".", *[claim for claim in (name_claim, form_claim, address_claim, activity_claim, industry_claim,
                                                                    status_claim if active else None) if _value(claim)])
    website_claim = _first(claims, "official_website")
    website_url = _value(website_claim)
    verified_site = bool(website_url) and (website_claim or {}).get("website_source") != "registry_record"
    if verified_site:
        site = ((profile.get("evidence") or {}).get("website") or {}).get("value") or {}
        blurb = _plain_activity(site.get("description") or "", 220)
        title = " ".join(str(site.get("title") or "").split())[:160]
        if blurb:
            say("brief", "what_it_does", f"Its verified website ({_host(website_url)}) describes it: “{blurb}”.", website_claim)
        elif title:
            say("brief", "what_it_does", f"Its verified website ({_host(website_url)}) is titled “{title}”.", website_claim)
    elif website_url:
        say("brief", "what_it_does", f"Website in its Brønnøysund record: {_host(website_url)} (the site could not be read at crawl time, "
                                     "so nothing from it is used).", website_claim)
    related = _available(claims, "related_website")
    if related:
        shown = "; ".join(f"{_host(claim['value']['url'])} ({claim['value'].get('subunit_name') or 'sub-unit'})" for claim in related[:3])
        say("brief", "what_it_does", f"Sub-unit website{'s' if len(related) != 1 else ''} in the register (the sub-unit's own, not "
                                     f"proven as the company's): {shown}.", *related[:3])
    if latest:
        years_cited = [figure_claim(claims, key, record) for record in (latest, previous) if record for key in ("revenue", "annual_result", "equity")]
        say("brief", "why_it_matters", why_it_matters(latest, previous), financial_claim, *years_cited)
    elif financial_claim:
        say("brief", "why_it_matters", "Why it matters: no year-on-year change can be judged, because no filed annual accounts were returned.",
            financial_claim)
    for label, text, sources in signal_items(claims, latest, previous, financial_claim):
        say("brief", "signal", f"{label}: {text}", *sources, label=label)
    platforms = sorted({str(item.get("platform")) for item in _value(_first(claims, "social_profiles")) or [] if item.get("platform")})
    if platforms:
        say("brief", "profile", f"Company-owned social profiles: {', '.join(platforms)}.", _first(claims, "social_profiles"))

    # Latest financials: the newest filed year's figures not already named, with operating margin and equity ratio.
    if latest:
        year, currency = fiscal_year(latest), latest.get("currency") or "NOK"
        kind = "group accounts" if latest.get("account_type") == "KONSERN" else "company accounts"
        parts, cites = [], [financial_claim]
        for key in ("revenue", "operating_result", "annual_result", "equity", "assets"):
            amount = latest.get(key)
            if (key, year) in used or (amount is None and key in {"operating_result", "assets"}):
                continue
            text = f"{FIGURE_LABELS[key]} {money(amount, currency) if amount is not None else 'not reported'}"
            if key == "operating_result" and amount is not None and latest.get("revenue"):
                margin = amount / latest["revenue"] * 100
                text += f" (operating margin {margin:.1f}%)" if abs(margin) >= 0.1 else " (operating margin under 0.1%)"
            if key == "assets" and amount and latest.get("equity") is not None:
                text += f" (equity ratio {latest['equity'] / amount * 100:.0f}%)"
            parts.append(text)
            used.add((key, year))
            cites.append(figure_claim(claims, key, latest))
        if parts:
            say("financials", "financials", f"FY{year} ({kind}): {', '.join(parts)}.", *cites)
        elif all(latest.get(key) is None for key in FIGURE_LABELS):
            say("financials", "financials", f"FY{year} ({kind}) is filed but reports no revenue, result or equity.", financial_claim)
    elif financial_claim:
        say("financials", "financials", "No annual accounts were returned by the Accounts Register for this organisation number.", financial_claim)

    # What changed: the earlier filed year (direction + the earlier amount only), then the register's change feed.
    if latest and previous:
        year, prev_year, currency = fiscal_year(latest), fiscal_year(previous), latest.get("currency") or "NOK"
        stated = False
        for key in ("revenue", "operating_result", "annual_result", "equity"):
            old, new = previous.get(key), latest.get(key)
            if old is None or new is None:
                continue
            stated = True
            if (key, prev_year) in used:
                continue
            used.add((key, prev_year))
            say("activity", "changes", f"{FIGURE_LABELS[key].capitalize()} FY{prev_year} → FY{year}: {change_phrase(key, old, new, currency)}.",
                figure_claim(claims, key, previous) or financial_claim, figure_claim(claims, key, latest) or financial_claim)
        if not stated:
            say("financials", "unknown", f"Trend: FY{prev_year} and FY{year} do not both report revenue, result or equity.", financial_claim,
                family="financial_trend")
    elif latest:
        say("financials", "unknown", f"Trend: FY{fiscal_year(latest)} is the only filed year, so no trend is stated.", financial_claim,
            family="financial_trend")
    if status.get("deleted"):
        say("activity", "changes", f"Register status: deleted{(' on ' + str(status['deleted_date'])) if status.get('deleted_date') else ''} (live register).", status_claim)
    elif status.get("bankrupt") or status.get("liquidating"):
        say("activity", "changes", f"Register status: {'bankrupt' if status.get('bankrupt') else 'under liquidation'} (live register).", status_claim)
    events = _available(claims, "registry_event")
    shown_events = [claim for claim in events if str((claim.get("value") or {}).get("field") or "").split(".")[0] in EVENT_FIELDS_SHOWN
                    or (claim.get("value") or {}).get("type") in {"registered", "deleted"}]
    seen_lines: set[str] = set()
    for claim in shown_events:
        line = event_line(claim)
        key = line.split(" (sub-unit ")[0]  # the same change on the main unit and its sub-unit is one line
        if key in seen_lines:
            continue
        seen_lines.add(key)
        say("activity", "changes", f"{line} (Brønnøysund change feed).", claim)
        if len(seen_lines) >= MAX_EVENT_LINES:
            break
    roles_events = [claim for claim in events if (claim.get("value") or {}).get("entity") == "roles"]
    other = [claim for claim in events if claim not in shown_events and claim not in roles_events]
    if roles_events:
        say("people", "changes", f"Role register updated {len(roles_events)} time{'s' if len(roles_events) != 1 else ''}, latest "
                                 f"{roles_events[0]['value']['date']} (Brønnøysund change feed; it gives dates, not names).", roles_events[0])
    if other:
        say("activity", "changes", f"{len(other)} other register update{'s' if len(other) != 1 else ''} (bookkeeping fields or no field "
                                   f"details), latest {other[0]['value']['date']}.", other[0])
    news = sorted(_available(claims, "dated_news"), key=lambda claim: claim["value"]["published_at"], reverse=True)
    if news:
        shown = "; ".join(f"“{claim['value']['title']}” ({claim['value']['published_at']})" for claim in news[:3])
        more = f" — {len(news)} dated items in all" if len(news) > 3 else ""
        say("activity", "news", f"News on its own website, newest first: {shown}{more}.", *news[:3])

    # Who runs it: roles, group and parent, sub-units.
    roles_claim = _first(claims, "roles")
    roles = _value(roles_claim) or []
    order = ("daglig leder", "styrets leder", "styreleder", "nestleder", "innehaver", "deltaker", "kontaktperson", "styremedlem")
    ranked = sorted(roles, key=lambda item: next((i for i, word in enumerate(order) if word in str(item.get("role") or "").casefold()), len(order)))
    shown = "; ".join(f"{item.get('role')}: {_name(item.get('name'))}" for item in ranked[:4] if _name(item.get("name")))
    if shown:
        more = f" ({len(roles)} registered roles in all)" if len(roles) > 4 else ""
        say("people", "people", f"{shown}{more}.", roles_claim)
    parent = _available(claims, "parent_entity")
    group = _value(_first(claims, "group_structure")) or []
    if group:
        say("people", "people", f"Part of a corporate group with {len(group)} other registered entit{'y' if len(group) == 1 else 'ies'}.", _first(claims, "group_structure"))
    elif parent:
        say("people", "people", f"Registered parent unit: org. no. {org_display(str(parent[0]['value'].get('organisation_number')))}.", parent[0])
    subunits = _available(claims, "registered_location")
    if subunits:
        places = sorted({str(claim["value"].get("municipality") or "").title() for claim in subunits if claim["value"].get("municipality")})
        where = f" ({', '.join(places[:4])}{'…' if len(places) > 4 else ''})" if places else ""
        say("people", "people", f"{len(subunits)} registered sub-unit{'s' if len(subunits) != 1 else ''}{where}.", *subunits[:5])
    founded, registered = _first(claims, "founding_date"), _first(claims, "registration_date")
    if _value(founded):
        extra = f", registered {_value(registered)}" if _value(registered) else ""
        say("people", "profile", f"Founded {_value(founded)}{extra}.", founded, registered if _value(registered) else None)

    # Working here: headcount, postings, careers page.
    employees_claim = _first(claims, "registered_employees")
    if _value(employees_claim) is not None:
        say("work", "work", f"The register lists {_value(employees_claim)} employees (as reported to the register, not live headcount).", employees_claim)
    postings = _available(claims, "job_posting")
    if postings:
        shown = "; ".join(f"“{claim['value'].get('title')}”" + (f" (posted {str(claim['value']['date_posted'])[:10]})" if claim["value"].get("date_posted") else "")
                          for claim in postings[:3])
        say("work", "work", f"Job postings on its verified website: {shown}.", *postings[:3])
    careers = _available(claims, "careers_page")
    if careers and not postings:
        say("work", "work", f"Careers page on its verified website: {careers[0]['value'].get('title') or careers[0]['value'].get('url')}.", careers[0])

    # One short line per missing family, in its section.
    for family, text, claim in unknown_items(profile, claims, verified_site):
        say(FAMILY_SECTION.get(family, "brief"), "unknown", text, claim, family=family)

    order_index = {section: index for index, section in enumerate(SECTIONS)}
    sentences.sort(key=lambda item: order_index.get(item["section"], len(SECTIONS)))  # stable: keeps order within a section
    narrative = " ".join(f"{item['text']} [{', '.join(item['evidence_ids'])}]" for item in sentences)
    why = next((item["text"] for item in sentences if item["topic"] == "why_it_matters"), None)
    return {"method": "template_from_claims_five_sections", "sections": [{"id": section, "title": SECTION_TITLES[section]} for section in SECTIONS],
            "headline": sentences[0]["text"] if sentences and sentences[0]["topic"] == "headline" else None,
            "sentences": sentences, "narrative": narrative, "why_it_matters": why,
            "signals": [{"label": item["label"], "text": item["text"], "evidence_ids": item["evidence_ids"]}
                        for item in sentences if item["topic"] == "signal"],
            "latest_fiscal_year": fiscal_year(latest) if latest else None}
