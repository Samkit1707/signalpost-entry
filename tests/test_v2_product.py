"""Offline tests for v2 product features: sourced synthesis, dated news from verified sites, static site."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import terminal_envelope  # noqa: E402
from norway_company_agent.citations import json_span  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.official import FIGURE_PATHS  # noqa: E402
from norway_company_agent.news import (  # noqa: E402
    MAX_REQUESTS, article_metadata, fetch_dated_news, news_listing_items, parse_date, parse_feed, parse_sitemap,
)
from norway_company_agent.site import build_site, company_facts  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
MODULES = ["registry", "accounting_obligation", "registry_live", "financials", "roles", "group", "locations",
           "wikidata_footprint", "website", "workforce", "external_profiles", "company_activity", "company_jobs", "dated_news"]
BRREG = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"


def account(year: int, kind: str = "SELSKAP", **figures) -> dict:
    base = {"revenue": 1000.0 * year, "operating_result": 10.0, "profit_before_tax": 9.0, "annual_result": 8.0,
            "assets": 50.0, "equity": 20.0, "debt": 30.0}
    base.update(figures)
    # as normalize_financials emits them: the JSON path and value of each figure in the accounts response
    return {"record_id": year, "account_type": kind, "currency": "NOK",
            "period": {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"}, **base,
            "period_span": json_span([("$[0].id", year), ("$[0].regnskapsperiode.tilDato", f"{year}-12-31")]),
            "figure_spans": {key: json_span([(f"$[0].{'.'.join(FIGURE_PATHS[key])}", value)]) for key, value in base.items() if value is not None}}


def make_profile(index: int, *, records: list | None = None, website: bool = False, news: list | None = None,
                 deleted: bool = False, name: str | None = None) -> dict:
    org = f"{900000000 + index:09d}"
    name = name or f"Testfirma {index} AS"
    row = {"organisasjonsnummer": org, "navn": name, "organisasjonsform.kode": "AS", "naeringskode1.kode": "62.010",
           "naeringskode1.beskrivelse": "Programmeringstjenester", "forretningsadresse.adresse": f"Gate {index}",
           "forretningsadresse.postnummer": "0150", "forretningsadresse.poststed": "OSLO"}
    ev = {
        "registry": evidence("registry", "available", "official_registry_bulk", BRREG, value=row, content_sha256="a" * 64),
        "accounting_obligation": evidence("accounting_obligation", "available", "official_registry_bulk", BRREG, content_sha256="a" * 64,
                                          value={"classification": "required", "claim_span": f'row[organisasjonsnummer={org}].organisasjonsform.kode="AS"'}),
        "registry_live": evidence("registry_live", "available", "official_registry_live", f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}",
                                  content_sha256="d" * 64,
                                  value={"name": name, "deleted": deleted, "deleted_date": "2026-05-01" if deleted else None,
                                         "bankrupt": False, "liquidating": False}),
        "financials": evidence("financials", "available" if records else "not_found", "official_annual_accounts",
                               f"https://data.brreg.no/regnskapsregisteret/regnskap/{org}", content_sha256="e" * 64,
                               value={"records": records} if records else None, note=None if records else "$ = [] (no annual accounts returned)"),
        "roles": evidence("roles", "available", "official_roles", f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}/roller",
                          content_sha256="f" * 64,
                          value={"roles": [{"name": "Kari Nordmann", "role": "Daglig leder", "role_code": "DAGL",
                                            "claim_span": '$.rollegrupper[0].roller[0].type.beskrivelse="Daglig leder"; '
                                                          '$.rollegrupper[0].roller[0].person.navn.fornavn="Kari"; '
                                                          '$.rollegrupper[0].roller[0].person.navn.etternavn="Nordmann"'}]}),
        "group": evidence("group", "not_found", "official_group_structure", "https://data.brreg.no/", note="HTTP 404", content_sha256="0" * 64),
        "locations": evidence("locations", "not_found", "official_subunits", "https://data.brreg.no/", content_sha256="1" * 64,
                              note="$.page.totalElements=0 (no registered sub-units)"),
        "wikidata_footprint": evidence("wikidata_footprint", "not_found", "wikidata_org_number_match", "https://query.wikidata.org/sparql"),
        "workforce": evidence("workforce", "not_found", "official_registry_bulk", BRREG),
        "external_profiles": evidence("external_profiles", "not_found", "company_owned_profiles", BRREG),
        "company_activity": evidence("company_activity", "not_found", "company_site_activity", BRREG),
        "company_jobs": evidence("company_jobs", "not_found", "company_site_careers", BRREG),
    }
    site = f"https://testfirma{index}.no/"
    if website:
        ev["website"] = evidence("website", "available", "registry_linked_company_website", site, content_sha256="b" * 64, value={
            "final_url": site, "title": f"{name} – hjem", "description": "Vi lager programvare for norske bedrifter.",
            "content_sha256": "b" * 64, "identity_text_excerpt": f"{name} · Org.nr {org[:3]} {org[3:6]} {org[6:]}",
            "identity_assessment": {"status": "exact", "score": 1.0, "publishable": True, "reasons": ["exact organisation number"]},
            "pages": []})
    else:
        ev["website"] = evidence("website", "not_found", "registry_linked_company_website", BRREG, note="No valid registry website URL")
    if news:
        ev["dated_news"] = evidence("dated_news", "available", "company_site_news", site + "feed/", value={"items": [
            {"title": title, "url": site + f"nyheter/{n}", "published_at": date, "source_kind": "rss", "source_url": site + "feed/",
             "content_sha256": "c" * 64, "retrieved_at": "2026-10-20T00:00:00Z", "company_match": {"verified_site": site},
             "claim_span": f"<title>{title}</title> <pubDate>{date}</pubDate>"}
            for n, (title, date) in enumerate(news)]})
    else:
        ev["dated_news"] = evidence("dated_news", "not_found", "company_site_news", site if website else BRREG)
    return {"organisation_number": org, "name": name, "legal_form": "AS", "employees": None, "bankrupt": False,
            "liquidating": False, "municipality": "OSLO", "industry_code": "62.010", "industry_label": "Programmeringstjenester",
            "website": site if website else None, "latest_submitted_accounts": None, "evidence": ev}


def envelope(profile: dict) -> dict:
    return terminal_envelope(profile, run_id="t", modules=MODULES, started_at="2026-10-20T00:00:00Z", completed_at="2026-10-20T00:01:00Z")


def sentences(env: dict, topic: str | None = None) -> list[str]:
    return [item["text"] for item in env["synthesis"]["sentences"] if topic is None or item["topic"] == topic]


# --- synthesis -------------------------------------------------------------------------------------------------

def test_latest_financials_use_newest_filed_year_and_company_accounts():
    env = envelope(make_profile(1, records=[account(2025, "KONSERN", revenue=999.0), account(2023), account(2025), account(2024)]))
    latest = next(claim for claim in env["claims"] if claim["field"] == "latest_annual_accounts")
    assert latest["value"]["period"]["tilDato"] == "2025-12-31" and latest["value"]["account_type"] == "SELSKAP"
    assert latest["as_of"] == "2025-12-31"
    text = " ".join(sentences(env, "financials"))
    assert "FY2025" in text and "NOK 2,025,000" in text and "999" not in text
    assert env["synthesis"]["latest_fiscal_year"] == "2025"
    change = " ".join(sentences(env, "changes"))
    assert "FY2024 to" in change and "FY2025" in change  # trend from the two newest filed years


def test_no_trend_with_one_filed_year_and_nulls_are_never_zero():
    env = envelope(make_profile(2, records=[account(2025, revenue=None, annual_result=None)]))
    text = env["synthesis"]["narrative"]
    assert "revenue not reported" in text and "annual result not reported" in text
    assert "NOK 0" not in text
    assert not re.search(r"\b(rose|fell|grew|growth|increase|decrease)\b", text)
    assert any("Only one filed year (FY2025)" in s for s in sentences(env, "unknown"))
    facts = {fact["key"]: fact for fact in company_facts(env)}
    assert facts["revenue"]["missing"] and "0" not in facts["revenue"]["text"]


def test_no_trend_when_previous_year_lacks_the_figure():
    env = envelope(make_profile(3, records=[account(2024, revenue=None, annual_result=None), account(2025)]))
    assert all("rose" not in s and "fell" not in s for s in sentences(env, "changes"))
    assert any("No financial trend is stated" in s for s in sentences(env, "unknown"))


def test_every_sentence_has_existing_evidence_ids_and_covers_the_four_topics():
    env = envelope(make_profile(4, records=[account(2024), account(2025)], website=True, news=[("Ny avtale signert", "2026-09-01")]))
    ids = {item["id"] for item in env["evidence"]}
    assert env["synthesis"]["sentences"]
    for item in env["synthesis"]["sentences"]:
        assert item["evidence_ids"] and set(item["evidence_ids"]) <= ids
    assert {item["topic"] for item in env["synthesis"]["sentences"]} >= {"what_it_does", "financials", "changes", "unknown"}
    assert "Ny avtale signert" in env["synthesis"]["narrative"] and "[ev-" in env["synthesis"]["narrative"]


def test_missing_accounts_and_deleted_entity_are_stated_not_invented():
    env = envelope(make_profile(5, records=None, deleted=True))
    text = env["synthesis"]["narrative"]
    assert "No annual accounts were returned" in text and "deleted on 2026-05-01" in text
    assert "NOK" not in text


# --- dated news ----------------------------------------------------------------------------------------------------

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Firma</title>
<item><title>Ny fabrikk &lt;b&gt;i dag&lt;/b&gt;</title><link>https://firma.no/nyheter/fabrikk</link><pubDate>Tue, 01 Sep 2026 10:00:00 +0200</pubDate></item>
<item><title>Fremtid</title><link>https://firma.no/nyheter/fremtid</link><pubDate>Tue, 01 Sep 2099 10:00:00 +0200</pubDate></item>
<item><title>Ekstern sak</title><link>https://annet.no/sak</link><pubDate>Mon, 03 Aug 2026 10:00:00 +0200</pubDate></item>
<item><title>Uten dato</title><link>https://firma.no/nyheter/uten</link></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Publisert</title><link rel="alternate" href="/a"/><published>2026-07-01T08:00:00Z</published></entry>
<entry><title>Bare oppdatert</title><link href="/b"/><updated>2026-07-02T08:00:00Z</updated></entry></feed>"""
SITEMAP_NEWS = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
<url><loc>https://firma.no/nyheter/x</loc><news:news><news:publication_date>2026-06-01</news:publication_date><news:title>Sitemap-nyhet</news:title></news:news></url>
<url><loc>https://firma.no/nyheter/y</loc><lastmod>2026-06-02</lastmod></url></urlset>"""


