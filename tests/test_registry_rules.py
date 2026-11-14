"""Offline tests: registry-kept website, ambiguous domains, sub-unit websites, change events, source links, link guard."""
from __future__ import annotations

import csv
import gzip
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_v2_product import MODULES, envelope, make_profile  # noqa: E402
from test_coverage_more import subunit_profile  # noqa: E402
from norway_company_agent.batch import terminal_envelope  # noqa: E402
from norway_company_agent.domain_filings import ambiguity, decide_registry_website, domain_counts  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.http import FetchResult  # noqa: E402
from norway_company_agent.public import guard_urls, reconcile_profile_claims  # noqa: E402
from norway_company_agent.registry_changes import (  # noqa: E402
    SlotPacer, fetch_registry_events, unit_events,
)

RESOLVES = lambda raw: (True, "resolves in DNS")  # noqa: E731
BULK = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"


def test_domain_counts_and_ambiguity(tmp_path):
    path = tmp_path / "bulk.csv.gz"
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(["organisasjonsnummer", "navn", "hjemmeside"])
    for org, site in (("1", "www.solo.no"), ("2", "www.shared.no"), ("3", "shared.no/avd"), ("4", "https://a.wixsite.com/x"), ("5", "")):
        writer.writerow([org, "X", site])
    path.write_bytes(gzip.compress(text.getvalue().encode()))
    counts = domain_counts(path)
    assert counts["solo.no"] == 1 and counts["shared.no"] == 2
    assert ambiguity("www.solo.no", counts) is None
    assert "filed as the website of 2 companies" in ambiguity("https://shared.no/", counts)
    assert "platform" in ambiguity("a.wixsite.com", counts) and "franchise" in ambiguity("www.7-eleven.no", counts)
    assert "not available" in ambiguity("www.solo.no", None)  # without the bulk file nothing is attributed from the registry alone


def registry_kept_profile(status: str = "source_error", site: str = "www.kept.no") -> dict:
    profile = make_profile(40)
    profile["website"] = site
    profile["evidence"]["registry"]["value"]["hjemmeside"] = site
    profile["evidence"]["website"] = evidence("website", status, "registry_linked_company_website", "https://" + site + "/",
                                              note="URLError: timed out")
    profile["registry_website_check"] = {"url": "https://" + site + "/", "fetch_status": status, "fetch_note": "URLError: timed out"}
    return profile


def test_unreadable_registry_website_is_kept_from_the_registry_record_only():
    profile = registry_kept_profile()
    decide_registry_website(profile, {"kept.no": 1}, resolver=RESOLVES)
    env = envelope(profile)
    claim = next(c for c in env["claims"] if c["field"] == "official_website")
    assert claim["availability"] == "available" and claim["value"] == "https://www.kept.no/"
    assert claim["website_source"] == "registry_record" and "site unreachable at crawl time" in claim["note"]
    assert claim["confidence_level"] == "medium" and claim["discovery_method"] == "registry_record"
    source = next(e for e in env["evidence"] if e["id"] == claim["evidence_ids"][0])
    assert source["source_url"] == f"https://data.brreg.no/enhetsregisteret/api/enheter/{env['organisation_number']}"
    assert 'hjemmeside="www.kept.no"' in source["claim_span"] and source["source_class"] == "official_registry"
    # nothing derived from the site
    assert not any(c["availability"] == "available" for c in env["claims"]
                   if c["field"] in {"dated_news", "site_email", "site_phone", "careers_page", "company_activity"})
    assert env["profile"]["website"] == "https://www.kept.no/" and env["link_guard"]["removed"] == 0
    assert any("Website in its Brønnøysund record: kept.no" in s["text"] for s in env["synthesis"]["sentences"])


