"""v6 (Builderr v5 feedback: 15 of 73 checked claims were about another company).

Both examples came through the envelope's diagnostics, not a published claim: ISABELLA CAFE AS's rejected name-variant
candidate isabellacafe.com (a US cafe) sat in `profile.website_discovery.candidates[].final_url` with "all legal-name
tokens appear", and LØWEHJERTE DESIGN AS's Squarespace template sameAs (twitter.com/squarespace) sat in the raw website
record and the claims' `not_published` lists. v6 removes every unverified link from what leaves the agent and tightens
the website and social rules themselves.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import terminal_envelope  # noqa: E402
from norway_company_agent.domain_guess import discover_website  # noqa: E402
from norway_company_agent.enrichment import company_jobs_record, external_profiles_record  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.identity import (apply_website_identity_gate, assess_social_identity, assess_website_identity,  # noqa: E402
                                           foreign_country_signals)
from norway_company_agent.public import diagnostics_record, guard_urls  # noqa: E402
from norway_company_agent.synthesis import build_synthesis, headline, short_money  # noqa: E402
from norway_company_agent.website import _social_links, careers_listing_items, structured_social_links  # noqa: E402

ORG = "932415313"
MODULES = ["website", "external_profiles", "company_jobs"]


def us_cafe(url: str = "https://www.isabellacafe.com/", source_type: str = "name_derived_domain_candidate", footer: str = "") -> dict:
    return evidence("website", "available", source_type, url, content_sha256="a" * 64, value={
        "requested_url": url, "final_url": url, "registered_domain": "isabellacafe.com",
        "title": "Home | Isabella's Cafe Italian Restaurant  | Tinley Park, IL", "description": "",
        "identity_text_excerpt": "Follow Us Facebook Instagram Yelp © 2023 by Isabellas. Proudly Designed by Rokt Media " + footer,
        "main_text_excerpt": "Isabella's Café opened its doors in 2001 serving Northern Italian food in the heart of Tinley Park." * 3,
        "structured_organisations": [{"@type": "Restaurant", "name": "Isabella's Cafe", "address": {
            "@type": "PostalAddress", "addressCountry": "US", "addressRegion": "IL", "postalCode": "60477-3401"}}],
        "content_sha256": "a" * 64, "pages": []})


def cafe_profile() -> dict:
    return {"organisation_number": ORG, "name": "ISABELLA CAFE AS", "evidence": {}}


def test_isabella_us_cafe_is_rejected_by_foreign_signals_and_the_unlisted_domain_rule():
    profile = {**cafe_profile(), "evidence": {"website": us_cafe()}}
    assessment = assess_website_identity(profile)
    assert not assessment["publishable"]
    assert any("US" in signal for signal in assessment["foreign_country_signals"])
    assert any("another country" in reason for reason in assessment["reasons"])
    # even registry-listed, a US address/title without the org number is not this company
    listed = {**cafe_profile(), "evidence": {"website": us_cafe(source_type="registry_linked_company_website")}}
    assert not assess_website_identity(listed)["publishable"]
    # only the printed org number overrides the foreign signals
    proven = {**cafe_profile(), "evidence": {"website": us_cafe(footer="Org.nr 932 415 313")}}
    assert assess_website_identity(proven)["publishable"]


def test_foreign_country_signals():
    assert foreign_country_signals({"title": "Joe's Pizza | Austin, TX"})
    assert foreign_country_signals({"identity_text_excerpt": "123 Main St, Springfield, IL 62701"})
    assert foreign_country_signals({"identity_text_excerpt": "Call us +1 (312) 555-0100"})
    assert foreign_country_signals({"description": "Family bakery in the USA since 1990"})
    assert foreign_country_signals({"structured_organisations": [{"address": {"addressCountry": "SE"}}]})
    # Norwegian sites stay clean, including exporters that mention the US in body text and +47 numbers
    assert not foreign_country_signals({"title": "Fjord Bakeri AS | Sandnes", "identity_text_excerpt": "Strandgata 21, 4307 Sandnes · +47 51 68 57 00",
                                        "structured_organisations": [{"address": {"addressCountry": "NO"}}],
                                        "main_text_excerpt": "Vi eksporterer laks til Japan og USA."})


def test_unlisted_domains_need_the_printed_org_number():
    footer = "Isabella Cafe AS · Stabells gate 13 · 3510 Hønefoss"
    norwegian = lambda url, **_: (evidence("website", "available", "name_derived_domain_candidate", url, value={  # noqa: E731
        "requested_url": url, "final_url": url, "title": "Isabella Cafe AS", "identity_text_excerpt": footer,
        "main_text_excerpt": "Isabella Cafe AS " * 20, "pages": [], "structured_organisations": []}), {"requests": 1, "bytes": 1, "latencies_ms": []})
    profile = cafe_profile()
    accepted, _, trail = discover_website(profile, norwegian, apply_website_identity_gate, resolver=lambda host: True)
    assert accepted is None and trail
    for source_type in ("wikidata_linked_company_website", "registry_email_domain_candidate"):
        record = norwegian("https://isabellacafe.no/")[0]
        record["source_type"] = source_type
        assert not assess_website_identity({**profile, "evidence": {"website": record}})["publishable"]
    record = norwegian("https://isabellacafe.no/")[0]
    record["source_type"] = "registry_linked_company_website"
    assert assess_website_identity({**profile, "evidence": {"website": record}})["publishable"]  # registry's own entry


def lowehjerte_site() -> tuple[dict, dict]:
    profile = {"organisation_number": "987832827", "name": "LØWEHJERTE DESIGN AS", "evidence": {}}
    same_as = structured_social_links([{"@type": "Organization", "name": "Squarespace", "sameAs": [
        "http://twitter.com/squarespace", "http://facebook.com/squarespace",
        "https://www.linkedin.com/company/squarespace/mycompany/verification/"]}], "https://www.lowehjerte.no/", "e" * 64)
    record = evidence("website", "available", "registry_linked_company_website", "https://www.lowehjerte.no/", content_sha256="e" * 64, value={
        "requested_url": "https://www.lowehjerte.no/", "final_url": "https://www.lowehjerte.no/", "registered_domain": "lowehjerte.no",
        "title": "Løwehjerte design", "identity_text_excerpt": "Løwehjerte design · Mørkridsvegen 35, 6876 Skjolden",
        "main_text_excerpt": "Løwehjerte design lager grafisk design. " * 5, "content_sha256": "e" * 64, "pages": [],
        "structured_organisations": [{"@type": "Organization", "name": "Squarespace", "sameAs": ["http://twitter.com/squarespace"]}],
        "social_links": same_as})
    profile["evidence"]["website"] = apply_website_identity_gate(profile, record)["website"]
    return profile, record


def test_lowehjerte_squarespace_template_profiles_never_reach_the_envelope():
    profile, _ = lowehjerte_site()
    assert profile["evidence"]["website"]["value"]["identity_assessment"]["publishable"]
    profile["evidence"]["external_profiles"] = external_profiles_record(profile, None)
    profile["evidence"]["company_jobs"] = company_jobs_record(profile)
    env = terminal_envelope(profile, run_id="t", modules=MODULES, started_at="s", completed_at="c")
    text = json.dumps(env, ensure_ascii=False)
    assert "squarespace" not in text.casefold()
    assert next(c for c in env["claims"] if c["field"] == "official_website")["value"] == "https://www.lowehjerte.no/"
    assert env["link_guard"]["removed"] == 0  # removed structurally, not by the last-line guard
    reasons = [item["reason"] for item in diagnostics_record(profile)["rejected_links"]]
    assert reasons and all("platform/vendor account" in reason for reason in reasons)


def test_vendor_and_credit_links_are_not_company_profiles():
    profile = {"name": "Fjord Bakeri AS"}
    for url in ("https://facebook.com/squarespace", "https://instagram.com/wix", "https://x.com/WordPressdotcom",
                "https://linkedin.com/company/shopify", "https://facebook.com/onecom", "https://facebook.com/facebook"):
        result = assess_social_identity(profile, {"platform": "x", "url": url, "location": "footer"}, ["fjordbakeri"])
        assert not result["publishable"] and "vendor" in result["reason"], url
    soup = BeautifulSoup('<footer><a href="https://facebook.com/fjordbakeri">FB</a>'
                         '<p>Nettside levert av <a href="https://facebook.com/webbyraa">Webbyrå</a></p></footer>', "lxml")
    links = {item["url"]: item["location"] for item in _social_links("https://fjordbakeri.no/", soup)}
    assert links == {"https://facebook.com/fjordbakeri": "footer", "https://facebook.com/webbyraa": "credit"}
    credit = assess_social_identity(profile, {"platform": "facebook", "url": "https://facebook.com/fjordbakeri", "location": "credit"}, ["fjordbakeri"])
    assert not credit["publishable"]


def test_rejected_site_cascades_and_leaves_no_trace():
    profile = {**cafe_profile(), "website_discovery": {"candidates": [
        {"candidate": "isabellacafe.com", "method": "name_variant", "result": "available", "identity_score": 0.3,
         "final_url": "https://www.isabellacafe.com/", "reasons": ["all normalized legal-name tokens appear together"]}]}}
    profile["evidence"]["website"] = apply_website_identity_gate(profile, us_cafe(source_type="registry_linked_company_website"))["website"]
    profile["evidence"]["external_profiles"] = external_profiles_record(profile, None)
    profile["evidence"]["company_jobs"] = company_jobs_record(profile)
    env = terminal_envelope(profile, run_id="t", modules=MODULES, started_at="s", completed_at="c")
    assert "isabellacafe" not in json.dumps(env).casefold()
    assert env["profile"]["website_discovery"] == {"candidates_checked": 1, "published_candidate": None}
    assert not [c for c in env["claims"] if c["availability"] == "available" and c["field"] in
                {"official_website", "external_profile", "social_profiles", "careers_page", "job_posting", "site_address"}]
    assert {item["url"] for item in diagnostics_record(profile)["rejected_sites"]} == {"https://www.isabellacafe.com/"}


def test_guard_removes_stray_links_and_keeps_verified_ones():
    env = {"profile": {"evidence": {"website": {"status": "available", "value": {
        "final_url": "https://firma.no/", "registered_domain": "firma.no", "identity_assessment": {"publishable": True}}}}},
        "claims": [{"field": "external_profile", "availability": "available", "value": {"url": "https://facebook.com/firma"}, "evidence_ids": ["e1"]},
                   {"field": "dated_news", "availability": "available", "value": {"url": "https://other.no/nyhet", "title": "x"}, "evidence_ids": ["e2"]}],
        "evidence": [{"id": "e1", "source_url": "https://firma.no/", "claim_span": 'href="https://www.facebook.com/firma"'},
                     {"id": "e2", "source_url": "https://other.no/nyhet", "claim_span": "x"}],
        "synthesis": {"narrative": "See https://firma.no/om and isabellacafe.com or post@firma.no"}}
    removed = guard_urls(env)
    assert {item["text"] for item in removed} >= {"https://other.no/nyhet", "isabellacafe.com"}
    assert env["claims"][0]["availability"] == "available" and env["claims"][1]["availability"] == "not_available"
    assert [item["id"] for item in env["evidence"]] == ["e1"]
    assert "https://firma.no/om" in env["synthesis"]["narrative"] and "isabellacafe" not in env["synthesis"]["narrative"]
    assert "post@firma.no" in env["synthesis"]["narrative"]


def test_careers_page_listed_jobs_become_job_posting_claims():
    html = ('<html><body><nav><a href="/karriere/om">Om karriere hos oss</a></nav><main>'
            '<a href="/karriere/prosjektleder-bygg">Prosjektleder bygg, Oslo</a>'
            '<a href="https://firma.webcruiter.no/Main2/Recruit/Public/4567890">Tømrer med fagbrev</a>'
            '<a href="/karriere/apen-soknad">Åpen søknad</a><a href="/kontakt">Kontakt oss</a></main></body></html>')
    soup = BeautifulSoup(html, "lxml")
    items = careers_listing_items("https://firma.no/karriere", soup, html)
    assert [(item["title"], item["kind"]) for item in items] == [("Prosjektleder bygg, Oslo", "site_job_page"), ("Tømrer med fagbrev", "ats_listing")]
    profile = {"organisation_number": "923609016", "name": "FIRMA AS", "evidence": {"website": evidence(
        "website", "available", "registry_linked_company_website", "https://firma.no/", content_sha256="b" * 64, value={
            "final_url": "https://firma.no/", "registered_domain": "firma.no", "title": "Firma AS", "content_sha256": "b" * 64,
            "identity_assessment": {"status": "exact", "score": 1.0, "publishable": True, "reasons": ["exact organisation number"]},
            "pages": [{"url": "https://firma.no/karriere", "title": "Karriere", "category": "careers", "content_sha256": "c" * 64,
                       "listed_jobs": items}]})}}
    profile["evidence"]["company_jobs"] = company_jobs_record(profile)
    env = terminal_envelope(profile, run_id="t", modules=["website", "company_jobs"], started_at="s", completed_at="c")
    jobs = [c for c in env["claims"] if c["field"] == "job_posting" and c["availability"] == "available"]
    assert [c["value"]["title"] for c in jobs] == ["Prosjektleder bygg, Oslo", "Tømrer med fagbrev"]
    assert all(c["value"]["url"] and "published_date" in c["value"] and "verified company site" in c["value"]["company_match"] for c in jobs)
    careers = next(c for c in env["claims"] if c["field"] == "careers_page")
    assert careers["value"]["open_postings"] == 2


def test_summary_opens_with_a_buyer_relevant_fact_and_its_source():
    latest = {"period": {"tilDato": "2025-12-31"}, "revenue": 17_400_000.0, "currency": "NOK", "account_type": "SELSKAP"}
    previous = {"period": {"tilDato": "2024-12-31"}, "revenue": 17_940_000.0, "currency": "NOK", "account_type": "SELSKAP"}
    claim = {"field": "latest_annual_accounts", "availability": "available", "value": {}, "evidence_ids": ["ev-financials-1"]}
    text, sources = headline([claim], latest, previous)
    assert text == "Revenue NOK 17.4m in FY2025, down 3% from FY2024 (Brønnøysund accounts)." and sources == [claim]
    assert short_money(912_345) == "NOK 912k" and short_money(-2_919_486) == "NOK -2.9m"
    status = {"field": "registry_status", "availability": "available", "value": {"bankrupt": True}, "evidence_ids": ["ev-registry_live-1"]}
    assert headline([status, claim], latest, previous)[0] == "Registered as bankrupt (Brønnøysund register)."
    profile = {"organisation_number": ORG, "name": "ISABELLA CAFE AS", "evidence": {"financials": {"value": {"records": [latest, previous]}}}}
    claims = [claim, {"field": "legal_name", "availability": "available", "value": "ISABELLA CAFE AS", "evidence_ids": ["ev-registry-1"]}]
    synthesis = build_synthesis(profile, claims, [{"id": "ev-financials-1"}, {"id": "ev-registry-1"}])
    assert synthesis["sentences"][0]["topic"] == "headline" and synthesis["headline"].startswith("Revenue NOK 17.4m")
    assert synthesis["sentences"][1]["text"].startswith("ISABELLA CAFE AS (org. no. 932 415 313)")


def test_policy_pages_are_not_news_and_job_board_anchors_are_not_titles():
    from norway_company_agent.news import not_article_reason
    assert "policy" in not_article_reason("https://ecceracare.no/redegjorelse-etter-apenhetsloven/")
    assert "policy" in not_article_reason("https://ecceracare.no/likestillingsredgjorelse-2024/")
    assert not_article_reason("https://ixys.no/ixys-receives-the-gaselle-award-for-the-ninth-consecutive-year/") is None
    html = '<main><a href="https://www.finn.no/job/ad/477206529">finn.no</a><a href="https://www.finn.no/job/ad/1">Elektronikkingeniør</a></main>'
    items = careers_listing_items("https://ixys.no/careers/", BeautifulSoup(html, "lxml"), html)
    assert [item["title"] for item in items] == ["Elektronikkingeniør"]


def test_careers_category_needs_a_whole_word():
    from norway_company_agent.website import page_category
    assert page_category("https://www.insbo.no/raadgivning/annet/faa-jobben-gjort", "Få jobben gjort - Insbo.no") is None
    for url, title in (("https://firma.no/karriere/", ""), ("https://firma.no/ledige-stillinger", ""), ("https://ixys.no/careers/", "Careers"),
                       ("https://firma.no/om/jobb-hos-oss", ""), ("https://firma.no/side", "Ledige stillinger")):
        assert page_category(url, title) == "careers", url
