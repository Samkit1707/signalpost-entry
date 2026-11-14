"""Coverage: more verified news, job postings, sub-unit sites, socials from contact pages. Identity rules unchanged:
every external link needs the exact org number or the registry's own website entry; the envelope carries only verified
material."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import terminal_envelope  # noqa: E402
from norway_company_agent.domain_guess import discover_website, website_candidates  # noqa: E402
from norway_company_agent.enrichment import company_jobs_record, external_profiles_record  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate, assess_social_identity  # noqa: E402
from norway_company_agent.news import archive_links  # noqa: E402
from norway_company_agent.official import normalize_locations  # noqa: E402
from norway_company_agent.website import ats_list_links, careers_listing_items, labelled_dates  # noqa: E402

ORG = "923609016"


def verified(pages: list[dict] | None = None, **extra) -> dict:
    return evidence("website", "available", "registry_linked_company_website", "https://firma.no/", content_sha256="b" * 64, value={
        "final_url": "https://firma.no/", "registered_domain": "firma.no", "title": "Firma AS", "content_sha256": "b" * 64,
        "identity_text_excerpt": f"Firma AS org.nr {ORG}", "main_text_excerpt": "Firma AS lager ting. " * 10,
        "structured_organisations": [], "pages": pages or [], **extra})


def test_archive_links_find_next_page_and_archive():
    soup = BeautifulSoup('<a href="/nyheter?page=2">Neste side</a><a href="/nyheter/arkiv">Nyhetsarkiv</a>'
                         '<a rel="next" href="/nyheter/side/2/">»</a><a href="/kontakt">Kontakt</a>'
                         '<a href="https://other.no/nyheter?page=2">Neste</a>', "lxml")
    assert archive_links("https://firma.no/nyheter", soup) == [
        "https://firma.no/nyheter?page=2", "https://firma.no/nyheter/arkiv", "https://firma.no/nyheter/side/2/"]


def test_job_ads_with_labelled_dates_and_ats_list_links():
    assert labelled_dates("Prosjektleder · Publisert 03.11.2026 · Søknadsfrist: 01.12.2026") == {
        "date_posted": "2026-11-03", "valid_through": "2026-12-01"}
    assert labelled_dates("Prosjektleder 03.11.2026") == {}  # an unlabelled date is not used
    html = ('<main><ul><li><a href="/karriere/prosjektleder">Prosjektleder bygg</a> <span>Publisert 03.11.2026</span></li>'
            '<li><a href="/karriere/tomrer">Tømrer med fagbrev</a> <span>Søknadsfrist 1.12.2026</span></li></ul>'
            '<a href="https://firma.teamtailor.com/jobs">Se alle stillinger hos oss</a>'
            '<a href="https://firma.teamtailor.com/jobs/4567890-elektriker">Elektriker</a></main>')
    soup = BeautifulSoup(html, "lxml")
    items = {item["title"]: item for item in careers_listing_items("https://firma.no/karriere", soup, html)}
    assert items["Prosjektleder bygg"]["date_posted"] == "2026-11-03" and "date_posted" not in items["Tømrer med fagbrev"]
    assert items["Tømrer med fagbrev"]["valid_through"] == "2026-12-01" and items["Elektriker"]["kind"] == "ats_listing"
    assert ats_list_links("https://firma.no/karriere", soup) == ["https://firma.teamtailor.com/jobs"]


def test_each_ats_list_ad_is_its_own_job_claim_and_the_careers_page_stays_separate():
    careers = {"url": "https://firma.no/karriere", "title": "Karriere", "category": "careers", "content_sha256": "c" * 64, "listed_jobs": []}
    record = verified([careers], identity_assessment={"status": "exact", "score": 1.0, "publishable": True, "reasons": ["exact organisation number"]},
                      ats_jobs={"list_url": "https://firma.teamtailor.com/jobs", "linked_from": "https://firma.no/karriere", "content_sha256": "d" * 64,
                                "jobs": [{"title": "Elektriker", "url": "https://firma.teamtailor.com/jobs/4567890-elektriker", "kind": "ats_listing",
                                          "date_posted": "2026-11-02",
                                          "claim_span": 'href="https://firma.teamtailor.com/jobs/4567890-elektriker">Elektriker'},
                                         {"title": "Lærling", "url": "https://firma.teamtailor.com/jobs/4567891-laerling", "kind": "ats_listing",
                                          "claim_span": 'href="https://firma.teamtailor.com/jobs/4567891-laerling">Lærling'}]})
    profile = {"organisation_number": ORG, "name": "FIRMA AS", "evidence": {"website": record}}
    profile["evidence"]["company_jobs"] = company_jobs_record(profile)
    env = terminal_envelope(profile, run_id="t", modules=["website", "company_jobs"], started_at="s", completed_at="c")
    jobs = [c for c in env["claims"] if c["field"] == "job_posting" and c["availability"] == "available"]
    assert [(c["value"]["title"], c["value"]["published_date"]) for c in jobs] == [("Elektriker", "2026-11-02"), ("Lærling", None)]
    assert all("teamtailor" in c["value"]["company_match"] and "verified company site" in c["value"]["company_match"] for c in jobs)
    careers_claim = next(c for c in env["claims"] if c["field"] == "careers_page")
    assert careers_claim["availability"] == "available" and careers_claim["value"]["open_postings"] == 2
    assert env["link_guard"]["removed"] == 0


def subunit_profile(website: str) -> dict:
    body = {"_embedded": {"underenheter": [{"organisasjonsnummer": "912345678", "navn": "FIRMA AS AVD BERGEN", "hjemmeside": website,
                                             "beliggenhetsadresse": {"adresse": ["Strandkaien 2"], "postnummer": "5013", "poststed": "BERGEN",
                                                                     "kommune": "BERGEN"}}]}, "page": {"totalElements": 1}}
    locations = evidence("locations", "available", "official_subunits", "https://data.brreg.no/u", value=normalize_locations(body),
                         content_sha256="e" * 64)
    return {"organisation_number": ORG, "name": "FIRMA AS", "evidence": {"locations": locations}}


def test_subunit_registry_website_is_a_candidate_needing_the_parents_org_number():
    profile = subunit_profile("www.firmabergen.no")
    assert website_candidates(profile)[0] == {"domain": "firmabergen.no", "method": "subunit_registry_website"}

    def site(url, footer):
        return lambda u, **_: (evidence("website", "available", _.get("source_type"), u, value={
            "requested_url": u, "final_url": u, "title": "Firma Bergen", "identity_text_excerpt": footer, "pages": [],
            "main_text_excerpt": "Firma Bergen " * 10, "structured_organisations": []}), {"requests": 1, "bytes": 1, "latencies_ms": []})

    only_name = site("https://firmabergen.no/", "Firma AS avd. Bergen · Strandkaien 2, 5013 Bergen")
    accepted, _, trail = discover_website(profile, only_name, apply_website_identity_gate, resolver=lambda host: host == "firmabergen.no")
    assert accepted is None and trail[0]["method"] == "subunit_registry_website"
    with_org = site("https://firmabergen.no/", f"Firma AS · org.nr {ORG}")
    accepted, _, _ = discover_website(profile, with_org, apply_website_identity_gate, resolver=lambda host: host == "firmabergen.no")
    assert accepted and accepted["source_type"] == "subunit_registry_website_candidate"


def test_subunit_site_label_only_when_it_is_the_companys_verified_site():
    from norway_company_agent.public import diagnostics_record
    # unproven (not the verified site): no label, no trace in the envelope; the diagnostics file keeps it
    profile = subunit_profile("www.cetho.no")
    profile["evidence"]["website"] = evidence("website", "not_found", "registry_linked_company_website", "https://data.brreg.no/x")
    diagnostics = diagnostics_record(profile)
    env = terminal_envelope(profile, run_id="t", modules=["locations", "website"], started_at="s", completed_at="c")
    location = next(c for c in env["claims"] if c["field"] == "registered_location")
    assert location["availability"] == "available" and "subunit_website" not in location["value"]
    assert "cetho" not in json.dumps(env) and env["link_guard"]["removed"] == 0
    assert diagnostics["subunit_sites_not_published"][0]["website"] == "www.cetho.no"
    # the same registered domain as the verified website: labelled on the location claim
    same = subunit_profile("www.firma.no")
    same["evidence"]["website"] = verified(identity_assessment={"status": "exact", "score": 1.0, "publishable": True, "reasons": ["org"]})
    env = terminal_envelope(same, run_id="t", modules=["locations", "website"], started_at="s", completed_at="c")
    location = next(c for c in env["claims"] if c["field"] == "registered_location")
    assert location["value"]["subunit_website"] == "www.firma.no" and "same domain" in location["value"]["subunit_website_note"]
    assert next(c for c in env["claims"] if c["field"] == "official_website")["value"] == "https://firma.no/"


def test_profiles_from_a_contact_page_body_count_with_the_same_handle_rules():
    profile = {"organisation_number": ORG, "name": "FIRMA AS"}
    own = assess_social_identity(profile, {"platform": "facebook", "url": "https://facebook.com/firmaas", "location": "contact_page"}, ["firma"])
    assert own["publishable"]
    vendor = assess_social_identity(profile, {"platform": "facebook", "url": "https://facebook.com/wix", "location": "contact_page"}, ["firma"])
    other = assess_social_identity(profile, {"platform": "facebook", "url": "https://facebook.com/naboenbakeri", "location": "contact_page"}, ["firma"])
    body = assess_social_identity(profile, {"platform": "facebook", "url": "https://facebook.com/firmaas", "location": "body"}, ["firma"])
    assert not vendor["publishable"] and not other["publishable"] and not body["publishable"]
    link = {"platform": "facebook", "url": "https://facebook.com/firmaas", "page_url": "https://firma.no/kontakt", "content_sha256": "f" * 64,
            "raw": 'href="https://www.facebook.com/firmaas"', "location": "contact_page"}
    site = {"organisation_number": ORG, "name": "FIRMA AS", "evidence": {}}
    site["evidence"]["website"] = apply_website_identity_gate(site, verified(social_links=[link]))["website"]
    (item,) = external_profiles_record(site, None)["value"]["profiles"]
    assert item["proof"] == "linked from https://firma.no/kontakt contact page"


def test_news_needs_a_news_section_or_jsonld_article_type():
    from norway_company_agent.news import jsonld_news_type, news_segment
    assert news_segment("https://musikkinnlandet.no/nytt/innpuls-2028/") == "nytt"
    assert news_segment("https://musikkinnlandet.no/innblikk/kronikk-mister-vi-fotfeste-na/") == "innblikk"
    assert news_segment("https://firma.no/aktuelt/2026/ny-fabrikk") == "aktuelt"
    assert news_segment("https://www.aspecto.no/post/et-varsko-for-akvakulturforvaltningen-1") == "post"
    for url in ("https://novooi.com/london/", "https://novooi.com/hospitality-at-work/", "https://novooi.com/design-dialogues/",
                "https://firma.no/nyheter", "https://firma.no/newsletter-signup/", "https://firma.no/postkasse"):
        assert news_segment(url) is None, url
    assert jsonld_news_type('<script type="application/ld+json">{"@type": "BlogPosting", "headline": "x"}</script>') == "JSON-LD BlogPosting"
    assert jsonld_news_type('<script type="application/ld+json">{"@graph": [{"@type": ["NewsArticle"]}]}</script>') == "JSON-LD NewsArticle"
    assert jsonld_news_type('<meta property="og:type" content="article">') is None  # og:type alone is not enough
    assert jsonld_news_type('<script type="application/ld+json">{"@type": "WebPage"}</script>') is None


def test_root_level_post_with_only_a_generic_article_type_is_not_news():
    from datetime import datetime, timezone
    from norway_company_agent.news import fetch_dated_news

    def page(kind: str, topic: str) -> bytes:
        body = f"Firma AS inviterer til {topic}. " + f"Programmet om {topic} samler arkitekter og designere fra hele landet. " * 4
        return (f'<html><head><meta property="og:title" content="Design Dialogues"><meta property="article:published_time" '
                f'content="2026-09-07T13:29:49+00:00"><script type="application/ld+json">{{"@type": "{kind}"}}</script></head>'
                f'<body><main><article><p>{body}</p></article></main></body></html>').encode()

    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Design Dialogues</title><link>https://firma.no/design-dialogues/</link><pubDate>Mon, 07 Sep 2026 13:29:49 +0000</pubDate></item>
<item><title>London</title><link>https://firma.no/london/</link><pubDate>Mon, 07 Sep 2026 13:29:49 +0000</pubDate></item>
<item><title>Designuka</title><link>https://firma.no/post/designuka/</link><pubDate>Mon, 07 Sep 2026 13:29:49 +0000</pubDate></item>
</channel></rss>"""
    responses = {"https://firma.no/feed/": feed, "https://firma.no/design-dialogues/": page("Article", "dialoger om design"),
                 "https://firma.no/london/": page("BlogPosting", "en utstilling i London"),
                 "https://firma.no/post/designuka/": page("Article", "designuka i Oslo")}

    def fetch(url):
        body = responses.get(url)
        return {"url": url, "final_url": url, "body": body, "requests": 1, "bytes": len(body or b""), "content_sha256": "a" * 64,
                "retrieved_at": "2026-11-14T00:00:00Z", "error": None if body else "HTTP 404"}

    site = evidence("website", "available", "registry_linked_company_website", "https://firma.no/", content_sha256="b" * 64, value={
        "final_url": "https://firma.no/", "registered_domain": "firma.no", "content_sha256": "b" * 64, "pages": [],
        "feed_links": ["https://firma.no/feed/"],
        "identity_assessment": {"status": "exact", "score": 1.0, "publishable": True, "reasons": ["org"]}})
    record, _ = fetch_dated_news({"organisation_number": ORG, "name": "FIRMA AS", "evidence": {"website": site}}, fetch,
                                 now=datetime(2026, 11, 14, tzinfo=timezone.utc), sitemaps_for=lambda url: [])
    kept = {item["url"] for item in (record.get("value") or {}).get("items") or []}
    rejected = {item["url"]: item["reason"] for item in ((record.get("value") or record.get("checked") or {}).get("not_published") or [])}
    assert kept == {"https://firma.no/london/", "https://firma.no/post/designuka/"}
    assert "generic Article counts only under a news/blog section" in rejected["https://firma.no/design-dialogues/"]