def test_registry_website_whose_domain_does_not_resolve_is_not_published():
    profile = registry_kept_profile(site="www.lapsed.no")
    decide_registry_website(profile, {"lapsed.no": 1}, resolver=lambda raw: (False, "domain does not resolve in DNS (NXDOMAIN)"))
    assert "registry_website_kept" not in profile and "NXDOMAIN" in profile["registry_website_unresolved"]["reason"]
    env = envelope(profile)
    assert next(c for c in env["claims"] if c["field"] == "official_website")["availability"] != "available"
    assert "lapsed.no" not in json.dumps(env)
    alive = registry_kept_profile()
    decide_registry_website(alive, {"kept.no": 1}, resolver=lambda raw: (True, "kept.no resolves in DNS"))
    assert alive["registry_website_kept"]["dns"] == "kept.no resolves in DNS"


def page_record(final_url, text, pages=()):
    value = {"final_url": final_url, "title": "", "identity_text_excerpt": text, "main_text_excerpt": "", "org_number_lines": [],
             "content_sha256": "b" * 64, "pages": [{"url": final_url}, *pages]}
    return {"status": "available", "value": value, "retrieved_at": "2026-11-15T00:00:00Z"}, {"requests": 2, "bytes": 1, "latencies_ms": [1]}


def test_subunit_site_check_needs_dns_same_domain_and_printed_org_number_or_exact_name():
    from norway_company_agent.subunit_sites import check_subunit_site
    sub, parent = {"organisation_number": "912345678", "name": "CETHO AVD BERGEN"}, {"organisation_number": "900000001", "name": "CETHO AS"}
    ok = lambda raw: (True, "resolves")  # noqa: E731
    result, _ = check_subunit_site("www.cetho.no", sub, parent, lambda *a, **k: page_record("https://www.cetho.no/", "Org.nr. 900 000 001"), ok)
    assert result["ok"] and result["proof"]["who"] == "parent" and result["proof"]["kind"] == "org_number"
    result, _ = check_subunit_site("www.cetho.no", sub, parent, lambda *a, **k: page_record("https://www.cetho.no/", "© 2026 Cetho AS. Alle rettigheter"), ok)
    assert result["ok"] and result["proof"]["kind"] == "legal_name"
    result, _ = check_subunit_site("www.cetho.no", sub, parent, lambda *a, **k: page_record("https://www.cetho.no/", "Cetho ASA og Cetho Holding"), ok)
    assert not result["ok"]  # a longer or different name is not the exact legal name
    result, _ = check_subunit_site("aiota.no", sub, parent, lambda *a, **k: page_record("https://tagtech.no/", "CETHO AS"), ok)
    assert not result["ok"] and "redirects to tagtech.no" in result["reason"]
    result, _ = check_subunit_site("www.cetho.no", sub, parent, lambda *a, **k: page_record("https://www.cetho.no/", "Domenet er registrert hos Domeneshop"), ok)
    assert not result["ok"] and "no page checked prints" in result["reason"]
    fetched = []
    result, _ = check_subunit_site("www.cetho.no", sub, parent, lambda *a, **k: fetched.append(1), lambda raw: (False, "NXDOMAIN"))
    assert not result["ok"] and result["reason"] == "NXDOMAIN" and fetched == []


def test_shared_or_platform_registry_website_is_ambiguous_and_not_published():
    profile = registry_kept_profile(site="www.chain.no")
    decide_registry_website(profile, {"chain.no": 14}, resolver=RESOLVES)
    assert "14 companies" in profile["registry_website_ambiguous"]["reason"] and "registry_website_kept" not in profile
    env = envelope(profile)
    assert next(c for c in env["claims"] if c["field"] == "official_website")["availability"] != "available"
    assert "chain.no" not in json.dumps(env)
    loaded = registry_kept_profile(status="available")
    decide_registry_website(loaded, {"kept.no": 1}, resolver=RESOLVES)
    assert "registry_website_kept" not in loaded  # the site was read (and did not prove identity): never kept


PROVEN = {"url": "www.cetho.no", "ok": True, "retrieved_at": "2026-11-15T00:00:00Z",
          "proof": {"kind": "org_number", "who": "sub-unit", "value": "912345678", "page_url": "https://www.cetho.no/kontakt",
                    "content_sha256": "a" * 64}}


