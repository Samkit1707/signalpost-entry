"""Offline tests for v4: exact citations, honest states, and nothing from a site that is not the company's own."""
from __future__ import annotations

import json

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from norway_company_agent.batch import terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.citations import check_envelopes, envelope_problems, span_supports  # noqa: E402
from norway_company_agent.contract import AVAILABILITY  # noqa: E402
from norway_company_agent.enrichment import company_jobs_record, external_profiles_record, quarantine_unowned_site  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.http import FetchResult  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate, assess_website_identity  # noqa: E402
from norway_company_agent.news import fetch_dated_news  # noqa: E402
from norway_company_agent.official import fetch_official_modules  # noqa: E402
from norway_company_agent.wikidata import build_index, evidence_for  # noqa: E402
from test_v2_product import MODULES, account, make_profile  # noqa: E402

ORG = "923609016"
OTHER = "914778271"  # another entity's valid organisation number
FILLER = " Vi planlegger, graver, murer og bygger uterom som varer, for bedrifter og privatkunder i hele regionen." * 3


def envelope(profile: dict) -> dict:
    return terminal_envelope(profile, run_id="t", modules=MODULES, started_at="a", completed_at="b")


def rich_envelopes() -> list[dict]:
    return [envelope(make_profile(index, records=[account(2024), account(2025)], website=index % 2 == 0,
                                  news=[("Ny avtale signert", "2026-09-01")] if index % 2 == 0 else None)) for index in range(1, 7)]


# --- 1. citations -------------------------------------------------------------------------------------------------

def test_every_published_claim_has_hash_and_a_span_containing_its_value():
    envelopes = rich_envelopes()
    summary = check_envelopes(envelopes)
    assert summary["published_claims"] > 100
    assert summary["published_claims_missing_hash"] == summary["published_claims_missing_span"] == 0
    assert summary["passed"], summary["examples"]
    for env in envelopes:
        evidence_by_id = {item["id"]: item for item in env["evidence"]}
        for claim in env["claims"]:
            if claim["availability"] == "available":
                for evidence_id in claim["evidence_ids"]:
                    item = evidence_by_id[evidence_id]
                    assert len(item["content_sha256"]) == 64 and item["claim_span"] and item["span_kind"] in {"field_path", "quote"}


def test_aggregate_registry_claims_each_cite_their_own_columns():
    env = rich_envelopes()[0]
    evidence_by_id = {item["id"]: item for item in env["evidence"]}

    def span(field: str) -> str:
        claim = next(c for c in env["claims"] if c["field"] == field)
        return evidence_by_id[claim["evidence_ids"][0]]["claim_span"]

    org = env["organisation_number"]
    assert span("legal_name") == f'row[organisasjonsnummer={org}].navn="Testfirma 1 AS"'
    assert f'row[organisasjonsnummer={org}].organisasjonsform.kode="AS"' in span("legal_form")
    assert 'naeringskode1.kode="62.010"' in span("industry")
    assert 'forretningsadresse.adresse="Gate 1"' in span("business_address")
    assert "$[0].resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter=2025000.0" in span("latest_annual_accounts")
    assert span("registry_status") == '$.konkurs=false; $.underAvvikling=false'