def test_feed_parsing_requires_title_link_and_publication_date():
    rss = parse_feed(RSS, "https://firma.no/feed/", now=NOW)
    assert [item["title"] for item in rss] == ["Ny fabrikk i dag", "Ekstern sak"]  # future and undated items dropped
    assert rss[0]["published_at"] == "2026-09-01"
    atom = parse_feed(ATOM, "https://firma.no/feed.atom", now=NOW)
    assert atom == [{"title": "Publisert", "url": "https://firma.no/a", "published_at": "2026-07-01",  # updated-only skipped
                     "claim_span": "<title>Publisert</title> <published>2026-07-01T08:00:00Z</published>"}]
    children, news, plain = parse_sitemap(SITEMAP_NEWS, now=NOW)
    assert news == [{"title": "Sitemap-nyhet", "url": "https://firma.no/nyheter/x", "published_at": "2026-06-01",
                     "claim_span": "<loc>https://firma.no/nyheter/x</loc> <news:title>Sitemap-nyhet</news:title> "
                                   "<news:publication_date>2026-06-01</news:publication_date>"}]
    assert plain == [("https://firma.no/nyheter/y", "2026-06-02")] and children == []
    assert parse_date("2099-01-01", now=NOW) is None and parse_date("1990-01-01", now=NOW) is None