def test_subunit_website_on_a_single_filer_domain_is_a_labelled_related_site():
    profile = subunit_profile("www.cetho.no")
    profile["domain_filings"] = {"cetho.no": None}
    profile["subunit_site_checks"] = {"www.cetho.no": PROVEN}
    env = terminal_envelope(profile, run_id="t", modules=["locations", "website"], started_at="s", completed_at="c")
    related = next(c for c in env["claims"] if c["field"] == "related_website")
    assert related["availability"] == "available" and related["value"]["url"] == "https://www.cetho.no"
    assert related["value"]["subunit_organisation_number"] == "912345678" and "not the company's own" in related["value"]["relationship"]
    source = next(e for e in env["evidence"] if e["id"] == related["evidence_ids"][0])
    assert source["source_url"] == "https://data.brreg.no/enhetsregisteret/api/underenheter/912345678"
    assert related["value"]["site_proof"]["page_url"] == "https://www.cetho.no/kontakt" and "prints the sub-unit's org number" in related["proof"]
    assert next(c for c in env["claims"] if c["field"] == "official_website")["availability"] != "available"
    for checks in ({}, {"www.cetho.no": {"ok": False, "reason": "cetho.no redirects to other.no, another domain"}}):
        unproven = subunit_profile("www.cetho.no")
        unproven["domain_filings"] = {"cetho.no": None}
        unproven["subunit_site_checks"] = checks
        env = terminal_envelope(unproven, run_id="t", modules=["locations", "website"], started_at="s", completed_at="c")
        assert not any(c["field"] == "related_website" for c in env["claims"]) and "cetho" not in json.dumps(env)
    shared = subunit_profile("www.cetho.no")
    shared["domain_filings"] = {"cetho.no": "cetho.no is filed as the website of 3 companies in the Brønnøysund bulk entity file"}
    env = terminal_envelope(shared, run_id="t", modules=["locations", "website"], started_at="s", completed_at="c")
    assert not any(c["field"] == "related_website" for c in env["claims"]) and "cetho" not in json.dumps(env)


def fetch(body, url="https://data.brreg.no/x"):
    return FetchResult(url, 200, 1, 1, body, content_sha256="9" * 64, retrieved_at="2026-11-14T00:00:00Z")


def test_change_events_state_old_only_when_the_feed_did_and_never_publish_past_websites():
    body = {"_embedded": {"oppdaterteEnheter": [
        {"organisasjonsnummer": "900000040", "dato": "2025-11-17T09:50:12Z", "endringstype": "Endring",
         "endringer": [{"op": "replace", "path": "/kapital/belop", "value": 37245}, {"op": "replace", "path": "/hjemmeside", "value": "www.old.no"},
                       {"op": "replace", "path": "/registreringsdatoAntallAnsatteEnhetsregisteret", "value": "2025-11-17"}]},
        {"organisasjonsnummer": "900000040", "dato": "2026-01-13T10:00:12Z", "endringstype": "Endring",
         "endringer": [{"op": "replace", "path": "/kapital/belop", "value": 37618}, {"op": "replace", "path": "/hjemmeside", "value": "www.new.no"}]},
        {"organisasjonsnummer": "900000040", "dato": "2021-06-26T22:30:00Z", "endringstype": "Ny"}]}}
    events = unit_events(body, entity="main unit", organisation_number="900000040", source=fetch(body), personal=False, list_key="oppdaterteEnheter")
    capital = [e for e in events if e["field"] == "kapital.belop"]
    assert capital[0]["old_stated"] is False and capital[0]["new"] == 37245
    assert capital[1]["old"] == 37245 and capital[1]["new"] == 37618 and "earlier value" in capital[1]["claim_span"]
    sites = [e for e in events if e["field"] == "hjemmeside"]
    assert all(e["new"] is None and e["old"] is None and "www." not in e["claim_span"] for e in sites)
    assert not any("registreringsdatoAntallAnsatte" in str(e["field"]) for e in events)
    assert events[-1]["type"] == "registered" and events[-1]["date"] == "2021-06-26"  # as written in the feed timestamp
    personal = unit_events({"_embedded": {"oppdaterteEnheter": [{"dato": "2026-01-01T00:00:00Z", "endringstype": "Endring",
                                                                  "endringer": [{"op": "replace", "path": "/telefon", "value": "99999999"}]}]}},
                           entity="main unit", organisation_number="1", source=fetch({}), personal=True, list_key="oppdaterteEnheter")
    assert personal == []  # a sole proprietor's phone is a private person's


