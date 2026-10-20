"""Offline tests for the recall additions (no network)."""
from __future__ import annotations

import csv
import gzip
import importlib.util
import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent import wikidata  # noqa: E402
from norway_company_agent.batch import evidence_terminal_state  # noqa: E402
from norway_company_agent.enrichment import (  # noqa: E402
    apply_wikidata_identity_support,
    company_activity_record,
    company_jobs_record,
    external_profiles_record,
    wikidata_website_candidate,
    workforce_record,
)
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.website import _priority_links, job_postings, page_dates  # noqa: E402

ORG = "923609016"


def sparql(rows: list[dict]) -> dict:
    def b(value: str) -> dict:
        return {"type": "literal", "value": value}
    bindings = []
    for row in rows:
        bindings.append({key: b(value) for key, value in row.items()})
    return {"results": {"bindings": bindings}, "_meta": {"retrieved_at": "2026-10-08T00:00:00Z", "content_sha256": "a" * 64, "mode": "full"}}


def test_wikidata_exact_match_profiles_and_ambiguity():
    data = sparql([
        {"item": "http://www.wikidata.org/entity/Q1", "orgnr": "923 609 016", "website": "https://www.equinor.com",
         "x": "Equinor", "linkedin": "equinor", "enwiki": "https://en.wikipedia.org/wiki/Equinor"},
        {"item": "http://www.wikidata.org/entity/Q1", "orgnr": "923609016", "facebook": "Equinor"},
        {"item": "http://www.wikidata.org/entity/Q2", "orgnr": "111111111"},
        {"item": "http://www.wikidata.org/entity/Q3", "orgnr": "111111111"},
    ])
    index = wikidata.build_index(data)
    record = wikidata.evidence_for(ORG, index, data["_meta"])
    assert record["status"] == "available"
    platforms = {item["platform"] for item in record["value"]["profiles"]}
    assert platforms == {"x", "linkedin", "facebook"}
    assert record["value"]["official_websites"] == ["https://www.equinor.com"]
    assert wikidata.evidence_for("111111111", index, data["_meta"])["status"] == "not_applicable"
    assert wikidata.evidence_for("999999999", index, data["_meta"])["status"] == "not_found"
    assert wikidata_website_candidate(record) == "https://www.equinor.com"


def test_page_dates_and_job_postings_are_declared_not_inferred():
    html = """<html><head><meta property="article:published_time" content="2026-09-30T10:00:00+02:00"></head>
    <body><time datetime="2026-10-01">1. okt</time><p>Founded 1972, phone 2025-1234</p></body></html>"""
    soup = BeautifulSoup(html, "lxml")
    structured = {"json-ld": [{"@type": "JobPosting", "title": "Data Engineer", "datePosted": "2026-10-02"}]}
    assert page_dates(soup, structured) == ["2026-10-02", "2026-10-01", "2026-09-30"]
    assert job_postings(structured)[0]["title"] == "Data Engineer"


def test_priority_links_diversify_categories():
    html = """<a href="/kontakt">Kontakt</a><a href="/om-oss">Om oss</a><a href="/nyheter">Nyheter</a>
    <a href="/karriere">Karriere</a><a href="/ledelse">Ledelse</a><a href="https://other.no/jobb">x</a>"""
    links = _priority_links("https://firma.no/", BeautifulSoup(html, "lxml"))
    assert links == ["https://firma.no/kontakt", "https://firma.no/nyheter", "https://firma.no/karriere", "https://firma.no/ledelse"]


def gated_profile() -> dict:
    website = evidence("website", "available", "registry_linked_company_website", "https://firma.no/", value={
        "final_url": "https://firma.no/", "title": "Firma AS", "content_sha256": "b" * 64,
        "declared_dates": [], "job_postings": [],
        "identity_assessment": {"publishable": True, "status": "exact", "score": 1.0},
        "social_links": [{"platform": "facebook", "url": "https://www.facebook.com/firma"}],
        "pages": [
            {"url": "https://firma.no/", "title": "Firma AS", "content_sha256": "b" * 64},
            {"url": "https://firma.no/nyheter/ny-avtale", "title": "Ny avtale", "category": "news",
             "declared_dates": ["2026-09-12"], "job_postings": [], "content_sha256": "c" * 64},
            {"url": "https://firma.no/karriere", "title": "Karriere", "category": "careers",
             "declared_dates": [], "job_postings": [{"title": "Lagermedarbeider", "date_posted": "2026-10-01"}],
             "content_sha256": "d" * 64},
        ],
    })
    registry = evidence("registry", "available", "official_registry_bulk", "https://data.brreg.no/x", value={}, content_sha256="e" * 64)
    return {"organisation_number": ORG, "name": "Firma AS", "employees": 12,
            "evidence": {"website": website, "registry": registry}}