def test_listing_and_article_metadata():
    page = BeautifulSoup("""<main><article><h2><a href="/nyheter/a">Vi åpner nytt kontor</a></h2><time datetime="2026-05-02">2. mai</time></article>
    <div><a href="/nyheter/b">Ny daglig leder ansatt</a><time datetime="2026-04-01T10:00">1. april</time></div>
    <div><a href="/x">Første</a><a href="/y">Andre</a><time datetime="2026-03-01">mars</time></div>
    <footer><a href="/nyheter/c">Footer-lenke her</a><time datetime="2026-01-01"></time></footer></main>""", "lxml")
    items = news_listing_items("https://firma.no/nyheter", page)
    assert [(item["title"], item["url"]) for item in items] == [("Vi åpner nytt kontor", "https://firma.no/nyheter/a"),
                                                                 ("Ny daglig leder ansatt", "https://firma.no/nyheter/b")]
    title, date = article_metadata('<meta property="og:title" content="Artikkel"><meta property="article:modified_time" content="2026-02-02">'
                                   '<script type="application/ld+json">{"datePublished":"2026-01-15"}</script>', now=NOW)
    assert (title, date) == ("Artikkel", "2026-01-15")
    assert article_metadata('<h1>Kun endret</h1><meta property="article:modified_time" content="2026-02-02">', now=NOW)[1] is None