def test_only_stated_changes_are_published_and_the_date_is_the_feed_text():
    body = {"_embedded": {"oppdaterteEnheter": [
        {"organisasjonsnummer": "900000040", "dato": "2025-04-15T00:04:32.722+02:00", "endringstype": "Endring"},
        {"organisasjonsnummer": "900000040", "dato": "2018-07-19T21:07:41.836Z", "endringstype": "Ukjent"},
        {"organisasjonsnummer": "900000040", "dato": "2026-10-12T22:37:43.658Z", "endringstype": "Endring",
         "endringer": [{"op": "replace", "path": "/registreringsdatoAntallAnsatteEnhetsregisteret", "value": "2026-10-12"}]},
        {"organisasjonsnummer": "900000040", "dato": "2026-10-12T22:37:43.658Z", "endringstype": "Endring",
         "endringer": [{"op": "replace", "path": "/antallAnsatte", "value": 150}]},
        {"organisasjonsnummer": "900000040", "dato": "2026-11-01T10:00:00Z", "endringstype": "Sletting"}]}}
    skipped = []
    events = unit_events(body, entity="main unit", organisation_number="900000040", source=fetch(body), personal=False,
                         list_key="oppdaterteEnheter", skipped=skipped)
    assert [(e["type"], e["field"], e["date"]) for e in events] == [("changed", "antallAnsatte", "2026-10-12"), ("deleted", None, "2026-11-01")]
    assert all(e["date"] in e["claim_span"] for e in events)  # character for character in the cited JSON
    assert [item["endringstype"] for item in skipped] == ["Endring", "Ukjent", "Endring"]
    record, _ = fetch_registry_events("900000040", personal=False,
                                      fetcher=lambda url: fetch([] if "roller" in url else {"_embedded": {"oppdaterteEnheter": body["_embedded"]["oppdaterteEnheter"][:2]}}, url))
    assert record["status"] == "not_found" and len(record["not_published"]) == 2
    profile = make_profile(43)
    profile["evidence"]["registry_events"] = record
    env = envelope(profile)
    assert "not_published" not in env["profile"]["evidence"]["registry_events"]
    assert not any(c["field"] == "registry_event" and c["availability"] == "available" for c in env["claims"])


def test_only_the_entity_feed_is_called_and_events_are_dated_claims():
    calls = []

    def fetcher(url):
        calls.append(url)
        return fetch({"_embedded": {"oppdaterteEnheter": [{"organisasjonsnummer": "900000041", "dato": "2026-05-01T08:00:00Z", "endringstype": "Endring",
                                                           "endringer": [{"op": "replace", "path": "/navn", "value": "NYTT NAVN AS"}]}]},
                      "page": {"totalPages": 1}}, url)

    record, metrics = fetch_registry_events("900000041", personal=False, fetcher=fetcher)
    assert len(calls) == len(metrics) == 1 and "/oppdateringer/enheter?organisasjonsnummer=900000041&includeChanges=true" in calls[0]
    profile = make_profile(41)
    profile["evidence"]["registry_events"] = record
    env = terminal_envelope(profile, run_id="t", modules=[*MODULES, "registry_events"], started_at="s", completed_at="c")
    claims = [c for c in env["claims"] if c["field"] == "registry_event" and c["availability"] == "available"]
    assert len(claims) == 1 and claims[0]["as_of"] == "2026-05-01" and claims[0]["evidence_ids"]
    lines = [s["text"] for s in env["synthesis"]["sentences"] if s["topic"] == "changes"]
    assert any(line.startswith("2026-05-01: name updated") for line in lines)