def test_bad_claims_are_withheld_in_place_and_never_fail_the_run():
    for breakage in ("hash", "span", "value"):
        profile = make_profile(1, records=[account(2025)])
        if breakage == "hash":
            profile["evidence"]["registry"]["content_sha256"] = None
        elif breakage == "span":
            profile["evidence"]["roles"]["value"]["roles"][0]["claim_span"] = ""
        else:  # a span that names another value than the claim's
            profile["evidence"]["roles"]["value"]["roles"][0]["claim_span"] = '$.rollegrupper[0].roller[0].person.navn.fornavn="Ola"'
        env = envelope(profile)
        report = validate_envelopes([env], 1)
        assert report["passed"] is True  # the run never fails on a claim
        audit = report["citations"]
        assert audit["published_claims_missing_hash"] == audit["published_claims_missing_span"] == audit["citation_problems"] == 0
        assert audit["withheld_by_check"] or audit["uncited_withheld"]
        field = "legal_name" if breakage == "hash" else "role"
        withheld = [c for c in env["claims"] if c["field"] in {field, "roles"} and c.get("note")]
        assert withheld and all(c["value"] is None and c["availability"] in {"ambiguous", "not_available"} for c in withheld)
    assert span_supports("annual_revenue", {"amount": 2119889.0}, ['$[0].x.sumDriftsinntekter=2119889.0'])
    assert not span_supports("annual_revenue", {"amount": 2119889.0}, ['$[0].x.sumDriftsinntekter=211988.0'])
    assert span_supports("site_phone", "+4751685700", ['href="tel:+47 51 68 57 00"'])


def test_validator_detects_problems_on_an_envelope_built_elsewhere():
    good = rich_envelopes()[:1]
    for breakage in ("hash", "span", "value"):
        broken = copy.deepcopy(good)
        claim = next(c for c in broken[0]["claims"] if c["field"] == "legal_name")
        item = next(e for e in broken[0]["evidence"] if e["id"] == claim["evidence_ids"][0])
        if breakage == "hash":
            item["content_sha256"] = None
        elif breakage == "span":
            item["claim_span"] = ""
        else:
            item["claim_span"] = 'row[organisasjonsnummer=1].navn="SOMETHING ELSE AS"'
        assert check_envelopes(broken)["passed"] is False


def test_official_command_with_a_deliberately_bad_claim_still_passes_with_100_envelopes(tmp_path, monkeypatch):
    """End to end through scripts/run_competition_batch.py: one company's roles answer has a span naming another person."""
    import csv
    import gzip
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("runner", ROOT / "scripts" / "run_competition_batch.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    orgs = [f"{910000000 + index:09d}" for index in range(100)]
    bulk = tmp_path / "bulk.csv"
    with gzip.open(bulk, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["organisasjonsnummer", "navn", "organisasjonsform.kode", "hjemmeside"])
        writer.writeheader()
        for org in orgs:
            writer.writerow({"organisasjonsnummer": org, "navn": f"Firma {org} AS", "organisasjonsform.kode": "AS", "hjemmeside": ""})
    inputs = tmp_path / "orgs.txt"
    inputs.write_text("\n".join(orgs), encoding="utf-8")

    def fake_official(org, modules):
        role = {"name": "Kari Nordmann", "role": "Daglig leder", "role_code": "DAGL",
                "claim_span": '$.rollegrupper[0].roller[0].type.beskrivelse="Daglig leder"; '
                              '$.rollegrupper[0].roller[0].person.navn.fornavn="Kari"; $.rollegrupper[0].roller[0].person.navn.etternavn="Nordmann"'}
        if org == orgs[42]:  # deliberately bad: the cited path names someone else
            role["claim_span"] = '$.rollegrupper[0].roller[0].person.navn.fornavn="Ola"'
        records = {module: evidence(module, "not_found", "official", "https://data.brreg.no/", note="HTTP 404", content_sha256="0" * 64)
                   for module in modules}
        records["roles"] = evidence("roles", "available", "official_roles", f"https://data.brreg.no/enhetsregisteret/api/enheter/{org}/roller",
                                    value={"roles": [role]}, content_sha256="f" * 64)
        return records, []

    monkeypatch.setattr(runner, "fetch_official_modules", fake_official)
    monkeypatch.setattr(runner, "fetch_website", lambda url, source_type=None, **_: (
        evidence("website", "not_found", source_type, "https://data.brreg.no/", note="No valid registry website URL"),
        {"requests": 0, "bytes": 0, "latencies_ms": []}))
    monkeypatch.setattr(runner, "fetch_dated_news", lambda profile: (evidence("dated_news", "not_found", "company_site_news", "about:none"),
                                                                      {"requests": 0, "bytes": 0, "latencies_ms": []}))
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["run", "--organisations", str(inputs), "--bulk", str(bulk), "--output", str(out / "env.jsonl"),
                                      "--profiles-output", str(out / "profiles.jsonl"), "--report", str(out / "report.json"),
                                      "--run-id", "bad-claim", "--expected-count", "100", "--workers", "4", "--no-domain-guess",
                                      "--modules", "registry,roles,website,dated_news", "--site-dir", ""])
    code = 0
    try:
        runner.main()
    except SystemExit as exc:
        code = exc.code
    assert code == 0
    envelopes = [json.loads(line) for line in (out / "env.jsonl").read_text().splitlines()]
    report = json.loads((out / "report.json").read_text())
    assert len(envelopes) == 100 and report["emitted_envelopes"] == 100 and report["validation"]["passed"] is True
    bad = next(env for env in envelopes if env["organisation_number"] == orgs[42])
    roles = next(c for c in bad["claims"] if c["field"] == "roles")
    assert roles["availability"] == "ambiguous" and roles["value"] is None and roles["note"].startswith("withheld")
    assert not [c for c in bad["claims"] if c["field"] == "role" and c["availability"] == "available"]
    logged = report["validation"]["citations"]["withheld_by_check"]
    assert {(item["organisation_number"], item["field"]) for item in logged} >= {(orgs[42], "roles"), (orgs[42], "role")}
    good = next(env for env in envelopes if env["organisation_number"] == orgs[0])
    assert next(c for c in good["claims"] if c["field"] == "roles")["availability"] == "available"