def gated_profile(feed_links: list[str] | None = None, publishable: bool = True) -> dict:
    return {"organisation_number": "923609016", "evidence": {"website": evidence("website", "available", "registry_linked_company_website",
            "https://firma.no/", value={"final_url": "https://firma.no/", "feed_links": feed_links or [], "pages": [],
                                        "identity_assessment": {"status": "exact" if publishable else "review", "publishable": publishable,
                                                                "reasons": ["exact organisation number"]}})}}


def fake_fetcher(responses: dict[str, bytes], calls: list[str]):
    def fetch(url: str) -> dict:
        calls.append(url)
        if url not in responses:
            return {"error": "HTTP 404", "requests": 1}
        return {"body": responses[url], "final_url": url, "content_sha256": "d" * 64, "retrieved_at": "2026-10-20T00:00:00Z",
                "requests": 1, "bytes": len(responses[url])}
    return fetch


def test_dated_news_from_feed_keeps_only_exact_site_items_and_builds_claims():
    calls: list[str] = []
    record, metrics = fetch_dated_news(gated_profile(["https://firma.no/feed/", "https://cdn.other.no/feed"]),
                                       fake_fetcher({"https://firma.no/feed/": RSS}, calls), now=NOW, sitemaps_for=lambda url: [])
    assert record["status"] == "available" and calls == ["https://firma.no/feed/"] and metrics["requests"] == 1
    items = record["value"]["items"]
    assert [item["url"] for item in items] == ["https://firma.no/nyheter/fabrikk"]  # annet.no item rejected
    assert items[0]["company_match"]["verified_site"] == "https://firma.no/"
    profile = make_profile(7, records=[account(2025)], website=True)
    profile["evidence"]["dated_news"] = record
    env = envelope(profile)
    news_claims = [claim for claim in env["claims"] if claim["field"] == "dated_news"]
    assert len(news_claims) == 1 and news_claims[0]["availability"] == "available"
    assert news_claims[0]["value"] == {"title": "Ny fabrikk i dag", "url": "https://firma.no/nyheter/fabrikk",
                                       "published_at": "2026-09-01", "source_kind": "rss"}
    ev = next(item for item in env["evidence"] if item["id"] == news_claims[0]["evidence_ids"][0])
    assert ev["item_url"] == "https://firma.no/nyheter/fabrikk" and ev["published_at"] == "2026-09-01" and ev["source_url"] == "https://firma.no/feed/"


