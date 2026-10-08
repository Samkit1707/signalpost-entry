"""Offline tests for the OUTPUT_CONTRACT.md claims/evidence fields added to every envelope."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import terminal_envelope  # noqa: E402
from norway_company_agent.contract import availability  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402

ORG = "923609016"
ALLOWED = {"available", "not_available", "blocked", "not_applicable", "ambiguous", "failed"}
EVIDENCE_KEYS = {"id", "source_url", "source_class", "retrieved_at", "content_sha256", "claim_span"}
CLAIM_KEYS = {"field", "value", "availability", "confidence", "evidence_ids"}


def registry_record(**row: str) -> dict:
    base = {"organisasjonsnummer": ORG, "navn": "NORDLYS BAKERI AS", "organisasjonsform.kode": "AS",
            "naeringskode1.kode": "10.710", "naeringskode1.beskrivelse": "Bakerier",
            "forretningsadresse.adresse": "Strandgata 21", "forretningsadresse.postnummer": "4307",
            "forretningsadresse.poststed": "SANDNES", "postadresse.adresse": "", "postadresse.postnummer": "",
            "postadresse.poststed": "", **row}
    return evidence("registry", "available", "official_registry_bulk", "https://data.brreg.no/x", value=base,
                    content_sha256="a" * 64, retrieved_at="2026-10-09T00:00:00Z")


def website_record(publishable: bool, score: float) -> dict:
    return evidence("website", "available", "registry_linked_company_website", "https://nordlysbakeri.no/", value={
        "final_url": "https://nordlysbakeri.no/", "title": "Nordlys Bakeri",
        "identity_text_excerpt": "Nordlys Bakeri AS · Org.nr 923 609 016" if publishable else "Nordlys",
        "identity_assessment": {"publishable": publishable, "score": score, "status": "exact" if publishable else "review"},
        "pages": [],
    }, content_sha256="b" * 64)


def envelope(records: dict) -> dict:
    profile = {"organisation_number": ORG, "name": "NORDLYS BAKERI AS", "evidence": records}
    return terminal_envelope(profile, run_id="t", modules=list(records), started_at="s", completed_at="c")


def claim(env: dict, field: str) -> dict:
    return next(item for item in env["claims"] if item["field"] == field)


def test_state_mapping_matches_contract():
    expected = {
        "complete": "available", "not_found": "not_available", "blocked_robots": "blocked", "blocked_policy": "blocked",
        "not_applicable": "not_applicable", "source_error": "failed", "budget_exhausted": "failed", "submission_error": "failed",
    }
    assert {state: availability(state) for state in expected} == expected


def test_existing_envelope_fields_are_kept_and_contract_fields_added():
    env = envelope({"registry": registry_record(), "website": website_record(True, 1.0)})
    assert {"run_id", "organisation_number", "state", "started_at", "completed_at", "modules", "profile"} <= set(env)
    assert env["modules"]["website"]["state"] == "complete"
    assert all(CLAIM_KEYS <= set(item) and item["availability"] in ALLOWED for item in env["claims"])
    assert all(EVIDENCE_KEYS <= set(item) for item in env["evidence"])
    ids = {item["id"] for item in env["evidence"]}
    assert all(set(item["evidence_ids"]) <= ids for item in env["claims"])


def test_published_website_claim_carries_the_proving_span():
    env = envelope({"registry": registry_record(), "website": website_record(True, 1.0)})
    website = claim(env, "official_website")
    assert website["availability"] == "available" and website["value"] == "https://nordlysbakeri.no/"
    assert website["confidence"] == 1.0
    span = next(item for item in env["evidence"] if item["id"] == website["evidence_ids"][0])["claim_span"]
    assert "923 609 016" in span
    assert next(item for item in env["evidence"] if item["id"] == website["evidence_ids"][0])["source_class"] == "company_owned"


def test_review_website_is_ambiguous_without_a_value():
    env = envelope({"website": website_record(False, 0.85)})
    website = claim(env, "official_website")
    assert website["availability"] == "ambiguous" and website["value"] is None and website["confidence"] is None


def test_missing_values_are_never_zero():
    workforce = evidence("workforce", "not_found", "official_registry_bulk", "https://data.brreg.no/x", note="no count")
    jobs = evidence("company_jobs", "available", "company_website_careers", "https://nordlysbakeri.no/", value={
        "careers_pages": [{"url": "https://nordlysbakeri.no/jobb", "title": "Jobb hos oss", "content_sha256": "c" * 64}],
        "job_postings": [], "open_postings_found": 0})
    env = envelope({"registry": registry_record(), "workforce": workforce, "company_jobs": jobs})
    employees = claim(env, "registered_employees")
    assert employees["availability"] == "not_available" and employees["value"] is None
    postal = claim(env, "postal_address")
    assert postal["availability"] == "not_available" and postal["value"] is None
    jobs_claim = claim(env, "company_jobs")
    assert "open_postings_found" not in jobs_claim["value"]
    assert claim(env, "business_address")["value"] == "Strandgata 21, 4307 SANDNES"


def test_failed_blocked_and_budget_states():
    records = {
        "financials": evidence("financials", "source_error", "official_annual_accounts", "https://data.brreg.no/r", note="HTTP 500"),
        "website": evidence("website", "blocked", "registry_linked_company_website", "https://x.no/", note="robots.txt disallows this user agent"),
        "roles": evidence("roles", "not_fetched", "budget_guard", "about:budget", note="budget_exhausted: run budget reached"),
    }
    env = envelope(records)
    assert claim(env, "latest_annual_accounts")["availability"] == "failed"
    assert claim(env, "official_website")["availability"] == "blocked"
    assert claim(env, "roles")["availability"] == "failed"
    assert all(item["value"] is None for item in env["claims"])


def test_deleted_company_is_recorded_and_never_active():
    import importlib.util

    from norway_company_agent.domain_guess import guess_website
    from norway_company_agent.official import normalize_entity

    body = {"organisasjonsnummer": "928987728", "navn": "SVANHOLMEN 23 AS", "organisasjonsform": {"kode": "AS"},
            "respons_klasse": "SlettetEnhet", "slettedato": "2026-08-24"}
    live_value = normalize_entity(body)
    assert live_value["deleted"] is True and live_value["deleted_date"] == "2026-08-24"
    assert normalize_entity({"organisasjonsnummer": ORG, "navn": "X AS", "konkurs": False})["deleted"] is False

    live = evidence("registry_live", "available", "official_registry_live", "https://data.brreg.no/e", value=live_value)
    status = claim(envelope({"registry_live": live}), "registry_status")
    assert status["availability"] == "available"
    assert status["value"] == {"active": False, "deleted": True, "deleted_date": "2026-08-24"}

    active = evidence("registry_live", "available", "official_registry_live", "https://data.brreg.no/e",
                      value=normalize_entity({"organisasjonsnummer": ORG, "konkurs": False, "underAvvikling": False}))
    assert claim(envelope({"registry_live": active}), "registry_status")["value"] == {
        "bankrupt": False, "liquidating": False, "deleted": False, "active": True}

    deleted_profile = {"organisation_number": "928987728", "name": "SVANHOLMEN 23 AS", "deleted": True}
    assert guess_website(deleted_profile, lambda *a, **k: pytest_fail(), lambda *a: None, resolver=lambda h: True)[0] is None

    spec = importlib.util.spec_from_file_location("viewer", ROOT / "scripts" / "build_prototype.py")
    viewer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(viewer)
    row = {"organisation_number": "928987728", "name": None, "legal_form": None, "employees": None, "municipality": None,
           "industry_code": None, "industry_label": None, "website": None, "bankrupt": None, "liquidating": None,
           "evidence": {"registry_live": live}}
    shown = viewer.compact(row, [])
    assert shown["deleted"] is True and shown["adverse"] is True and shown["deletedDate"] == "2026-08-24"
    assert shown["name"] == "SVANHOLMEN 23 AS"  # never null, so the viewer's name sort cannot break


def pytest_fail():
    raise AssertionError("a deleted company must not be crawled on a guessed domain")


def test_group_tree_lists_parent_and_siblings_but_not_self():
    group = evidence("group", "available", "official_group_structure",
                     f"https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{ORG}", value={
                         "organisasjonsnummer": "911111111", "navn": "MOR AS", "children": [
                             {"organisasjonsnummer": ORG, "navn": "NORDLYS BAKERI AS", "grunnlag": "100 %",
                              "knytningsform": {"beskrivelse": "Konsern datter"}, "children": []},
                             {"organisasjonsnummer": "922222222", "navn": "SØSTER AS", "grunnlag": "60%",
                              "knytningsform": {"beskrivelse": "Konsern datter"}, "children": []}]})
    related = claim(envelope({"group": group}), "group_structure")["value"]
    assert [item["organisation_number"] for item in related] == ["911111111", "922222222"]
    assert related[0]["relation"] == "group top parent"


def test_registry_claims_fall_back_to_live_registry_without_bulk_row():
    missing = evidence("registry", "source_error", "official_registry_bulk", "https://data.brreg.no/x",
                       note="Bulk registry snapshot unavailable")
    live = evidence("registry_live", "available", "official_registry_live",
                    f"https://data.brreg.no/enhetsregisteret/api/enheter/{ORG}", content_sha256="c" * 64, value={
                        "organisation_number": ORG, "name": "NORDLYS BAKERI AS", "legal_form": "AS",
                        "industry": {"kode": "10.710", "beskrivelse": "Bakerier"},
                        "business_address": {"adresse": ["Strandgata 21"], "postnummer": "4307", "poststed": "SANDNES"},
                        "postal_address": None, "bankrupt": False, "liquidating": False, "deleted": False})
    env = envelope({"registry": missing, "registry_live": live})
    assert claim(env, "legal_name")["value"] == "NORDLYS BAKERI AS"
    assert claim(env, "legal_name")["availability"] == "available"
    assert claim(env, "legal_form")["value"] == "AS"
    assert claim(env, "industry")["value"] == {"code": "10.710", "description": "Bakerier"}
    assert claim(env, "business_address")["value"] == "Strandgata 21, 4307 SANDNES"
    assert claim(env, "postal_address")["value"] is None
    assert claim(env, "postal_address")["availability"] == "not_available"
    proof = next(item for item in env["evidence"] if item["id"] in claim(env, "legal_name")["evidence_ids"])
    assert proof["source_url"].endswith(ORG) and proof["source_class"] == "official_registry"
    assert "NORDLYS BAKERI AS" in proof["claim_span"]


def test_registry_claims_stay_failed_without_bulk_or_live_data():
    missing = evidence("registry", "source_error", "official_registry_bulk", "https://data.brreg.no/x", note="down")
    failed_live = evidence("registry_live", "source_error", "official_registry_live", "https://data.brreg.no/y", note="503")
    env = envelope({"registry": missing, "registry_live": failed_live})
    assert claim(env, "legal_name")["value"] is None and claim(env, "legal_name")["availability"] == "failed"