def test_enrichment_records_from_gated_site():
    profile = gated_profile()
    assert workforce_record(profile)["value"]["registered_employees"] == 12
    activity = company_activity_record(profile)
    assert activity["status"] == "available" and activity["value"]["latest_declared_date"] == "2026-09-12"
    jobs = company_jobs_record(profile)
    assert jobs["status"] == "available" and jobs["value"]["open_postings_found"] == 1
    wiki = {"status": "available", "retrieved_at": "t", "content_sha256": "a" * 64, "value": {
        "wikidata_item": "http://www.wikidata.org/entity/Q9", "identity_proof": f"Wikidata P2333 = {ORG}",
        "official_websites": ["https://firma.no"],
        "profiles": [{"platform": "facebook", "handle": "firma", "url": "https://www.facebook.com/firma"},
                     {"platform": "linkedin", "handle": "firma-as", "url": "https://www.linkedin.com/company/firma-as/"}],
        "wikipedia": []}}
    profiles = external_profiles_record(profile, wiki)
    assert profiles["status"] == "available"
    by_platform = {item["platform"]: item for item in profiles["value"]["profiles"]}
    assert set(by_platform) == {"facebook", "linkedin"}
    assert len(by_platform["facebook"]["sources"]) == 2  # deduplicated across site + Wikidata


def test_unverified_site_publishes_nothing_external():
    profile = gated_profile()
    profile["evidence"]["website"]["value"]["identity_assessment"] = {"publishable": False, "status": "review", "score": 0.85}
    assert company_activity_record(profile)["status"] == "not_found"
    assert company_jobs_record(profile)["status"] == "not_found"
    assert external_profiles_record(profile, None)["status"] == "not_found"


def test_wikidata_promotes_review_site_only_on_same_domain():
    profile = gated_profile()
    identity = profile["evidence"]["website"]["value"]["identity_assessment"]
    identity.update({"publishable": False, "status": "review", "score": 0.85})
    other = {"status": "available", "value": {"official_websites": ["https://annet.no"], "wikidata_item": "Q"}}
    apply_wikidata_identity_support(profile, other)
    assert identity["publishable"] is False
    same = {"status": "available", "value": {"official_websites": ["https://www.firma.no/"], "wikidata_item": "Q"}}
    apply_wikidata_identity_support(profile, same)
    assert identity["publishable"] is True and identity["status"] == "exact"


def test_budget_state_is_terminal():
    record = evidence("website", "not_fetched", "budget_guard", "about:budget", note="budget_exhausted: x")
    assert evidence_terminal_state(record) == "budget_exhausted"