def test_dated_news_sitemap_fallback_and_request_cap():
    calls: list[str] = []
    index = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://firma.no/page-sitemap.xml</loc></sitemap>
    <sitemap><loc>https://firma.no/post-sitemap.xml</loc></sitemap></sitemapindex>"""
    posts = "".join(f"<url><loc>https://firma.no/nyheter/sak-{n}</loc><lastmod>2026-0{n}-01</lastmod></url>" for n in range(1, 9))
    responses = {"https://firma.no/sitemap.xml": index,
                 "https://firma.no/post-sitemap.xml": f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{posts}</urlset>'.encode()}
    for n in range(1, 9):
        responses[f"https://firma.no/nyheter/sak-{n}"] = f'<h1>Sak nummer {n}</h1><meta property="article:published_time" content="2026-0{n}-01">'.encode()
    record, metrics = fetch_dated_news(gated_profile(), fake_fetcher(responses, calls), now=NOW, sitemaps_for=lambda url: [])
    assert metrics["requests"] <= MAX_REQUESTS and len(calls) <= MAX_REQUESTS
    assert calls[:2] == ["https://firma.no/sitemap.xml", "https://firma.no/post-sitemap.xml"]
    assert record["status"] == "available"
    assert [item["title"] for item in record["value"]["items"]] == ["Sak nummer 8", "Sak nummer 7"]  # newest first
    assert all(item["source_kind"] == "sitemap_article" for item in record["value"]["items"])


def test_unverified_site_gets_no_news_and_no_requests():
    calls: list[str] = []
    record, metrics = fetch_dated_news(gated_profile(["https://firma.no/feed/"], publishable=False),
                                       fake_fetcher({"https://firma.no/feed/": RSS}, calls), now=NOW)
    assert record["status"] == "not_found" and calls == [] and metrics["requests"] == 0


# --- static site -----------------------------------------------------------------------------------------------------

def synthetic_envelopes(count: int) -> list[dict]:
    envelopes = []
    for index in range(count):
        kind = index % 5
        records = None if kind == 0 else [account(2025)] if kind == 1 else [account(2023), account(2024), account(2025, revenue=None)] \
            if kind == 2 else [account(2024), account(2025)]
        news = [("Nyhet om oss", "2026-08-0" + str(1 + index % 9))] if kind == 3 else None
        name = f"Ærlig <script>alert(1)</script> Firma {index} AS" if index == 7 else None
        profile = make_profile(index, records=records, website=kind in {3, 4}, news=news, deleted=index % 37 == 0, name=name)
        if index == 8:  # a hostile link must never become a clickable javascript: URL
            profile["evidence"]["website"]["value"]["final_url"] = "javascript:alert(1)"
        envelopes.append(envelope(profile))
    return envelopes


def check_site(out: Path, envelopes: list[dict]) -> None:
    manifest = json.loads((out / "site-manifest.json").read_text())
    assert manifest["companies"] == manifest["company_pages"] == manifest["with_summary"] == len(envelopes)
    index = (out / "index.html").read_text()
    assert index.count('<li class="row"') == len(envelopes)
    assert 'id="q"' in index and "organisation number" in index and 'name="viewport"' in index
    assert (out / "compare.html").exists() and "SP_COMPANIES" in (out / "assets" / "companies.js").read_text()
    assert len(list((out / "companies").glob("*.html"))) == len(envelopes) == len(list((out / "data").glob("*.js")))
    for env in envelopes[:60]:
        page = (out / "companies" / f"{env['organisation_number']}.html").read_text()
        assert "<script>alert(1)</script>" not in page and 'href="javascript:' not in page
        assert "None" not in re.sub(r"<[^>]+>", " ", page) and "nan" not in page.split("<body>")[1].split()
        assert 'class="summary"' in page and "ev-registry-1" in page
        rows = re.findall(r"<tr><td>[^<]+</td><td[^>]*>.*?</td><td>(.*?)</td></tr>", page.split("<tbody>")[1].split("</tbody>")[0])
        assert rows and all("source ↗" in row and ("retrieved" in row or "as of" in row) for row in rows)
        data = (out / "data" / f"{env['organisation_number']}.js").read_text()
        payload = json.loads(data[data.index(",{") + 1:data.rindex(");")])
        assert all(fact["src"] and (fact["retrieved"] or fact["as_of"]) for fact in payload["facts"])


def test_site_builds_on_300_profiles(tmp_path):
    envelopes = synthetic_envelopes(300)
    build_site(envelopes, tmp_path / "site", {"run_id": "t300", "completed_at": "2026-10-20T00:01:00Z"})
    check_site(tmp_path / "site", envelopes)
    news_page = (tmp_path / "site" / "companies" / f"{envelopes[3]['organisation_number']}.html").read_text()
    assert "Nyhet om oss" in news_page and "published 2026-08-04" in news_page


def test_site_builds_on_1000_profiles(tmp_path):
    envelopes = synthetic_envelopes(1000)
    manifest = build_site(envelopes, tmp_path / "site", {"run_id": "t1000", "completed_at": "2026-10-20T00:01:00Z"})
    assert manifest["companies"] == 1000
    check_site(tmp_path / "site", envelopes)


def test_site_rejects_envelope_without_org_number(tmp_path):
    with pytest.raises(ValueError):
        build_site([{"organisation_number": "12"}], tmp_path / "site")