def test_only_the_six_contract_states_appear_and_not_found_maps_to_not_available():
    assert AVAILABILITY["not_found"] == "not_available"
    states = {claim["availability"] for env in rich_envelopes() for claim in env["claims"]}
    assert states <= {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}
    profile = make_profile(9)
    env = envelope(profile)
    env["claims"][0]["availability"] = "unavailable"
    from norway_company_agent.citations import withhold_problem_claims
    withhold_problem_claims(env)
    assert env["claims"][0]["availability"] == "ambiguous" and env["claims"][0]["value"] is None
    assert {claim["availability"] for claim in env["claims"]} <= {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}


def test_official_responses_carry_json_paths():
    bodies = {
        "roller": {"rollegrupper": [{"sistEndret": "2021-03-10", "roller": [
            {"type": {"beskrivelse": "Daglig leder", "kode": "DAGL"}, "person": {"navn": {"fornavn": "Marius", "etternavn": "Eilertsen"}}}]}]},
        "regnskap": [{"id": 6891134, "regnskapstype": "SELSKAP", "valuta": "NOK", "regnskapsperiode": {"fraDato": "2025-01-01", "tilDato": "2025-12-31"},
                      "resultatregnskapResultat": {"driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": 2119889.0}}}}],
    }

    def fetch(url: str) -> FetchResult:
        body = bodies["roller"] if url.endswith("/roller") else bodies["regnskap"]
        return FetchResult(url=url, status=200, body=body, content_sha256="c" * 64, retrieved_at="t", elapsed_ms=1, bytes_received=1)

    records, _ = fetch_official_modules(ORG, {"roles", "financials"}, fetcher=fetch)
    role = records["roles"]["value"]["roles"][0]
    assert role["claim_span"] == ('$.rollegrupper[0].roller[0].type.beskrivelse="Daglig leder"; '
                                  '$.rollegrupper[0].roller[0].person.navn.fornavn="Marius"; '
                                  '$.rollegrupper[0].roller[0].person.navn.etternavn="Eilertsen"; $.rollegrupper[0].sistEndret="2021-03-10"')
    figures = records["financials"]["value"]["records"][0]["figure_spans"]
    assert figures["revenue"] == "$[0].resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter=2119889.0"