def _load_runner():
    spec = importlib.util.spec_from_file_location("runner", ROOT / "scripts" / "run_competition_batch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_batch_end_to_end_offline_with_budget(tmp_path, monkeypatch):
    runner = _load_runner()
    orgs = ["923609016", "914778271", "981276957"]
    bulk = tmp_path / "bulk.csv"
    with gzip.open(bulk, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["organisasjonsnummer", "navn", "antallAnsatte", "hjemmeside"])
        writer.writeheader()
        writer.writerow({"organisasjonsnummer": orgs[0], "navn": "Firma AS", "antallAnsatte": "12", "hjemmeside": ""})
        writer.writerow({"organisasjonsnummer": orgs[1], "navn": "Annet AS", "antallAnsatte": "", "hjemmeside": "annet.no"})
        writer.writerow({"organisasjonsnummer": orgs[2], "navn": "Tredje AS", "antallAnsatte": "3", "hjemmeside": ""})
    inputs = tmp_path / "orgs.txt"
    inputs.write_text("\n".join(orgs), encoding="utf-8")
    cache = tmp_path / "wd.json"
    cache.write_text(json.dumps(sparql([{"item": "http://www.wikidata.org/entity/Q1", "orgnr": orgs[0],
                                         "website": "https://firma.no", "linkedin": "firma-as"}])), encoding="utf-8")

    def fake_official(org, modules):
        return {module: evidence(module, "available", "official", "https://data.brreg.no/", value={}) for module in modules}, []

    seen_urls = []

    def fake_website(url, source_type="registry_linked_company_website", **_):
        seen_urls.append((url, source_type))
        if not url:
            return evidence("website", "not_found", source_type, "https://data.brreg.no/", note="No valid registry website URL"), {"requests": 0, "bytes": 0, "latencies_ms": []}
        return evidence("website", "available", source_type, url, value={
            "final_url": url, "title": "Firma AS", "main_text_excerpt": "Firma AS org.nr 923 609 016 " * 5,
            "content_sha256": "f" * 64, "pages": [], "social_links": [], "declared_dates": ["2026-10-01"], "job_postings": [],
        }), {"requests": 3, "bytes": 10, "latencies_ms": [5]}

    monkeypatch.setattr(runner, "fetch_official_modules", fake_official)
    monkeypatch.setattr(runner, "fetch_website", fake_website)
    monkeypatch.setattr(runner, "fetch_dated_news", lambda profile: (evidence("dated_news", "not_found", "company_site_news", "https://firma.no/"),
                                                                      {"requests": 0, "bytes": 0, "latencies_ms": []}))
    out = tmp_path / "out"
    argv = ["run", "--organisations", str(inputs), "--bulk", str(bulk), "--output", str(out / "env.jsonl"),
            "--profiles-output", str(out / "profiles.jsonl"), "--report", str(out / "report.json"),
            "--run-id", "t1", "--expected-count", "3", "--workers", "1", "--wikidata-cache", str(cache),
            "--max-requests", "40", "--no-domain-guess", "--site-dir", str(out / "site")]
    monkeypatch.setattr(sys, "argv", argv)
    try:
        runner.main()
    except SystemExit as exc:
        assert exc.code == 0
    envelopes = [json.loads(line) for line in (out / "env.jsonl").read_text().splitlines()]
    assert [item["organisation_number"] for item in envelopes] == orgs
    report = json.loads((out / "report.json").read_text())
    assert report["validation"]["passed"] is True
    assert report["wikidata"]["requests"] == 0  # served from declared cache
    assert ("https://firma.no", "wikidata_linked_company_website") in seen_urls
    first = envelopes[0]["profile"]["evidence"]
    assert first["website"]["value"]["identity_assessment"]["publishable"] is True
    assert first["external_profiles"]["status"] == "available"
    assert first["company_activity"]["status"] == "available"
    assert report["site"]["status"] == "ok" and (out / "site" / "index.html").exists()
    states = {module["state"] for env in envelopes for module in env["modules"].values()}
    assert states <= {"complete", "not_found", "not_applicable", "budget_exhausted", "source_error", "blocked_policy", "blocked_robots"}


from norway_company_agent.domain_guess import candidate_domains, guess_website  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate, assess_website_identity  # noqa: E402
from norway_company_agent.website import identity_excerpt  # noqa: E402


def test_candidate_domains_skip_generic_and_holding_names():
    assert candidate_domains("Fjord Fisk AS") == ["fjordfisk.no", "fjord-fisk.no"]
    assert candidate_domains("Bygg Service AS") == []
    assert candidate_domains("Nordic Holding AS") == []
    assert candidate_domains("Ærlig Økologisk Mat AS") == ["aerligokologiskmat.no", "aerlig-okologisk-mat.no"]


def _site(url: str, footer: str, title: str = "Velkommen") -> tuple[dict, dict]:
    html = f"<html><head><title>{title}</title></head><body><main>Vi leverer fisk.</main><footer>{footer}</footer></body></html>"
    return evidence("website", "available", "name_derived_domain_candidate", url, value={
        "final_url": url, "title": title, "main_text_excerpt": "Vi leverer fisk.",
        "identity_text_excerpt": identity_excerpt(BeautifulSoup(html, "lxml")), "pages": [],
        "social_links": [], "content_sha256": "1" * 64,
    }), {"requests": 2, "bytes": 100, "latencies_ms": [10]}


def test_guess_publishes_only_with_org_number_proof():
    profile = {"organisation_number": "923609016", "name": "Fjord Fisk AS", "evidence": {}}
    accepted, metrics, trail = guess_website(
        profile, lambda url, **_: _site(url, "Fjord Fisk AS | Org.nr. 923 609 016 MVA"), apply_website_identity_gate,
        resolver=lambda host: host == "fjordfisk.no")
    assert accepted is not None and accepted["value"]["identity_assessment"]["score"] == 1.0
    assert metrics["requests"] == 2
    rejected, _, trail = guess_website(
        profile, lambda url, **_: _site(url, "Fjord Fisk AS, Bergen", title="Fjord Fisk AS"), apply_website_identity_gate,
        resolver=lambda host: True)
    assert rejected is None and len(trail) == 2


def test_footer_org_number_makes_homepage_exact_but_group_listing_does_not():
    record, _ = _site("https://firma.no/", "Firma AS · Org nr 923609016")
    profile = {"organisation_number": "923609016", "name": "Helt Annet Navn AS", "evidence": {"website": record}}
    assert assess_website_identity(profile)["score"] == 1.0
    page_record, _ = _site("https://group.no/", "Group")
    page_record["value"]["pages"] = [{"url": "https://group.no/kontakt",
                                      "identity_text_excerpt": "Datter AS 923 609 016 · Annen AS 914 778 271"}]
    profile = {"organisation_number": "923609016", "name": "Datter AS", "evidence": {"website": page_record}}
    assert assess_website_identity(profile)["score"] < 1.0
    page_record["value"]["pages"] = [{"url": "https://group.no/kontakt", "identity_text_excerpt": "Datter AS · org.nr 923 609 016"}]
    assert assess_website_identity(profile)["score"] == 1.0
