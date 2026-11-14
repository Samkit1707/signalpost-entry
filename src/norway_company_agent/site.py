"""Static, offline site written by the official run from the same envelopes (no server, no external assets).

out/site/
  index.html              search by name or organisation number, filters, pick 2–3 companies to compare
  compare.html            side-by-side view (?orgs=a,b,c); every cell links its source and date
  companies/<org>.html    one page per company: sourced summary, facts, financial years, dated news, evidence
  data/<org>.js           compare data per company (script files, so the site also works from file://)
  assets/                 style.css, app.js, companies.js
  site-manifest.json      counts and file list for audit

Every fact shown carries a link to its source and a date (the date the fact refers to when the source states
one, and the retrieval date). Missing values are shown as missing, never as 0.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

from .synthesis import fiscal_year, money, newest_filed_record, org_display, previous_filed_record

AVAILABILITY_TEXT = {
    "not_available": "Not found in checked sources",
    "ambiguous": "Found, identity not verified — not used",
    "blocked": "Blocked (robots.txt or policy)",
    "failed": "Not checked (source error or budget)",
    "not_applicable": "Not applicable",
}


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def safe_url(value: Any) -> str | None:
    url = str(value or "").strip()
    parsed = urllib.parse.urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _day(value: Any) -> str | None:
    match = re.match(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else None


def _claims(envelope: dict[str, Any], field: str) -> list[dict[str, Any]]:
    return [claim for claim in envelope.get("claims") or [] if claim.get("field") == field]


def _claim(envelope: dict[str, Any], field: str) -> dict[str, Any] | None:
    found = _claims(envelope, field)
    return found[0] if found else None


def _evidence_map(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in envelope.get("evidence") or []}


def _source(envelope: dict[str, Any], claim: dict[str, Any] | None, evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ids = (claim or {}).get("evidence_ids") or []
    item = evidence.get(ids[0]) if ids else None
    item = item or {}
    return {"ev": ids[0] if ids else None, "src": safe_url(item.get("source_url")),
            "retrieved": _day(item.get("retrieved_at")), "as_of": _day((claim or {}).get("as_of"))}


def _fact(envelope: dict[str, Any], evidence: dict[str, dict[str, Any]], key: str, label: str,
          claim: dict[str, Any] | None, text: str | None, *, link: str | None = None,
          as_of: str | None = None, links: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    source = _source(envelope, claim, evidence)
    if as_of:
        source["as_of"] = as_of
    availability = (claim or {}).get("availability") or "not_available"
    missing = not text
    if missing:
        text = AVAILABILITY_TEXT.get("not_available" if availability == "available" else availability, "Not available")
    fact = {"key": key, "label": label, "text": text, "missing": missing, "link": safe_url(link), **source}
    if links and not missing:
        fact["links"] = [{**item, "url": safe_url(item.get("url"))} for item in links if safe_url(item.get("url"))]
    return fact


def _org_name(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(part) for part in value if part)
    return str(value or "")


FACT_FAMILY = {"website": "website", "news": "news", "jobs": "jobs", "profiles": "profiles", "employees": "employees",
               "roles": "roles", "fiscal_year": "financials", "revenue": "financials", "operating_result": "financials",
               "annual_result": "financials", "equity": "financials", "previous_revenue": "financial_trend"}


def unknown_reasons(envelope: dict[str, Any]) -> dict[str, str]:
    """family -> why it is missing, from the synthesis 'unknown' sentences (text after the label)."""
    reasons: dict[str, str] = {}
    for item in (envelope.get("synthesis") or {}).get("sentences") or []:
        if item.get("topic") == "unknown" and item.get("family"):
            text = item["text"].split(": ", 1)[-1]
            reasons.setdefault(item["family"], text[0].upper() + text[1:])
    return reasons


def company_facts(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """The fixed fact rows shown on company pages and in the compare view, each with source and dates.
    A missing fact says "Not available" and why (the reason the summary gives for that family)."""
    evidence = _evidence_map(envelope)
    facts = []
    reasons = unknown_reasons(envelope)

    def value(field: str) -> Any:
        claim = _claim(envelope, field)
        return claim.get("value") if claim and claim.get("availability") == "available" else None

    def add(key: str, label: str, field: str, text: str | None, **kwargs: Any) -> None:
        facts.append(_fact(envelope, evidence, key, label, _claim(envelope, field), text, **kwargs))

    add("legal_name", "Legal name", "legal_name", value("legal_name"))
    add("organisation_number", "Organisation number", "legal_name", org_display(envelope["organisation_number"]))
    add("legal_form", "Legal form", "legal_form", value("legal_form"))
    industry = value("industry")
    add("industry", "Main industry", "industry", f"{industry.get('code')} {industry.get('description') or ''}".strip() if industry else None)
    add("business_address", "Business address", "business_address", " ".join(str(value("business_address") or "").split()) or None)
    status = value("registry_status")
    status_text = None
    if status:
        status_text = ("Deleted" + (f" ({status.get('deleted_date')})" if status.get("deleted_date") else "")) if status.get("deleted") else \
            "Bankrupt" if status.get("bankrupt") else "Under liquidation" if status.get("liquidating") else "Active (no bankruptcy, liquidation or deletion)"
    add("registry_status", "Registry status", "registry_status", status_text)
    employees = value("registered_employees")
    add("employees", "Registered employees", "registered_employees", None if employees is None else str(employees))
    roles = value("roles") or []
    leader = next((item for item in roles if "leder" in str(item.get("role") or "").casefold()), roles[0] if roles else None)
    add("roles", "Key role", "roles", f"{leader.get('role')}: {_org_name(leader.get('name'))}" if leader else None)

    financial_claim = _claim(envelope, "latest_annual_accounts")
    records = ((((envelope.get("profile") or {}).get("evidence") or {}).get("financials") or {}).get("value") or {}).get("records") or [] \
        if (financial_claim or {}).get("availability") == "available" else []
    latest = newest_filed_record(records)
    previous = previous_filed_record(records, latest) if latest else None
    period_end = ((latest or {}).get("period") or {}).get("tilDato")
    currency = (latest or {}).get("currency") or "NOK"
    add("fiscal_year", "Latest filed year", "latest_annual_accounts",
        f"FY{fiscal_year(latest)} ({'group' if latest.get('account_type') == 'KONSERN' else 'company'} accounts)" if latest else None,
        as_of=period_end)
    for key, label in (("revenue", "Revenue"), ("operating_result", "Operating result"), ("annual_result", "Annual result"), ("equity", "Equity")):
        if latest:
            # A null figure in filed accounts is "not reported", never 0.
            add(key, f"{label} (FY{fiscal_year(latest)})", "latest_annual_accounts",
                money(latest.get(key), currency) if latest.get(key) is not None else "Not reported in filed accounts", as_of=period_end)
            facts[-1]["missing"] = latest.get(key) is None
        else:
            add(key, label, "latest_annual_accounts", None)
    if previous and previous.get("revenue") is not None and latest and latest.get("revenue") is not None:
        add("previous_revenue", f"Revenue (FY{fiscal_year(previous)})", "latest_annual_accounts", money(previous["revenue"], currency),
            as_of=(previous.get("period") or {}).get("tilDato"))
    else:
        add("previous_revenue", "Revenue (previous filed year)", "latest_annual_accounts",
            "No second filed year to compare" if latest else None)
        facts[-1]["missing"] = True

    website = value("official_website")
    add("website", "Verified website", "official_website", website, link=website)
    profiles = value("social_profiles") or []
    add("profiles", "Social profiles", "social_profiles",
        " · ".join(f"{item.get('platform')}: {item.get('url')} ({item.get('confidence') or '?'})" for item in profiles) if profiles else None,
        links=profiles)
    news = sorted([claim for claim in _claims(envelope, "dated_news") if claim.get("availability") == "available"],
                  key=lambda claim: claim["value"]["published_at"], reverse=True)
    if news:
        top = news[0]
        facts.append(_fact(envelope, evidence, "news", "Latest dated news", top,
                           f"{top['value']['title']} ({top['value']['published_at']})", link=top["value"]["url"]))
    else:
        add("news", "Latest dated news", "dated_news", None)
    jobs = value("company_jobs")
    jobs_text = None
    if jobs:
        postings = len(jobs.get("job_postings") or [])
        jobs_text = f"{postings} structured job posting{'s' if postings != 1 else ''}" if postings else "Careers page (no structured postings)"
    add("jobs", "Hiring", "company_jobs", jobs_text,
        link=((jobs or {}).get("careers_pages") or [{}])[0].get("url") if jobs else None)
    group = value("group_structure") or []
    add("group", "Corporate group", "group_structure", f"{len(group)} related entit{'y' if len(group) == 1 else 'ies'}" if group else None)
    locations = value("registered_locations") or []
    add("locations", "Registered sub-units", "registered_locations", f"{len(locations)}" if locations else None)
    for fact in facts:
        family = FACT_FAMILY.get(fact["key"])
        if fact["missing"] and family in reasons and not (family == "employees" and fact["key"] != "employees"):
            fact["reason"] = reasons[family]
            if fact["key"] in {"website", "news", "jobs", "profiles", "employees", "roles"}:
                fact["text"] = "Not available"
    return facts


def _status_key(envelope: dict[str, Any]) -> str:
    claim = _claim(envelope, "registry_status")
    status = (claim or {}).get("value") or {}
    if not status:
        return "unknown"
    if status.get("deleted"):
        return "deleted"
    if status.get("bankrupt") or status.get("liquidating"):
        return "adverse"
    return "active"


def _summary_record(envelope: dict[str, Any]) -> dict[str, Any]:
    facts = {fact["key"]: fact for fact in company_facts(envelope)}
    name = facts["legal_name"]["text"] if not facts["legal_name"]["missing"] else f"Organisation {org_display(envelope['organisation_number'])}"
    return {
        "org": envelope["organisation_number"], "name": name, "status": _status_key(envelope),
        "industry": None if facts["industry"]["missing"] else facts["industry"]["text"],
        "address": None if facts["business_address"]["missing"] else facts["business_address"]["text"],
        "fy": None if facts["fiscal_year"]["missing"] else facts["fiscal_year"]["text"],
        "revenue": None if facts["revenue"]["missing"] else facts["revenue"]["text"],
        "website": not facts["website"]["missing"], "news": not facts["news"]["missing"], "jobs": not facts["jobs"]["missing"],
        "headline": (envelope.get("synthesis") or {}).get("headline"),
        "facts": list(facts.values()),
    }


CSS = """
:root{--paper:#f5f1e8;--card:#fffdf8;--ink:#171614;--muted:#5f5b53;--line:#d8d0c2;--accent:#7d2432;--good:#276044;--warn:#8a5a0b;--bad:#9b1c1c;--focus:#1d4ed8}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--paper:#15140f;--card:#1f1d18;--ink:#f1ede4;--muted:#b3ad9f;--line:#3a362d;--accent:#e58a99;--good:#7fc79e;--warn:#e2b25c;--bad:#f19a9a;--focus:#93b4ff}}
:root[data-theme=dark]{--paper:#15140f;--card:#1f1d18;--ink:#f1ede4;--muted:#b3ad9f;--line:#3a362d;--accent:#e58a99;--good:#7fc79e;--warn:#e2b25c;--bad:#f19a9a;--focus:#93b4ff}
*{box-sizing:border-box}[hidden]{display:none!important}html{-webkit-text-size-adjust:100%}
label.chip{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;min-height:40px}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent);overflow-wrap:anywhere}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
header.top{border-bottom:1px solid var(--line);background:var(--card)}
.wrap{max-width:1180px;margin:0 auto;padding:16px}
.brand{font-weight:700;letter-spacing:.01em}.brand a{color:var(--ink);text-decoration:none}
.meta{color:var(--muted);font-size:13px}
h1{font-size:clamp(22px,4vw,30px);line-height:1.2;margin:8px 0}h2{font-size:18px;margin:28px 0 10px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:12px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}.stat b{display:block;font-size:20px}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.controls input[type=search]{flex:1 1 260px;min-width:0}
input,select,button{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px 10px;min-height:40px}
button{cursor:pointer}button.primary{background:var(--accent);color:var(--card);border-color:var(--accent)}button:disabled{opacity:.5;cursor:not-allowed}
ul.list{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.row{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;display:grid;grid-template-columns:1fr auto;gap:6px 12px;align-items:start}
.row .name{font-weight:650}.row .sub{color:var(--muted);font-size:13px;overflow-wrap:anywhere}
.row label{display:flex;gap:6px;align-items:center;font-size:13px;white-space:nowrap}
.chips{display:flex;flex-wrap:wrap;gap:4px;grid-column:1/-1}
.chip{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--muted)}
.chip.good{color:var(--good);border-color:currentColor}.chip.warn{color:var(--warn);border-color:currentColor}.chip.bad{color:var(--bad);border-color:currentColor}
.tray{position:sticky;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:10px 16px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;z-index:2}
.empty{color:var(--muted)}
.summary{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;padding:12px 16px}
.summary p{margin:6px 0}.cite{font-size:12px;white-space:nowrap}.cite a{text-decoration:none}
.topic{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-top:10px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px}
th,td{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid var(--line)}th{font-size:13px;color:var(--muted);font-weight:600}
td.missing,.missing{color:var(--muted);font-style:italic}
.src{display:block;font-size:12px;color:var(--muted)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.cmp{display:grid;gap:0;background:var(--card);border:1px solid var(--line);border-radius:8px}
.cmp .r{display:grid;grid-template-columns:minmax(120px,180px) repeat(var(--n),minmax(0,1fr));border-bottom:1px solid var(--line)}
.cmp .r>div{padding:8px 10px;overflow-wrap:anywhere}.cmp .h{font-weight:650}.cmp .l{color:var(--muted);font-size:13px}
.pickers{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;margin:12px 0}
code{font-size:12px;overflow-wrap:anywhere}
details summary{cursor:pointer}summary:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.skip{position:absolute;left:-9999px;top:0;background:var(--card);padding:10px 14px;z-index:5}.skip:focus{left:8px;top:8px}
.sticky{position:sticky;top:0;z-index:3;background:var(--card);border-bottom:1px solid var(--line)}
.sticky .wrap{display:flex;flex-wrap:wrap;align-items:center;gap:4px 14px;padding-top:6px;padding-bottom:6px}
.sticky .who{font-weight:700;min-width:0;overflow-wrap:anywhere}.sticky .org{color:var(--muted);font-size:13px;white-space:nowrap}
.sticky nav{display:flex;flex-wrap:wrap;gap:2px 10px;margin-left:auto}.sticky nav a{display:inline-flex;align-items:center;min-height:44px;padding:0 4px}
h2{scroll-margin-top:72px}tr[id]{scroll-margin-top:72px}
.summary{display:grid;gap:12px}.summary h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:4px 0 2px}
.summary .lead{font-size:17px;font-weight:650;margin:2px 0}
.summary ul{margin:0;padding-left:18px}.summary li{margin:4px 0}
.signals{display:flex;flex-wrap:wrap;gap:8px;list-style:none;padding:0!important}
.signals li{border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--paper);flex:1 1 220px;margin:0!important}
.signals b{display:block}
.srcs{display:inline;font-size:12px;color:var(--muted)}.srcs a{text-decoration:none;white-space:nowrap;padding:2px 4px}
.ids{font-family:ui-monospace,monospace;font-size:11px;overflow-wrap:anywhere}
table.evidence td:first-child code{white-space:nowrap}table.evidence td:nth-child(2){min-width:240px;max-width:420px}
@media (max-width:640px){table.evidence{min-width:640px}}
.reason{display:block;font-size:13px;color:var(--muted);font-style:normal}
.row .lead{grid-column:1/-1;font-size:14px;margin:0}
.row label{min-height:44px;padding:0 4px}
input,select,button{min-height:44px}label.chip{min-height:44px}
@media (pointer:coarse),(max-width:640px){a{display:inline-flex;align-items:center;min-height:44px}summary{padding:11px 0;min-height:44px}.cmp a{display:inline-flex}}
@media (max-width:640px){
 .row{grid-template-columns:1fr}
 table.facts thead{display:none}table.facts tr{display:block;border-bottom:1px solid var(--line);padding:6px 0}table.facts td{display:block;border:0;padding:2px 10px}
 table.facts td:first-child{font-size:12px;color:var(--muted);font-style:normal}
 .cmp .r{grid-template-columns:1fr}.cmp .r>div.l{background:var(--paper);font-weight:600}
 .cmp .r>div[data-company]::before{content:attr(data-company);display:block;font-size:11px;color:var(--muted)}
 .cmp .r.h-row{display:none}
}
"""

APP_JS = r"""
(function(){
// fold: lower case, æ→ae ø→o å→a, accents and everything but letters/digits dropped (so spaces never matter)
const fold=v=>String(v??'').toLowerCase().replace(/æ/g,'ae').replace(/ø/g,'o').replace(/å/g,'a').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]/g,'');
const digits=v=>String(v??'').replace(/\D/g,'');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safe=u=>/^https?:\/\//i.test(String(u||''))?u:null;
const MAX=3;
function selected(){try{return JSON.parse(sessionStorage.getItem('sp-compare')||'[]')}catch(e){return []}}
function store(v){try{sessionStorage.setItem('sp-compare',JSON.stringify(v))}catch(e){}}
// Index: search, filters, compare tray.
const list=document.getElementById('companies');
if(list){
  const rows=[...list.querySelectorAll('li[data-org]')],q=document.getElementById('q'),st=document.getElementById('status'),
        web=document.getElementById('has-website'),news=document.getElementById('has-news'),jobs=document.getElementById('has-jobs'),count=document.getElementById('count'),
        tray=document.getElementById('tray-names'),go=document.getElementById('compare-go'),clear=document.getElementById('compare-clear');
  let picks=selected().filter(o=>rows.some(r=>r.dataset.org===o));
  function apply(){
    const text=fold(q.value);let shown=0;
    rows.forEach(r=>{const okText=!text||r.dataset.name.includes(text);
      const ok=okText&&(!st.value||r.dataset.status===st.value)&&(!web.checked||r.dataset.website==='1')&&(!news.checked||r.dataset.news==='1')&&(!jobs.checked||r.dataset.jobs==='1');
      r.hidden=!ok;if(ok)shown++});
    count.textContent=shown+' of '+rows.length+' companies';
  }
  function renderTray(){
    rows.forEach(r=>{const box=r.querySelector('input[type=checkbox]');box.checked=picks.includes(r.dataset.org);box.disabled=!box.checked&&picks.length>=MAX});
    tray.textContent=picks.length?picks.map(o=>rows.find(r=>r.dataset.org===o).dataset.label).join(' · '):'Pick 2–3 companies to compare';
    go.disabled=picks.length<2;clear.hidden=!picks.length;store(picks);
  }
  list.addEventListener('change',e=>{const box=e.target;if(box.type!=='checkbox')return;const org=box.closest('li').dataset.org;
    picks=box.checked?[...new Set([...picks,org])].slice(0,MAX):picks.filter(o=>o!==org);renderTray()});
  go.addEventListener('click',()=>{location.href='compare.html?orgs='+picks.join(',')});
  clear.addEventListener('click',()=>{picks=[];renderTray()});
  [q,st,web,news,jobs].forEach(el=>el.addEventListener(el===q?'input':'change',apply));
  const initial=new URLSearchParams(location.search).get('q');if(initial)q.value=initial;
  apply();renderTray();
}
// Compare view.
const cmp=document.getElementById('compare');
if(cmp){
  const COMPANIES=window.SP_COMPANIES||[],byOrg=Object.fromEntries(COMPANIES.map(c=>[c[0],c[1]])),loaded={};
  window.SP_LOAD=(org,data)=>{loaded[org]=data;render()};
  let orgs=(new URLSearchParams(location.search).get('orgs')||'').split(',').map(digits).filter(o=>byOrg[o]).slice(0,MAX);
  if(!orgs.length)orgs=selected().filter(o=>byOrg[o]).slice(0,MAX);
  const pickers=[...document.querySelectorAll('.pick')];
  pickers.forEach((input,i)=>{input.value=orgs[i]?byOrg[orgs[i]]+' — '+orgs[i]:'';
    input.addEventListener('change',()=>{const m=digits(input.value.split('—').pop());const next=pickers.map((p,j)=>j===i?m:orgs[j]).filter(o=>o&&byOrg[o]);
      orgs=[...new Set(next)].slice(0,MAX);store(orgs);history.replaceState(null,'','?orgs='+orgs.join(','));load()})});
  function load(){orgs.forEach(o=>{if(loaded[o])return;const s=document.createElement('script');s.src='data/'+o+'.js';
    s.onerror=()=>{loaded[o]={org:o,name:byOrg[o],facts:[],error:true};render()};document.head.appendChild(s)});render()}
  function cell(f){if(!f)return '<span class="missing">Not available</span><span class="reason">Not checked for this company</span>';
    const val=f.link&&safe(f.link)?'<a href="'+esc(f.link)+'" target="_blank" rel="noopener noreferrer">'+esc(f.text)+'</a>':esc(f.text);
    const dates=[f.as_of?'as of '+esc(f.as_of):'',f.retrieved?'retrieved '+esc(f.retrieved):''].filter(Boolean).join(' · ');
    const src=safe(f.src)?'<a href="'+esc(f.src)+'" target="_blank" rel="noopener noreferrer">source ↗</a>':'no source';
    return '<span class="'+(f.missing?'missing':'')+'">'+val+'</span>'+(f.reason?'<span class="reason">'+esc(f.reason)+'</span>':'')+'<span class="src">'+src+(dates?' · '+dates:'')+(f.ev?' · <code>'+esc(f.ev)+'</code>':'')+'</span>'}
  function render(){
    if(orgs.length<2){cmp.innerHTML='<p class="empty">Choose at least two companies above, or pick them on the <a href="index.html">search page</a>.</p>';return}
    const data=orgs.map(o=>loaded[o]);if(data.some(d=>!d)){cmp.innerHTML='<p class="empty">Loading…</p>';return}
    const keys=[];data.forEach(d=>d.facts.forEach(f=>{if(!keys.some(k=>k[0]===f.key))keys.push([f.key,f.label])}));
    let out='<div class="cmp" style="--n:'+data.length+'"><div class="r h-row"><div class="l">Company</div>'+data.map(d=>'<div class="h"><a href="companies/'+esc(d.org)+'.html">'+esc(d.name)+'</a></div>').join('')+'</div>';
    keys.forEach(([k,label])=>{out+='<div class="r"><div class="l">'+esc(label.replace(/ \(FY\d{4}\)$/,''))+'</div>'+data.map(d=>{const f=d.facts.find(x=>x.key===k);
      return '<div data-company="'+esc(d.name)+'">'+(f&&/\(FY\d{4}\)$/.test(f.label)?'<span class="src">'+esc(f.label.match(/FY\d{4}/)[0])+'</span>':'')+cell(f)+'</div>'}).join('')+'</div>'});
    cmp.innerHTML=out+'</div>';
  }
  load();
}
})();
"""


def _chip(text: str, kind: str = "") -> str:
    return f'<span class="chip {kind}">{esc(text)}</span>'


def _page(title: str, body: str, *, depth: int = 0, scripts: list[str] | None = None, description: str = "") -> str:
    prefix = "../" * depth
    tags = "".join(f'<script src="{prefix}{src}"></script>' for src in scripts or [])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><meta name="description" content="{esc(description)}"><link rel="icon" href="data:,">'
            f'<link rel="stylesheet" href="{prefix}assets/style.css"></head><body><a class="skip" href="#main">Skip to content</a>'
            f'<header class="top"><div class="wrap"><div class="brand"><a href="{prefix}index.html">Signalpost Norway</a></div>'
            f'<div class="meta">Company profiles from official registers and identity-verified company sources</div></div></header>'
            f'{body.replace("<main class=", "<main id=main class=", 1)}{tags}</body></html>')


def _source_html(fact: dict[str, Any]) -> str:
    dates = " · ".join(part for part in (f"as of {fact['as_of']}" if fact.get("as_of") else "",
                                          f"retrieved {fact['retrieved']}" if fact.get("retrieved") else "") if part)
    source = f'<a href="{esc(fact["src"])}" target="_blank" rel="noopener noreferrer">source ↗</a>' if fact.get("src") else "no source"
    evidence = f' · <a href="#{esc(fact["ev"])}"><code>{esc(fact["ev"])}</code></a>' if fact.get("ev") else ""
    return f'<span class="src">{source}{" · " + esc(dates) if dates else ""}{evidence}</span>'


def _link(url: Any) -> str:
    target = safe_url(url)
    if not target:
        return esc(url or "—")
    return f'<a href="{esc(target)}" target="_blank" rel="noopener noreferrer">{esc(target)}</a>'


def _value_html(fact: dict[str, Any]) -> str:
    if fact.get("missing") and fact.get("reason"):
        return f'{esc(fact["text"])}<span class="reason">{esc(fact["reason"])}</span>'

    if fact.get("links"):
        # one clickable link per profile, with its confidence and proof
        return "<br>".join(
            f'<a href="{esc(item["url"])}" target="_blank" rel="noopener noreferrer">{esc(item.get("platform") or "")}: {esc(item["url"])}</a> '
            f'{_chip(item.get("confidence") or "unknown", "good" if item.get("confidence") == "high" else "warn")}'
            f'{"<br><small>" + esc(item["proof"]) + "</small>" if item.get("proof") else ""}' for item in fact["links"])
    text = esc(fact["text"])
    if fact.get("link") and not fact.get("missing"):
        text = f'<a href="{esc(fact["link"])}" target="_blank" rel="noopener noreferrer">{text}</a>'
    return text


FACT_LABELS = {
    "role": "Role", "registered_location": "Registered sub-unit", "annual_revenue": "Revenue", "operating_result": "Operating result",
    "annual_result": "Annual result", "equity": "Equity", "total_assets": "Total assets", "nace_code": "NACE code",
    "founding_date": "Founded", "registration_date": "Registered", "share_capital": "Share capital", "vat_registered": "VAT register",
    "register_membership": "Register", "institutional_sector": "Institutional sector", "parent_entity": "Parent unit",
    "registered_purpose": "Registered purpose", "registered_activity": "Registered activity", "auditor_opt_out": "Auditor",
    "registry_email": "Registry e-mail", "registry_phone": "Registry phone", "group_relationship": "Group relationship",
    "external_profile": "Profile", "careers_page": "Careers page", "job_posting": "Job posting", "site_address": "Address on website",
    "site_phone": "Phone on website", "site_email": "E-mail on website", "site_logo": "Logo", "group_website": "Group website",
}


def fact_text(field: str, value: Any) -> tuple[str, str | None]:
    """Readable text and optional link for a fact-level claim value."""
    if not isinstance(value, dict):
        text = str(value)
        return text, (value if field == "site_logo" else None)
    if field == "role":
        return f"{value.get('role')}: {value.get('name')}", None
    if field == "registered_location":
        parts = [value.get("name"), value.get("address"), f"NACE {value['nace_code']}" if value.get("nace_code") else None,
                 f"{value['employees']} employees" if value.get("employees") is not None else None, f"org. no. {value.get('organisation_number')}"]
        return " · ".join(str(part) for part in parts if part), None
    if "amount" in value and "fiscal_year" in value:
        return f"FY{value['fiscal_year']}: {money(value['amount'], value.get('currency'))}", None
    if field == "nace_code":
        return f"{value.get('code')} {value.get('description') or ''} ({'main' if value.get('rank') == 1 else 'secondary'})".strip(), None
    if field == "share_capital":
        return f"{money(value.get('amount'), value.get('currency'))}" + (f" · {value['shares']} shares" if value.get("shares") else ""), None
    if field in {"vat_registered", "register_membership"}:
        return f"{value.get('register')}: {'registered' if value.get('registered') else 'not registered'}", None
    if field in {"parent_entity", "group_relationship"}:
        return f"{value.get('relationship')}: {value.get('name') or ''} ({value.get('organisation_number')})".replace(":  (", ": ("), None
    if field == "institutional_sector":
        return f"{value.get('code')} {value.get('description') or ''}".strip(), None
    if field == "auditor_opt_out":
        return "Opted out of audit", None
    if field == "registry_phone":
        return str(value.get("number")), None
    if field in {"external_profile", "careers_page"}:
        return f"{value.get('platform') or value.get('title') or ''} {value.get('url')}".strip(), value.get("url")
    if field == "job_posting":
        return f"{value.get('title')}" + (f" (posted {value['date_posted'][:10]})" if value.get("date_posted") else ""), value.get("source_url")
    if field == "group_website":
        return f"{value.get('url')} — {value.get('relationship')}", value.get("url")
    return json.dumps(value, ensure_ascii=False)[:200], None


def facts_table(envelope: dict[str, Any]) -> str:
    evidence = _evidence_map(envelope)
    rows = []
    for claim in envelope.get("claims") or []:
        if not claim.get("fact_level") or claim.get("availability") != "available":
            continue
        text, link = fact_text(claim["field"], claim["value"])
        fact = {**_source(envelope, claim, evidence), "text": text, "link": safe_url(link), "missing": False}
        rows.append(f'<tr><td>{esc(FACT_LABELS.get(claim["field"], claim["field"]))}</td><td>{_value_html(fact)}</td><td>{_source_html(fact)}</td></tr>')
    if not rows:
        return '<p class="empty">No fact-level claims.</p>'
    return (f'<details><summary>{len(rows)} individual facts, each with its source and date</summary><div class="scroll"><table class="facts">'
            f'<thead><tr><th>Fact</th><th>Value</th><th>Source and date</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>')


LINK_LABELS = {"official_website": "Website", "external_profile": "Profile", "dated_news": "News", "company_activity": "Article",
               "careers_page": "Careers page", "job_posting": "Job posting"}


def links_section(envelope: dict[str, Any]) -> str:
    """Every published link (website, profiles, news, jobs) with its confidence and proof, weakest first, and the
    candidates that were found but not published, with the reason."""
    evidence = _evidence_map(envelope)
    rows = []
    for claim in envelope.get("claims") or []:
        field = claim.get("field")
        if field not in LINK_LABELS or claim.get("availability") != "available":
            continue
        value = claim.get("value")
        if field == "official_website":
            text, link = str(value), value
        elif field == "dated_news":
            text, link = f'{value.get("title")} ({value.get("published_at")})', value.get("url")
        elif field == "company_activity":
            for item in value or []:
                rows.append((claim, f'{item.get("title")} ({item.get("latest_declared_date")})', item.get("url")))
            continue
        else:
            text, link = fact_text(field, value)
        rows.append((claim, text, link))
    rows.sort(key=lambda row: (row[0].get("confidence_level") != "medium", LINK_LABELS[row[0]["field"]]))
    body = ""
    for claim, text, link in rows:
        level = claim.get("confidence_level") or "unknown"
        extra = f' · found by {esc(str(claim.get("discovery_method")).replace("_", " "))}' if claim.get("discovery_method") else ""
        fact = {**_source(envelope, claim, evidence), "text": text, "link": safe_url(link), "missing": False}
        body += (f'<tr><td>{esc(LINK_LABELS[claim["field"]])}</td><td>{_value_html(fact)}</td>'
                 f'<td>{_chip(level, "good" if level == "high" else "warn")}</td>'
                 f'<td>{esc(claim.get("proof") or "")}{extra}{_source_html(fact)}</td></tr>')
    html_out = (f'<div class="scroll"><table class="facts"><thead><tr><th>Kind</th><th>Link</th><th>Confidence</th><th>Proof</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div>') if body else '<p class="empty">No website, profile, news or job link was published for this company.</p>'
    missed = [(claim.get("field"), item) for claim in envelope.get("claims") or [] if claim.get("field") != "social_profiles"
              for item in claim.get("not_published") or []]
    if missed:
        items = "".join(f'<li>{esc(LINK_LABELS.get(field, "Profile" if field == "external_profiles" else field))}: '
                        f'<code>{esc(item.get("url"))}</code> — {esc(item.get("reason"))}</li>' for field, item in missed[:20])
        html_out += f'<details><summary>{len(missed)} candidate link(s) found but not published, with the reason</summary><ul>{items}</ul></details>'
    return html_out


def _host_label(url: str | None) -> str:
    host = urllib.parse.urlparse(url or "").netloc
    return host[4:] if host.startswith("www.") else host or "source"


def sentence_sources(item: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> str:
    """The source links of one summary sentence, one tap each (external source, then the evidence row)."""
    links, seen = [], set()
    for evidence_id in item.get("evidence_ids") or []:
        record = evidence.get(evidence_id) or {}
        source = safe_url(record.get("item_url")) or safe_url(record.get("source_url"))
        if source and source not in seen:
            seen.add(source)
            links.append(f'<a href="{esc(source)}" target="_blank" rel="noopener noreferrer">{esc(_host_label(source))} ↗</a>')
    ids = ", ".join(esc(eid) for eid in item.get("evidence_ids") or [])
    return f' <span class="srcs">{" ".join(links[:3])} <span class="ids" title="Evidence ids (see Sources)">[{ids}]</span></span>'


BRIEF_SECTIONS = (("headline", "Buyer fact"), ("why_it_matters", "Why it matters"), ("what_it_does", "What it does"), ("signal", "Signals"),
                  ("changes", "What changed"), ("unknown", "What’s unknown"), ("financials", "Latest filed figures"),
                  ("profile", "Registry profile"))


def brief_html(envelope: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> str:
    sentences = (envelope.get("synthesis") or {}).get("sentences") or []
    out = ""
    for topic, heading in BRIEF_SECTIONS:
        items = [item for item in sentences if item.get("topic") == topic]
        if not items:
            continue
        if topic == "headline":
            out += f'<div><h3>{esc(heading)}</h3><p class="lead">{esc(items[0]["text"])}{sentence_sources(items[0], evidence)}</p></div>'
        elif topic == "why_it_matters":
            text = re.sub(r"^Why it matters(?: \(FY\d{4} → FY\d{4}\))?: ", "", items[0]["text"])
            out += f'<div><h3>{esc(heading)}</h3><p>{esc(text[0].upper() + text[1:])}{sentence_sources(items[0], evidence)}</p></div>'
        elif topic == "signal":
            cards = "".join(f'<li><b>{esc(item.get("label") or "")}</b>{esc(item["text"].split(": ", 1)[-1])}{sentence_sources(item, evidence)}</li>'
                            for item in items)
            out += f'<div><h3>{esc(heading)}</h3><ul class="signals">{cards}</ul></div>'
        else:
            lis = "".join(f'<li>{esc(item["text"])}{sentence_sources(item, evidence)}</li>' for item in items)
            out += f'<div><h3>{esc(heading)}</h3><ul>{lis}</ul></div>'
    return out or '<p class="empty">No sourced summary could be written for this company.</p>'


def company_page(envelope: dict[str, Any], record: dict[str, Any], run: dict[str, Any]) -> str:
    org = envelope["organisation_number"]
    evidence = _evidence_map(envelope)
    synthesis = envelope.get("synthesis") or {}
    summary_html = brief_html(envelope, evidence)
    fact_rows = "".join(
        f'<tr><td>{esc(fact["label"])}</td><td class="{"missing" if fact["missing"] else ""}">{_value_html(fact)}</td><td>{_source_html(fact)}</td></tr>'
        for fact in record["facts"])

    financial_claim = _claim(envelope, "latest_annual_accounts")
    records = ((((envelope.get("profile") or {}).get("evidence") or {}).get("financials") or {}).get("value") or {}).get("records") or [] \
        if (financial_claim or {}).get("availability") == "available" else []
    fin_source = _source(envelope, financial_claim, evidence)
    fin_html = '<p class="empty">No filed annual accounts returned.</p>'
    if records:
        ordered = sorted(records, key=lambda item: (((item.get("period") or {}).get("tilDato") or ""), item.get("account_type") or ""), reverse=True)
        head = "".join(f"<th>{label}</th>" for label in ("Fiscal year", "Type", "Revenue", "Operating result", "Annual result", "Equity", "Source"))
        body = ""
        for item in ordered:
            period = item.get("period") or {}
            cells = "".join(f'<td class="{"missing" if item.get(key) is None else ""}">{esc(money(item.get(key), item.get("currency")))}</td>'
                            for key in ("revenue", "operating_result", "annual_result", "equity"))
            body += (f'<tr><td>FY{esc(fiscal_year(item))}<span class="src">{esc(period.get("fraDato"))} – {esc(period.get("tilDato"))}</span></td>'
                     f'<td>{"Group" if item.get("account_type") == "KONSERN" else "Company"}</td>{cells}'
                     f'<td>{_source_html({**fin_source, "as_of": _day(period.get("tilDato"))})}</td></tr>')
        fin_html = f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    news = sorted([claim for claim in _claims(envelope, "dated_news") if claim.get("availability") == "available"],
                  key=lambda claim: claim["value"]["published_at"], reverse=True)
    if news:
        news_html = "<ul>" + "".join(
            f'<li><a href="{esc(safe_url(claim["value"]["url"]) or "")}" target="_blank" rel="noopener noreferrer">{esc(claim["value"]["title"])}</a> '
            f'— published {esc(claim["value"]["published_at"])}{_source_html(_source(envelope, claim, evidence))}</li>' for claim in news) + "</ul>"
    else:
        news_claim = _claim(envelope, "dated_news")
        news_html = f'<p class="empty">{esc(AVAILABILITY_TEXT.get((news_claim or {}).get("availability") or "not_available", "Not available"))}.</p>'

    roles = (_claim(envelope, "roles") or {}).get("value") or []
    roles_html = "<ul>" + "".join(f"<li>{esc(item.get('role'))}: {esc(_org_name(item.get('name')))}</li>" for item in roles[:20]) + "</ul>" \
        if roles else '<p class="empty">No registered roles returned.</p>'
    if roles:
        roles_html += _source_html(_source(envelope, _claim(envelope, "roles"), evidence))

    evidence_rows = "".join(
        f'<tr id="{esc(item["id"])}"><td><code>{esc(item["id"])}</code></td><td>{_link(item.get("source_url"))}'
        f'{("<span class=src>item: " + esc(item.get("item_url")) + "</span>") if item.get("item_url") else ""}</td>'
        f'<td>{esc(item.get("source_class"))}</td><td>{esc(_day(item.get("retrieved_at")) or "—")}</td>'
        f'<td>{esc(item.get("claim_span") or "")}{("<span class=src>sha256 " + esc(item.get("content_sha256")) + "</span>") if item.get("content_sha256") else ""}</td></tr>'
        for item in envelope.get("evidence") or [])

    status = record["status"]
    chips = _chip({"active": "Active", "deleted": "Deleted", "adverse": "Bankrupt / liquidation", "unknown": "Status unknown"}[status],
                  {"active": "good", "deleted": "bad", "adverse": "bad"}.get(status, ""))
    body = (f'<div class="sticky"><div class="wrap"><span class="who">{esc(record["name"])}</span>'
            f'<span class="org">Org. no. {esc(org_display(org))}</span>'
            f'<nav aria-label="On this page"><a href="#summary">Summary</a><a href="#facts">Facts</a><a href="#evidence">Sources</a>'
            f'<a href="../index.html">All companies</a></nav></div></div>'
            f'<main class="wrap"><h1>{esc(record["name"])}</h1><p class="meta">Org. no. {esc(org_display(org))} · {chips} · run {esc(run.get("run_id"))}, '
            f'completed {esc(_day(run.get("completed_at")))} · <a href="../compare.html?orgs={esc(org)}">Compare with others</a></p>'
            f'<h2 id="summary">Summary · What changed · Unknowns</h2><section class="summary" aria-labelledby="summary">{summary_html}</section>'
            f'<h2 id="facts">Facts</h2><div class="scroll"><table class="facts"><thead><tr><th>Fact</th><th>Value</th><th>Source and date</th></tr></thead>'
            f'<tbody>{fact_rows}</tbody></table></div>'
            f'<h2>Links and confidence</h2>{links_section(envelope)}'
            f'<h2>All facts</h2>{facts_table(envelope)}'
            f'<h2>Filed annual accounts</h2>{fin_html}'
            f'<h2>Dated news from its verified website</h2>{news_html}'
            f'<h2>Registered roles</h2>{roles_html}'
            f'<h2 id="evidence">Sources ({len(envelope.get("evidence") or [])} evidence records)</h2>'
            f'<div class="scroll"><table class="evidence"><thead><tr><th>Id</th><th>Source</th><th>Class</th><th>Retrieved</th><th>Span / hash</th></tr></thead>'
            f'<tbody>{evidence_rows}</tbody></table></div></main>')
    return _page(f"{record['name']} — Signalpost Norway", body, depth=1,
                 description=(synthesis.get("headline") or ((synthesis.get("sentences") or [{}])[0].get("text", "")))[:200])


def _fold(text: str, variant: int) -> str:
    text = str(text or "").casefold()
    for letter, plain in (("æ", ("ae", "a")), ("ø", ("o", "oe")), ("å", ("a", "aa"))):
        text = text.replace(letter, plain[variant])
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", text)


def search_key(name: str, org: str, *more: str | None) -> str:
    """What the index search matches: the name in both æ/ø/å spellings, the org number and the industry/address,
    each folded with no spaces or punctuation (the browser folds the query the same way)."""
    parts = [_fold(name, 0), _fold(name, 1), org, *(_fold(item or "", 0) for item in more)]
    return "|".join(dict.fromkeys(part for part in parts if part))


def index_page(records: list[dict[str, Any]], run: dict[str, Any]) -> str:
    total = len(records)
    stats = [(total, "companies"), (sum(r["website"] for r in records), "verified websites"),
             (sum(r["news"] for r in records), "with dated news"), (sum(r["jobs"] for r in records), "with jobs"),
             (sum(bool(r["fy"]) for r in records), "with filed accounts")]
    stats_html = "".join(f'<div class="stat"><b>{value}</b>{esc(label)}</div>' for value, label in stats)
    rows = []
    for r in records:
        label = r["name"]
        chips = [_chip({"active": "Active", "deleted": "Deleted", "adverse": "Bankrupt / liquidation", "unknown": "Status unknown"}[r["status"]],
                       {"active": "good", "deleted": "bad", "adverse": "bad"}.get(r["status"], ""))]
        if r["fy"]:
            chips.append(_chip(f"{r['fy'].split(' ')[0]} revenue: {r['revenue'] or 'not reported'}"))
        if r["website"]:
            chips.append(_chip("Verified website", "good"))
        if r["news"]:
            chips.append(_chip("Dated news", "good"))
        if r["jobs"]:
            chips.append(_chip("Jobs", "good"))
        folded = search_key(r["name"], r["org"], r["industry"], r["address"])
        rows.append(
            f'<li class="row" data-org="{esc(r["org"])}" data-name="{esc(folded)}" data-label="{esc(label)}" data-status="{esc(r["status"])}" '
            f'data-website="{int(r["website"])}" data-news="{int(r["news"])}" data-jobs="{int(r["jobs"])}">'
            f'<div><a class="name" href="companies/{esc(r["org"])}.html">{esc(label)}</a>'
            f'<div class="sub">{esc(org_display(r["org"]))}{" · " + esc(r["industry"]) if r["industry"] else ""}{" · " + esc(r["address"]) if r["address"] else ""}</div></div>'
            f'<label><input type="checkbox" aria-label="Compare {esc(label)}"> Compare</label>'
            f'{"<p class=lead>" + esc(r["headline"]) + "</p>" if r.get("headline") else ""}'
            f'<div class="chips">{"".join(chips)}</div></li>')
    body = (f'<main class="wrap"><h1>Norwegian company profiles</h1>'
            f'<p class="meta">Run {esc(run.get("run_id"))} · completed {esc(run.get("completed_at"))} · every fact links to its source and date.</p>'
            f'<div class="stats">{stats_html}</div>'
            f'<div class="controls" role="search"><input id="q" type="search" placeholder="Search by name or organisation number (spaces, æ/ø/å ignored)" '
            f'aria-label="Search by name or organisation number" autocomplete="off">'
            f'<select id="status" aria-label="Registry status"><option value="">Any status</option><option value="active">Active</option>'
            f'<option value="adverse">Bankrupt / liquidation</option><option value="deleted">Deleted</option><option value="unknown">Unknown</option></select>'
            f'<label class="chip"><input id="has-website" type="checkbox"> Verified website</label>'
            f'<label class="chip"><input id="has-news" type="checkbox"> Dated news</label>'
            f'<label class="chip"><input id="has-jobs" type="checkbox"> Jobs</label></div>'
            f'<p class="meta" id="count" aria-live="polite">{total} companies</p>'
            f'<ul class="list" id="companies">{"".join(rows)}</ul></main>'
            f'<div class="tray" role="region" aria-label="Compare selection"><span id="tray-names" class="meta">Pick 2–3 companies to compare</span>'
            f'<span><button id="compare-clear" type="button" hidden>Clear</button> <button id="compare-go" class="primary" type="button" disabled>Compare</button></span></div>')
    return _page("Signalpost Norway — company search", body, scripts=["assets/app.js"],
                 description="Search, compare and verify Norwegian company profiles with sourced facts.")


def compare_page(records: list[dict[str, Any]]) -> str:
    options = "".join(f'<option value="{esc(r["name"])} — {esc(r["org"])}">' for r in records)
    pickers = "".join(f'<input class="pick" list="company-options" placeholder="Company {i} — name or org. no." aria-label="Company {i}">' for i in (1, 2, 3))
    body = (f'<main class="wrap"><p class="meta"><a href="index.html">← Search</a></p><h1>Compare companies</h1>'
            f'<p class="meta">Side by side, 2–3 companies. Each value links to its source with the date it refers to and the retrieval date.</p>'
            f'<div class="pickers">{pickers}</div><datalist id="company-options">{options}</datalist>'
            f'<div id="compare" class="scroll" aria-live="polite"><p class="empty">Loading…</p></div>'
            f'<noscript><p>The compare view needs JavaScript. Each company page lists the same facts with sources.</p></noscript></main>')
    return _page("Compare companies — Signalpost Norway", body, scripts=["assets/companies.js", "assets/app.js"],
                 description="Compare 2–3 Norwegian companies side by side with sourced facts.")


def _js_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def build_site(envelopes: list[dict[str, Any]], out_dir: str | Path, run: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the static site for these envelopes into out_dir (replacing a previous build). Returns the manifest."""
    run = run or {}
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    for sub in ("companies", "data", "assets"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    records = []
    for envelope in envelopes:
        org = str(envelope.get("organisation_number") or "")
        if not re.fullmatch(r"\d{9}", org):
            raise ValueError(f"Envelope without a valid organisation number: {org!r}")
        record = _summary_record(envelope)
        records.append(record)
        (out / "companies" / f"{org}.html").write_text(company_page(envelope, record, run), encoding="utf-8")
        compare_data = {"org": org, "name": record["name"],
                        "facts": [{key: fact.get(key) for key in ("key", "label", "text", "missing", "reason", "link", "src", "as_of", "retrieved", "ev")}
                                  for fact in record["facts"]]}
        (out / "data" / f"{org}.js").write_text(f"window.SP_LOAD&&SP_LOAD({_js_payload(org)},{_js_payload(compare_data)});\n", encoding="utf-8")
    ordered = sorted(records, key=lambda r: r["name"].casefold())
    (out / "assets" / "style.css").write_text(CSS.strip() + "\n", encoding="utf-8")
    (out / "assets" / "app.js").write_text(APP_JS.strip() + "\n", encoding="utf-8")
    (out / "assets" / "companies.js").write_text(f"window.SP_COMPANIES={_js_payload([[r['org'], r['name']] for r in ordered])};\n", encoding="utf-8")
    (out / "index.html").write_text(index_page(ordered, run), encoding="utf-8")
    (out / "compare.html").write_text(compare_page(ordered), encoding="utf-8")
    manifest = {
        "run_id": run.get("run_id"), "completed_at": run.get("completed_at"), "companies": len(records),
        "company_pages": len(records), "with_summary": sum(1 for e in envelopes if (e.get("synthesis") or {}).get("sentences")),
        "with_verified_website": sum(r["website"] for r in records), "with_dated_news": sum(r["news"] for r in records),
        "with_jobs": sum(r["jobs"] for r in records),
        "entry": "index.html", "compare": "compare.html",
    }
    (out / "site-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