def test_wikidata_claims_cite_one_response_hash_and_the_binding_path():
    data = {"results": {"bindings": [
        {"item": {"value": "http://www.wikidata.org/entity/Q1"}, "orgnr": {"value": ORG}, "linkedin": {"value": "firma-as"},
         "_response": 1, "_index": 3}]},
        "_meta": {"retrieved_at": "t", "content_sha256": "f" * 64, "responses": ["a" * 64, "b" * 64]}}
    record = evidence_for(ORG, build_index(data), data["_meta"])
    assert record["content_sha256"] == "b" * 64  # the response the binding came from, not a combined digest
    assert record["value"]["claim_span"] == ('$.results.bindings[3].item.value="http://www.wikidata.org/entity/Q1"; '
                                             f'$.results.bindings[3].orgnr.value="{ORG}"')
    profile = record["value"]["profiles"][0]
    assert profile["content_sha256"] == "b" * 64 and '$.results.bindings[3].linkedin.value="firma-as"' in profile["claim_span"]


# --- 2. honest states ---------------------------------------------------------------------------------------------

def test_empty_registry_answers_are_not_found_never_complete_with_no_value():
    empty = {"underenheter": {"page": {"totalElements": 0}}, "roller": {"rollegrupper": []}, "regnskap": []}

    def fetch(url: str) -> FetchResult:
        body = empty["underenheter"] if "underenheter" in url else empty["roller"] if url.endswith("/roller") else empty["regnskap"]
        return FetchResult(url=url, status=200, body=body, content_sha256="c" * 64, retrieved_at="t", elapsed_ms=1, bytes_received=1)

    records, _ = fetch_official_modules(ORG, {"locations", "roles", "financials"}, fetcher=fetch)
    assert {module: record["status"] for module, record in records.items()} == {
        "locations": "not_found", "roles": "not_found", "financials": "not_found"}
    assert all(record["value"] is None and record["content_sha256"] for record in records.values())
    assert records["locations"]["note"] == "$.page.totalElements=0 (no registered sub-units)"


def test_no_claim_has_a_value_and_an_unavailable_state():
    profile = make_profile(2, records=None, website=False)
    profile["evidence"]["locations"] = evidence("locations", "not_found", "official_subunits", "https://data.brreg.no/u",
                                                content_sha256="1" * 64, note="$.page.totalElements=0 (no registered sub-units)")
    envelopes = [envelope(profile), *rich_envelopes()]
    for env in envelopes:
        for claim in env["claims"]:
            if claim["availability"] == "available":
                assert claim["value"] not in (None, "", [], {})
            else:
                assert claim["value"] is None
        for module, record in env["profile"]["evidence"].items():
            if record["status"] != "available":
                assert record["value"] is None, module
    assert check_envelopes(envelopes)["state_problems"] == 0
    assert set(AVAILABILITY.values()) <= {"available", "not_available", "blocked", "not_applicable", "failed"}


def test_validator_flags_state_and_value_disagreements():
    env = rich_envelopes()[0]
    broken = copy.deepcopy(env)
    claim = next(c for c in broken["claims"] if c["field"] == "postal_address")
    claim["value"] = "Somewhere 1"  # value under not_available
    broken["profile"]["evidence"]["group"]["value"] = {"sources_checked": []}  # not_found record with a value
    problems = envelope_problems(broken)["state"]
    assert {item["problem"] for item in problems} == {"not_available_with_value", "record_not_found_with_value"}


def test_dated_news_not_found_keeps_what_was_checked_out_of_the_value():
    profile = make_profile(3, website=True)

    def fetch(url: str) -> dict:
        return {"error": "HTTPError: HTTP Error 404: Not Found", "requests": 1}

    record, _ = fetch_dated_news(profile, fetch, sitemaps_for=lambda url: [])
    assert record["status"] == "not_found" and record["value"] is None
    assert record["checked"]["sources_checked"][0]["status"] == "error" and record["content_sha256"] == "b" * 64


# --- 3. sites that are not the company's own ------------------------------------------------------------------------

