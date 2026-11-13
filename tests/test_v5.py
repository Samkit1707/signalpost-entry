"""v5: social handles match the legal name or the verified site's brand, only from header/footer/JSON-LD sameAs;
news is published only as an article with a publication date that names the company; every link has a confidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.confidence import link_report, website_confidence  # noqa: E402
from norway_company_agent.batch import terminal_envelope  # noqa: E402
from norway_company_agent.enrichment import external_profiles_record  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate, assess_social_identity, handle_slug, site_brands  # noqa: E402
from norway_company_agent.news import block_hash, company_mention, fetch_dated_news, not_article_reason, text_block_hashes  # noqa: E402
from norway_company_agent.site import _value_html, company_facts  # noqa: E402
from norway_company_agent.synthesis import why_it_matters  # noqa: E402
from norway_company_agent.website import _social_links  # noqa: E402


def social(name: str, url: str, domain: str, location: str = "footer") -> dict:
    return assess_social_identity({"name": name}, {"platform": "x", "url": url, "location": location},
                                  site_brands({"registered_domain": domain}))


def test_handle_matching_the_verified_sites_brand_publishes():
    # rejected by v4 (handle vs legal name only), all linked from the company's own verified site
    assert social("HJEMMEOPTIKK.NO AS", "https://facebook.com/hjemmeoptikk", "hjemmeoptikk.no")["confidence"] == "high"
    assert social("BYGG OG TØMMERMESTER PER-ERIK ØSTLIE AS", "https://facebook.com/pererikostlie", "pererikostlie.no")["confidence"] == "high"
    assert social("EFFEKTIV MARKETING SOLUTIONS AS", "https://linkedin.com/company/effektiv-marketing", "effektivmarketing.no")["confidence"] == "high"
    assert social("KONTORLEVERANDØREN GJØVIK AS", "https://instagram.com/kontorlev.no", "kontorlev.no")["confidence"] == "high"
    # LinkedIn drops ø from slugs
    assert social("LOVISENBERG DIAKONALE HØGSKOLE AS", "https://linkedin.com/company/lovisenberg-diakonale-h-gskole", "ldh.no")["confidence"] == "high"
    # only legal-name words and the brand: medium
    assert social("VOLLVAAGEN APARTMENTS SMØLA AS", "https://instagram.com/vollvaagen_apartments", "vollvaagen.no")["confidence"] == "medium"
    assert social("KONTORLEVERANDØREN GJØVIK AS", "https://facebook.com/kontorleverandorenas", "kontorlev.no")["confidence"] == "medium"


def test_handles_that_add_other_words_ids_or_people_are_not_published():
    assert not social("PRODERMA TROMSØ AS", "https://instagram.com/proderma.lillestrom", "proderma.no")["publishable"]  # another branch
    assert not social("SALTEN TRANSPORTSENTRAL AS", "https://facebook.com/TransportsentralenNORD", "sts.as")["publishable"]
    assert not social("ZIMM ARKITEKTER AS", "https://instagram.com/andreaspoulsson", "zimmark.no")["publishable"]  # a person
    assert not social("OSLO ENTREPRENØRBEDRIFT AS", "https://facebook.com/osloentreprenor", "oseb.no")["publishable"]
    assert not social("LOVISENBERG DIAKONALE HØGSKOLE AS", "https://facebook.com/lovisenbergDH", "ldh.no")["publishable"]
    numeric = social("TRUCK OG MASKIN AS", "https://facebook.com/412838828750179", "truckogmaskin.no")
    assert not numeric["publishable"] and "numeric" in numeric["reason"]
    assert handle_slug("https://youtube.com/channel/UCVzGwSOGZTLNOUROKgqNq7g") == ""
    # Facebook's automatic page URL: page name, then the page id (unseen-30: CONOW AS on conow.no)
    assert handle_slug("https://facebook.com/CONOW-1159100470769905") == "CONOW"
    assert social("CONOW AS", "https://facebook.com/CONOW-1159100470769905", "conow.no")["confidence"] == "high"
    # Facebook's newer page URL form (300 sample: JÆDER AS on jaeder.no); on Instagram /p/ is a post, never a profile
    assert handle_slug("https://facebook.com/p/J%C3%86DER-100034744493193") == "JÆDER"
    assert social("JÆDER AS", "https://facebook.com/p/JaeDER-100034744493193", "jaeder.no")["confidence"] == "high"
    assert not social("JÆDER AS", "https://instagram.com/p/jaeder", "jaeder.no")["publishable"]
    assert not social("CS NETTHANDEL AS", "https://facebook.com/Bilverksted-736715266357495", "autoroyal.no")["publishable"]
    assert not assess_social_identity({"name": "Aon Norway AS"}, {"platform": "linkedin", "url": "https://linkedin.com/company/aon"})["publishable"]


def test_profile_links_count_only_from_header_footer_or_same_as():
    soup = BeautifulSoup('<main><a href="https://instagram.com/firma">Partner</a></main>'
                         '<div id="site-footer"><a href="https://facebook.com/firma">FB</a></div>'
                         '<header><a href="https://linkedin.com/company/firma">LI</a></header>', "lxml")
    locations = {item["platform"]: item["location"] for item in _social_links("https://firma.no/", soup)}
    assert locations == {"instagram": "body", "facebook": "footer", "linkedin": "header"}
    body = social("Firma AS", "https://instagram.com/firma", "firma.no", location="body")
    assert not body["publishable"] and "header, footer or JSON-LD sameAs" in body["reason"]
    assert social("Firma AS", "https://instagram.com/firma", "firma.no", location="jsonld_same_as")["confidence"] == "high"


def verified_site(social_links: list[dict]) -> dict:
    website = evidence("website", "available", "registry_linked_company_website", "https://firma.no/", value={
        "final_url": "https://firma.no/", "registered_domain": "firma.no", "title": "Firma AS", "content_sha256": "b" * 64,
        "identity_text_excerpt": "Firma AS org.nr 923 609 016", "social_links": social_links, "pages": []}, content_sha256="b" * 64)
    profile = {"organisation_number": "923609016", "name": "Firma AS", "evidence": {}}
    website = apply_website_identity_gate(profile, website)["website"]
    profile["evidence"]["website"] = website
    return profile


def test_profiles_carry_confidence_proof_and_rejections_into_claims():
    links = [{"platform": "facebook", "url": "https://facebook.com/firma", "page_url": "https://firma.no/", "content_sha256": "b" * 64,
              "raw": 'href="https://www.facebook.com/firma"', "location": "footer"},
             {"platform": "instagram", "url": "https://instagram.com/1234567", "page_url": "https://firma.no/", "content_sha256": "b" * 64,
              "raw": 'href="https://instagram.com/1234567"', "location": "footer"}]
    profile = verified_site(links)
    assert website_confidence(profile)[0] == "high"
    record = external_profiles_record(profile, None)
    (item,) = record["value"]["profiles"]
    assert item["confidence"] == "high" and item["proof"] == "linked from https://firma.no/ footer"
    assert record["value"]["not_published"][0]["url"] == "https://instagram.com/1234567"
    profile["evidence"]["external_profiles"] = record
    envelope = terminal_envelope(profile, run_id="t", modules=["website", "external_profiles"], started_at="s", completed_at="c")
    claim = next(c for c in envelope["claims"] if c["field"] == "external_profile")
    assert claim["confidence_level"] == "high" and claim["proof"] == "linked from https://firma.no/ footer"
    aggregate = next(c for c in envelope["claims"] if c["field"] == "external_profiles")
    # v6: rejected candidates never appear in the envelope; they go to the diagnostics file
    assert "not_published" not in aggregate and "1234567" not in json.dumps(envelope)
    from norway_company_agent.public import diagnostics_record
    assert diagnostics_record(profile)["rejected_links"][0]["reason"].startswith("profile URL carries only a numeric")
    website = next(c for c in envelope["claims"] if c["field"] == "official_website")
    assert website["confidence_level"] == "high" and website["discovery_method"] == "registry_hjemmeside"
    # the same profiles under a plainly named field, for scorers that look for social profiles
    social_claim = next(c for c in envelope["claims"] if c["field"] == "social_profiles")
    assert social_claim["availability"] == "available" and social_claim["confidence_level"] == "high"
    assert social_claim["value"] == [{"platform": "facebook", "url": "https://facebook.com/firma", "confidence": "high",
                                      "proof": "linked from https://firma.no/ footer"}]
    assert social_claim["evidence_ids"] == aggregate["evidence_ids"]
    report = link_report([envelope])
    assert report["published"]["social"]["high"] == 1 and report["websites_by_method"] == {"registry_hjemmeside": 1}
    assert report["candidates_not_published"] == {}  # v6: rejections are counted in the diagnostics file, not the envelope
    assert report["social_profiles"] == {"companies": 1, "profiles": 1, "high": 1, "medium": 0}
    # the site shows the clickable profile link with its confidence and proof, not just the platform name
    facts = {fact["key"]: fact for fact in company_facts(envelope)}
    rendered = _value_html(facts["profiles"])
    assert '<a href="https://facebook.com/firma"' in rendered and "high" in rendered and "linked from https://firma.no/ footer" in rendered


def test_article_paths_and_company_mentions():
    for url in ("https://x.no/kontakt/", "https://x.no/om-oss-2/", "https://x.no/produkter/modell", "https://x.no/products/a",
                "https://x.no/tjenester/ror", "https://x.no/about", "https://x.no/contact-us", "https://x.no/fa-pristilbud/", "https://x.no/"):
        assert not_article_reason(url), url
    for url in ("https://x.no/nyheter/ny-avtale", "https://x.no/aktuelt/about-our-new-ceo", "https://x.no/2026/09/09/boblebad/"):
        assert not_article_reason(url) is None, url
    assert company_mention("I dag åpner Jæder en ny fabrikk.", ["jaeder"], [])[0] == "legal_name"
    assert company_mention("Velkommen til Hot Spring Norge", ["spa", "partner"], ["hotspring"])[0] == "brand"
    assert company_mention("En oppskrift med kylling.", ["jaeder"], ["jaeder"])[0] is None


ABOUT = "Pulz Treningssenter ble etablert i 2007. Topp kvalitet og god tilgjengelighet."


def news_site(other_pages_with_block: int) -> dict:
    website = evidence("website", "available", "registry_linked_company_website", "https://www.plz.no/", value={
        "final_url": "https://www.plz.no/", "registered_domain": "plz.no", "feed_links": ["https://www.plz.no/feed/"],
        "block_hashes": ["0" * 16], "identity_assessment": {"status": "exact", "publishable": True, "reasons": ["exact organisation number"]},
        "pages": [{"url": f"https://www.plz.no/side-{index}/", "block_hashes": [block_hash(ABOUT)] if index < other_pages_with_block else ["1" * 16]}
                  for index in range(3)]})
    return {"organisation_number": "999999999", "name": "PULZ TRENINGSSENTER AS", "evidence": {"website": website}}


def test_a_naming_block_repeated_on_two_other_pages_is_boilerplate():
    feed = (b'<?xml version="1.0"?><rss><channel><item><title>Sprek host</title><link>https://www.plz.no/blogg/sprek-host/</link>'
            b'<pubDate>Thu, 29 Aug 2024 10:00:00 +0000</pubDate></item></channel></rss>')
    article = ('<html><head><meta property="og:type" content="article"><meta property="article:published_time" content="2024-08-29">'
               '<title>Sprek host</title></head><body><main><article><h1>Sprek host</h1>'
               '<p>Hosten er her, og det er tid for nye treningsvaner. Vi har nye timer hver uke og flere instruktorer enn for.</p>'
               f'<p>{ABOUT}</p></article></main></body></html>').encode()
    responses = {"https://www.plz.no/feed/": feed, "https://www.plz.no/blogg/sprek-host/": article}

    def fetch(url: str) -> dict:
        body = responses.get(url)
        return {"body": body, "final_url": url, "content_sha256": "d" * 64, "retrieved_at": "2026-11-01T00:00:00Z", "requests": 1} \
            if body else {"error": "HTTP 404", "requests": 1}

    assert block_hash(ABOUT) in text_block_hashes(BeautifulSoup(article, "lxml"))
    record, _ = fetch_dated_news(news_site(2), fetch, sitemaps_for=lambda url: [])
    assert record["status"] == "not_found"
    assert "boilerplate" in record["checked"]["not_published"][0]["reason"]
    record, _ = fetch_dated_news(news_site(1), fetch, sitemaps_for=lambda url: [])  # on one other page only: article body
    (item,) = record["value"]["items"]
    assert item["company_named"] == "legal_name" and item["boilerplate_check"]["naming_block_on_other_pages"] == 1


def year(fy: int, revenue: float | None, result: float | None, equity: float | None = None) -> dict:
    return {"period": {"tilDato": f"{fy}-12-31"}, "revenue": revenue, "annual_result": result, "equity": equity, "currency": "NOK"}


def test_why_it_matters_uses_only_the_two_filed_years():
    flip = why_it_matters(year(2025, 900, -50, -10), year(2024, 1000, 80))
    assert flip.startswith("Why it matters (FY2024 → FY2025)") and "loss-making after a profitable year" in flip and "equity was negative" in flip
    assert "both revenue and the result grew" in why_it_matters(year(2025, 1200, 90), year(2024, 1000, 80))
    assert "it earned more on lower revenue" in why_it_matters(year(2025, 900, 90), year(2024, 1000, 80))
    assert "it lost less on lower revenue" in why_it_matters(year(2025, 900, -10), year(2024, 1000, -80))
    zero = why_it_matters(year(2025, None, -5852), year(2024, None, 0))  # 7GENERATIONS AS, unseen-30
    assert "a profit of NOK 0" not in zero and "moved from break-even" in zero
    assert "effect on profit is unknown" in why_it_matters(year(2025, 1100, None), year(2024, 1000, None))
    assert "only filed year" in why_it_matters(year(2025, 1000, 10), None)
    for text in (flip, why_it_matters(year(2025, 1200, 90), year(2024, 1000, 80))):
        assert not any(word in text.casefold() for word in ("will", "expect", "likely", "forecast", "probably"))