def test_change_feed_skipped_for_the_deadline_is_not_available_with_its_reason():
    from norway_company_agent.registry_changes import skipped_record
    profile = make_profile(44)
    profile["evidence"]["registry_events"] = skipped_record("900000044")
    env = terminal_envelope(profile, run_id="t", modules=[*MODULES, "registry_events"], started_at="s", completed_at="c")
    claim = next(c for c in env["claims"] if c["field"] == "registry_events")
    assert claim["availability"] == "not_available" and "skipped to protect the run deadline" in json.dumps(claim)
    assert claim["note"] == "skipped to protect the run deadline"
    # summary lines must cite evidence; a check that was skipped has none, so no line is written for it
    assert not any(s.get("family") == "registry_history" for s in env["synthesis"]["sentences"])


def test_slot_pacer_keeps_each_connection_at_least_0_6_s_apart():
    now = [0.0]
    pacer = SlotPacer(slots=2, gap=0.6, clock=lambda: now[0], sleeper=lambda s: now.__setitem__(0, now[0] + s))
    starts = []
    for _ in range(6):
        with pacer.slot() as index:
            starts.append((index, now[0]))
    for slot in (0, 1):
        times = [t for i, t in starts if i == slot]
        assert all(b - a >= 0.6 - 1e-9 for a, b in zip(times, times[1:]))


def test_no_source_link_is_the_bulk_csv_or_the_sparql_endpoint():
    env = envelope(make_profile(42, records=None))
    text = json.dumps(env)
    assert "lastned/csv" not in text and "query.wikidata.org/sparql" not in text
    org = env["organisation_number"]
    registry = [e for e in env["evidence"] if e["module"] == "registry"]
    assert registry and all(e["source_url"] == f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}" for e in registry)
    assert all("bulk entity file" in e["read_from"] for e in registry)
    wiki = env["profile"]["evidence"]["wikidata_footprint"]
    assert wiki["source_url"].startswith("https://www.wikidata.org/w/index.php?search=haswbstatement")


def test_a_profile_handle_that_looks_like_a_domain_is_not_withheld_and_aggregates_agree():
    url = "https://instagram.com/novooi.no"
    site = {"status": "available", "value": {"final_url": "https://novooi.com/", "requested_url": "https://novooi.no/",
                                             "identity_assessment": {"publishable": True, "score": 1.0}}}
    env = {"organisation_number": "827340162", "profile": {"evidence": {"website": site}},
           "evidence": [{"id": "ev-external_profiles-1", "source_url": "https://novooi.com/"}],
           "claims": [
               {"field": "external_profile", "availability": "available", "fact_level": True, "evidence_ids": ["ev-external_profiles-1"],
                "value": {"platform": "instagram", "url": url, "identity_proof": "linked from https://novooi.com/ header; handle 'novooi.no' is the legal name"},
                "confidence_level": "high", "confidence": 0.95, "proof": "linked from header"},
               {"field": "external_profiles", "availability": "available", "evidence_ids": ["ev-external_profiles-1"],
                "value": [{"platform": "instagram", "url": url}, {"platform": "facebook", "url": "https://facebook.com/gone"}]},
               {"field": "social_profiles", "availability": "available", "evidence_ids": ["ev-external_profiles-1"],
                "value": [{"platform": "instagram", "url": url}]}]}
    removed = [item for item in guard_urls(env) if "novooi.no" in item["text"]]
    assert removed == [] and env["claims"][0]["availability"] == "available"
    reconcile_profile_claims(env)
    for claim in env["claims"][1:]:
        assert [entry["url"] for entry in claim["value"]] == [url]  # exactly the published per-fact profiles


def test_an_org_number_inside_a_script_is_not_printed_on_the_site():
    from bs4 import BeautifulSoup
    from norway_company_agent.website import identity_excerpt, org_number_lines
    hidden = BeautifulSoup("<html><body><footer><script>var URL = 'https://log.example/track?orgnr=935711851&domain=x.no';"
                           "</script><style>.orgnr{}</style>Kontakt oss</footer></body></html>", "lxml")
    assert "935711851" not in identity_excerpt(hidden) and org_number_lines(hidden) == []
    shown = BeautifulSoup("<html><body><footer><script>var a = 1;</script>Org.nr. 935 711 851</footer></body></html>", "lxml")
    assert "Org.nr. 935 711 851" in identity_excerpt(shown) and "var a" not in identity_excerpt(shown)
    assert org_number_lines(shown) == ["Org.nr. 935 711 851"]