def unowned_site(footer: str, title: str = "Perfekt Hage") -> dict:
    return evidence("website", "available", "registry_linked_company_website", "https://perfekthage.no/", content_sha256="7" * 64, value={
        "requested_url": "https://perfekthage.no/", "final_url": "https://perfekthage.no/", "title": title, "description": "Anleggsgartner",
        "identity_text_excerpt": footer, "main_text_excerpt": FILLER, "content_sha256": "7" * 64, "structured_organisations": [],
        "social_links": [{"platform": "facebook", "url": "https://facebook.com/perfekthage", "raw": 'href="https://facebook.com/perfekthage"'}],
        "site_contact": {"organisations": [{"logo": "https://perfekthage.no/logo.png"}], "emails": ["post@perfekthage.no"], "phones": ["22334455"]},
        "job_postings": [{"title": "Anleggsgartner"}], "news_items": [{"title": "Ny hage", "url": "https://perfekthage.no/a", "raw_date": "2026-01-01"}],
        "feed_links": ["https://perfekthage.no/feed"],
        "pages": [{"url": "https://perfekthage.no/karriere", "title": "Karriere", "category": "careers", "content_sha256": "8" * 64,
                   "job_postings": [{"title": "Lærling"}], "main_text_excerpt": FILLER,
                   "site_contact": {"emails": ["jobb@perfekthage.no"], "phones": []}}],
    })


def test_site_printing_another_entitys_org_number_and_not_ours_is_never_published():
    profile = {"organisation_number": "829962462", "name": "PERFEKT HAGE AS", "evidence": {}}
    record = unowned_site("Perfekt Hus og Hage AS Midtveien 51 0598 Oslo Org.nr. 925 353 191", title="Perfekt Hage")
    assessment = assess_website_identity({**profile, "evidence": {"website": record}})
    assert assessment["publishable"] is False and assessment["score"] <= 0.6
    assert assessment["other_entity_site"]["organisation_numbers"] == ["925353191"]
    assert "Org.nr. 925 353 191" in assessment["other_entity_site"]["claim_span"]


def test_unowned_site_content_is_quarantined_and_nothing_is_attached():
    profile = make_profile(5)
    profile["evidence"]["website"] = apply_website_identity_gate(
        profile, unowned_site(f"Perfekt Hus og Hage AS · Org.nr. {OTHER[:3]} {OTHER[3:6]} {OTHER[6:]}"))["website"]
    profile["evidence"]["company_jobs"] = company_jobs_record(profile)
    profile["evidence"]["external_profiles"] = external_profiles_record(profile, None)
    env = envelope(profile)
    value = profile["evidence"]["website"]["value"]  # the raw record is quarantined in place ...
    assert value["quarantined"] == {"social_links": 1, "emails": 2, "phones": 1, "jsonld_contacts_and_logos": 1,
                                    "job_postings": 2, "news_items": 1, "feeds": 1}
    assert not any(key in value for key in ("site_contact", "job_postings", "news_items", "feed_links", "description",
                                            "main_text_excerpt", "identity_text_excerpt"))
    assert value["social_links"] == [] and all(set(page) <= {"url", "title", "content_sha256", "category"} for page in value["pages"])
    # ... and v6: the envelope carries no trace of the unowned site at all
    public = env["profile"]["evidence"]["website"]
    assert public["status"] == "not_found" and public["value"] is None and value["final_url"] not in json.dumps(env)
    published = {c["field"] for c in env["claims"] if c["availability"] == "available"}
    assert not published & {"site_email", "site_phone", "site_logo", "site_address", "careers_page", "job_posting", "external_profile",
                            "external_profiles", "company_jobs", "company_activity", "dated_news", "official_website"}
    assert check_envelopes([env])["site_ownership_problems"] == 0
    assert quarantine_unowned_site(profile) == value["quarantined"]  # idempotent


def test_group_site_appears_only_as_the_labelled_group_relationship():
    profile = make_profile(6)
    profile["evidence"]["group"] = evidence("group", "available", "official_group_structure", "https://data.brreg.no/g", content_sha256="6" * 64,
                                            value={"organisasjonsnummer": OTHER, "navn": "Hage Konsern AS",
                                                   "children": [{"organisasjonsnummer": profile["organisation_number"]}]})
    profile["evidence"]["website"] = apply_website_identity_gate(
        profile, unowned_site(f"Hage Konsern AS org.nr {OTHER}", title="Testfirma 6 AS – Hage Konsern"))["website"]
    env = envelope(profile)
    assert not [c for c in env["claims"] if c["field"] == "group_website"]  # v6: another entity's site is never linked
    assert not [c for c in env["claims"] if c["availability"] == "available" and c["field"] in {"site_email", "site_logo", "external_profile",
                                                                                                  "careers_page", "official_website"}]
    assert check_envelopes([env])["passed"]


def test_validator_flags_content_left_on_an_unowned_site():
    profile = make_profile(7)
    profile["evidence"]["website"] = unowned_site(f"Org.nr {OTHER}")
    profile["evidence"]["website"]["value"]["identity_assessment"] = {"publishable": False, "score": 0.6}
    env = terminal_envelope(profile, run_id="t", modules=MODULES, started_at="a", completed_at="b")
    assert check_envelopes([env])["site_ownership_problems"] == 0  # terminal_envelope quarantines defensively
    env["profile"]["evidence"]["website"] = {**profile["evidence"]["website"], "status": "available"}
    env["profile"]["evidence"]["website"]["value"] = {**profile["evidence"]["website"]["value"], "site_contact": {"emails": ["post@perfekthage.no"]}}
    assert envelope_problems(env)["site_ownership"][0]["problem"] == "content_kept_from_site_that_is_not_the_companys_own"


def test_span_check_accepts_name_proof_without_legal_form_and_percent_encoded_hrefs():
    proof = ["Dynabyte Consulting Tjenester Om Dynabyte Referanser"]
    assert span_supports("official_website", "https://dynabyte.no/", proof, "993842621", "DYNABYTE CONSULTING AS")
    assert not span_supports("official_website", "https://dynabyte.no/", ["Dynabyte Tjenester"], "993842621", "DYNABYTE CONSULTING AS")
    assert span_supports("official_website", "https://x.no/", ["Org.nr. 993 842 621"], "993842621", "X AS")
    assert span_supports("external_profile", {"url": "https://facebook.com/p/J%C3%86DER-100034744493193"},
                         ['href="https://www.facebook.com/p/J%C3%86DER-100034744493193/?locale=nb_NO"'])


def test_site_whose_footer_names_another_company_is_never_published_even_without_org_number():
    profile = {"organisation_number": "829962462", "name": "PERFEKT HAGE AS", "evidence": {}}
    record = unowned_site("Perfekt Hus og Hage AS Midtveien 51 0598 Oslo", title="Perfekt Hage – anleggsgartner")
    assessment = assess_website_identity({**profile, "evidence": {"website": record}})
    assert assessment["publishable"] is False and assessment["other_entity_site"]["legal_names"] == ["Perfekt Hus og Hage AS"]
    own = unowned_site("Perfekt Hage AS · Midtveien 51 · 0598 Oslo · Nettside levert av Webbyrå AS", title="Perfekt Hage AS")
    assert assess_website_identity({**profile, "evidence": {"website": own}})["other_entity_site"] is None  # own full name present


def test_name_proof_span_is_found_with_norwegian_letters():
    from norway_company_agent.contract import _website_proof
    value = {"final_url": "https://jaeder.no/", "content_sha256": "a" * 64,
             "identity_text_excerpt": "Kontakt oss Skurvemarka 5 4331 Ålgård Sentralbord: 51 61 16 00 Følg oss © Jæder | Utviklet av Megabite"}
    span, url, sha = _website_proof({"organisation_number": "833598902", "name": "JÆDER AS"}, value)
    assert "© Jæder" in span and url == "https://jaeder.no/" and sha == "a" * 64
    assert span_supports("official_website", "https://jaeder.no/", [span], "833598902", "JÆDER AS")
