"""Offline tests for v3 recall: fact-level claims, ordered website candidates, stricter identity, site contact facts."""
from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from norway_company_agent.batch import terminal_envelope  # noqa: E402
from norway_company_agent.domain_guess import (  # noqa: E402
    discover_website, name_labels, registry_email_domain, website_candidates,
)
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.facts import live_as_bulk_row, registry_facts  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate, assess_website_identity, org_numbers_in  # noqa: E402
from norway_company_agent.website import probe_links, site_contact  # noqa: E402
from test_v2_product import MODULES, account, make_profile  # noqa: E402

ORG = "923609016"  # valid mod-11
OTHER = "914778271"  # valid mod-11
FILLER = " Vi baker ferskt brød hver dag og leverer til kafeer og butikker i hele regionen." * 3


def envelope(profile: dict) -> dict:
    return terminal_envelope(profile, run_id="t", modules=MODULES, started_at="a", completed_at="b")


def facts(env: dict, field: str) -> list[dict]:
    return [claim for claim in env["claims"] if claim["field"] == field and claim.get("fact_level")]


# --- A: fact-level claims ---------------------------------------------------------------------------------------

def test_every_role_location_year_and_registry_attribute_is_its_own_claim():
    profile = make_profile(1, records=[account(2024), account(2025, revenue=None)])
    row = profile["evidence"]["registry"]["value"]
    row.update({"stiftelsesdato": "2003-02-05", "registreringsdatoenhetsregisteret": "2003-04-22", "kapital.belop": "100000.00",
                "kapital.valuta": "NOK", "kapital.antallAksjer": "100", "kapital.innfortDato": "2003-04-22",
                "registrertIMvaRegisteret": "true", "registreringsdatoMerverdiavgiftsregisteret": "2004-03-01",
                "registrertIForetaksregisteret": "true", "registrertIFrivillighetsregisteret": "false",
                "naeringskode2.kode": "71.112", "naeringskode2.beskrivelse": "Rådgivning", "epostadresse": "Post@Firma.no",
                "telefon": "57 69 89 50", "vedtektsfestetFormaal": "Arkitekttjenester.", "overordnetEnhet": OTHER})
    profile["evidence"]["roles"]["value"]["roles"] += [{"name": ["REVISOR AS"], "organisation_number": OTHER, "role": "Revisor",
                                                       "last_changed": "2020-01-01"}, {"name": "Gammel", "role": "Styremedlem", "inactive": True}]
    profile["evidence"]["locations"] = evidence("locations", "available", "official_subunits", "https://data.brreg.no/u", value={"locations": [
        {"organisation_number": "973246534", "name": "Avd Bergen", "address": {"adresse": ["Tomtebu 2"], "postnummer": "6893", "poststed": "VIK"},
         "industry": {"kode": "71.110", "beskrivelse": "Arkitekt"}, "employees": 4, "start_date": "2001-01-01"},
        {"organisation_number": "973246535", "name": "Avd Oslo", "address": {}, "industry": {}, "employees": None}]})
    env = envelope(profile)
    roles = facts(env, "role")
    assert [r["value"]["name"] for r in roles] == ["Kari Nordmann", "REVISOR AS"] and roles[1]["value"]["holder_type"] == "organisation"
    assert roles[1]["as_of"] == "2020-01-01"
    locations = facts(env, "registered_location")
    assert len(locations) == 2 and locations[0]["value"]["nace_code"] == "71.110" and locations[0]["value"]["employees"] == 4
    revenue = facts(env, "annual_revenue")
    assert [(c["value"]["fiscal_year"], c["value"]["amount"]) for c in revenue] == [(2024, 2024000.0)]  # FY2025 null: omitted, not 0
    assert {c["value"]["fiscal_year"] for c in facts(env, "equity")} == {2024, 2025}
    assert facts(env, "annual_revenue")[0]["value"]["period_end"] == "2024-12-31"
    assert facts(env, "founding_date")[0]["value"] == "2003-02-05"
    assert facts(env, "share_capital")[0]["value"]["amount"] == 100000.0
    assert facts(env, "vat_registered")[0]["value"] == {"register": "merverdiavgiftsregisteret", "registered": True, "registered_date": "2004-03-01"}
    assert [c["value"]["register"] for c in facts(env, "register_membership")] == ["foretaksregisteret"]  # 'false' extras skipped
    assert [c["value"]["code"] for c in facts(env, "nace_code")] == ["62.010", "71.112"]
    assert facts(env, "parent_entity")[0]["value"]["organisation_number"] == OTHER
    assert facts(env, "registry_email")[0]["value"] == "post@firma.no"
    ids = {item["id"] for item in env["evidence"]}
    for claim in env["claims"]:
        if claim.get("fact_level"):
            assert claim["availability"] == "available" and claim["value"] is not None and set(claim["evidence_ids"]) <= ids


def test_sole_proprietor_contact_details_are_not_emitted():
    row = {"organisasjonsform.kode": "ENK", "epostadresse": "ola@gmail.com", "telefon": "99 88 77 66"}
    assert not [f for f in registry_facts(row) if f[0] in {"registry_email", "registry_phone"}]


def test_live_registry_extras_flatten_to_bulk_columns():
    row = live_as_bulk_row({"organisation_number": ORG, "legal_form": "AS", "extras": {
        "stiftelsesdato": "2001-01-01", "registrertIMvaregisteret": True, "kapital": {"belop": 30000, "valuta": "NOK"},
        "naeringskode2": {"kode": "1.2", "beskrivelse": "x"}, "aktivitet": ["Linje 1", "Linje 2"]}})
    got = {f[0]: f[1] for f in registry_facts(row)}
    assert got["founding_date"] == "2001-01-01" and got["share_capital"]["amount"] == 30000.0
    assert got["vat_registered"]["registered"] is True and got["registered_activity"] == "Linje 1 Linje 2"


# --- B: candidates and stricter identity ------------------------------------------------------------------------------

def bare(name: str, email: str = "", **extra) -> dict:
    return {"organisation_number": ORG, "name": name, "evidence": {
        "registry": evidence("registry", "available", "official_registry_bulk", "https://data.brreg.no/x",
                             value={"epostadresse": email, "forretningsadresse.postnummer": "4307", "forretningsadresse.poststed": "SANDNES",
                                    "forretningsadresse.adresse": "Strandgata 21", **extra}),
        **({"locations": evidence("locations", "available", "official_subunits", "https://data.brreg.no/u",
                                  value={"locations": [{"name": "Fjordbakeriet Sandnes"}]})} if extra.get("_subunit") else {})}}


def test_candidate_order_email_then_no_then_com_and_generic_email_skipped():
    assert registry_email_domain(bare("Fjord Bakeri AS", "post@gmail.com")) is None
    assert registry_email_domain(bare("Fjord Bakeri AS", "Post@Fjordbakst.NO")) == "fjordbakst.no"
    assert name_labels("Nordlys Fjord Bakeri AS") == ["nordlysfjordbakeri", "nordlys-fjord-bakeri", "nordlysfjord", "nordlys"]
    assert name_labels("Fjord Holding AS") == []
    order = [(c["domain"], c["method"]) for c in website_candidates(bare("Fjord Bakeri AS", "post@fjordbakst.no", _subunit=True),
                                                                     tried={"fjordbakeri.no"})]
    assert order == [("fjordbakst.no", "registry_email_domain"), ("fjord-bakeri.no", "name_variant"), ("fjord.no", "name_variant"),
                     ("fjordbakerietsandnes.no", "subunit_name"), ("fjordbakeri.com", "name_variant"), ("fjord-bakeri.com", "name_variant")]


def site_record(url: str, footer: str = "", pages: list | None = None, title: str = "Velkommen", text: str = FILLER) -> dict:
    return evidence("website", "available", "name_derived_domain_candidate", url, value={
        "requested_url": url, "final_url": url, "title": title, "description": "", "identity_text_excerpt": footer,
        "main_text_excerpt": text, "pages": pages or [], "structured_organisations": []})


def test_dns_precheck_fetch_cap_and_org_number_on_contact_page():
    profile = bare("Fjord Bakeri AS", "post@fjordbakst.no")
    fetched = []

    def fetch(url, source_type, probe=False, probe_terms=None):
        fetched.append((url, probe))
        if "fjordbakst" in url:
            page = {"url": url + "kontakt", "category": "identity", "identity_text_excerpt": f"Fjord Bakeri AS · Org.nr {ORG[:3]} {ORG[3:6]} {ORG[6:]}"}
            return site_record(url, pages=[page]), {"requests": 3, "bytes": 1, "latencies_ms": [1]}
        return site_record(url, title="Noe annet"), {"requests": 2, "bytes": 1, "latencies_ms": [1]}

    accepted, metrics, trail = discover_website(profile, fetch, apply_website_identity_gate, resolver=lambda host: "fjord-bakeri.no" not in host)
    assert accepted and accepted["value"]["identity_assessment"]["score"] == 1.0
    assert fetched[0] == ("https://fjordbakst.no/", True) and fetched[1][1] is False  # probe, then the normal crawl
    assert trail[0]["result"] == "published"

    fetched.clear()
    accepted, _, trail = discover_website(bare("Fjord Bakeri Nordlys Vest AS"), fetch, apply_website_identity_gate, resolver=lambda host: True,
                                          max_fetches=2)
    assert accepted is None and len(fetched) == 2 and any(item["result"] == "skipped_fetch_cap" for item in trail)
    fetched.clear()
    accepted, _, trail = discover_website(bare("Fjord Bakeri AS"), fetch, apply_website_identity_gate, resolver=lambda host: False)
    assert accepted is None and not fetched and all(item["result"] == "no_dns" for item in trail)


def test_name_alone_never_publishes_a_candidate():
    fetch = lambda url, source_type, probe=False, probe_terms=None: (site_record(url, footer="Fjord Bakeri AS", title="Fjord Bakeri AS"),  # noqa: E731
                                                                     {"requests": 2, "bytes": 1, "latencies_ms": []})
    accepted, _, _ = discover_website(bare("Fjord Bakeri AS"), fetch, apply_website_identity_gate, resolver=lambda host: True)
    assert accepted is None


def gate_profile(record: dict, profile: dict | None = None) -> dict:
    profile = profile or bare("Fjord Bakeri AS")
    return assess_website_identity({**profile, "evidence": {**profile["evidence"], "website": record}})


def test_org_number_must_be_a_standalone_token_and_alone_on_the_homepage():
    glued = site_record("https://x.no/", footer=f"Tlf 1{ORG}2", title="Fjord Bakeri AS")
    assert gate_profile(glued)["score"] < 1.0
    multi = site_record("https://x.no/", footer=f"Fjord Bakeri AS org.nr {ORG} · Søster AS org.nr {OTHER}", title="Fjord Bakeri AS")
    assessment = gate_profile(multi)
    assert assessment["publishable"] is False and assessment["score"] <= 0.6
    assert org_numbers_in(f"{ORG} 123456789 {OTHER}") == {ORG, OTHER}  # invalid check digit ignored


def test_group_site_is_labelled_never_published():
    profile = bare("Fjord Bakeri AS")
    profile["evidence"]["group"] = evidence("group", "available", "official_group_structure", "https://data.brreg.no/g",
                                            value={"organisasjonsnummer": OTHER, "navn": "Fjord Konsern AS", "children": [{"organisasjonsnummer": ORG}]})
    record = site_record("https://fjordkonsern.no/", footer=f"Fjord Konsern AS org.nr {OTHER}", title="Fjord Bakeri AS – Fjord Konsern")
    assessment = gate_profile(record, profile)
    assert assessment["publishable"] is False and assessment["group_site"]["organisation_numbers"] == [OTHER]
    gated = apply_website_identity_gate(profile, record)
    full = make_profile(9)
    full["evidence"]["website"] = gated["website"]
    env = envelope(full)
    assert next(c for c in env["claims"] if c["field"] == "official_website")["value"] is None
    assert next(c for c in env["claims"] if c["field"] == "group_website")["value"]["group_organisation_numbers"] == [OTHER]


# --- C: more from each verified site ---------------------------------------------------------------------------------

def test_probe_links_and_site_contact_extraction():
    soup = BeautifulSoup("""<a href="/personvern">Personvern</a><a href="/om-oss">Om oss</a><a href="/kontakt-oss">Kontakt</a>
    <a href="https://other.no/kontakt">x</a><footer><a href="tel:+47 51 68 57 00">Ring</a><a href="mailto:post@firma.no">Post</a>
    <a href="mailto:byraa@webbyraa.no">Laget av</a></footer>""", "lxml")
    assert probe_links("https://firma.no/", soup) == ["https://firma.no/kontakt-oss", "https://firma.no/om-oss"]
    orgs = [{"name": "Firma AS", "url": "https://www.firma.no/", "telephone": "51 68 57 00", "logo": {"url": "https://firma.no/logo.png"},
             "address": {"streetAddress": "Strandgata 21", "postalCode": "4307", "addressLocality": "Sandnes"}},
            {"name": "Webbyrå AS", "url": "https://webbyraa.no/", "telephone": "22 22 22 22"}]
    contact = site_contact("https://firma.no/", soup, orgs)
    assert contact["emails"] == ["post@firma.no"] and contact["phones"] == ["+4751685700"]
    assert contact["organisations"] == [{"name": "Firma AS", "address": "Strandgata 21, 4307 Sandnes", "telephone": "51 68 57 00",
                                         "email": None, "logo": "https://firma.no/logo.png"}]


def test_site_contact_and_job_postings_become_fact_claims_only_on_verified_sites():
    profile = make_profile(3, website=True)
    value = profile["evidence"]["website"]["value"]
    value["site_contact"] = {"organisations": [{"address": "Gate 3, 0150 Oslo", "telephone": "22 33 44 55", "email": None, "logo": None}],
                             "emails": ["post@testfirma3.no"], "phones": ["22334455"]}
    profile["evidence"]["company_jobs"] = evidence("company_jobs", "available", "company_site_careers", "https://testfirma3.no/jobb", value={
        "careers_pages": [{"url": "https://testfirma3.no/jobb", "title": "Jobb"}],
        "job_postings": [{"title": "Utvikler", "date_posted": "2026-10-01", "source_url": "https://testfirma3.no/jobb"}]})
    env = envelope(profile)
    assert [c["value"] for c in facts(env, "site_address")] == ["Gate 3, 0150 Oslo"]
    assert [c["value"] for c in facts(env, "site_email")] == ["post@testfirma3.no"]
    assert [c["value"]["title"] for c in facts(env, "job_posting")] == ["Utvikler"] and facts(env, "job_posting")[0]["as_of"] == "2026-10-01"
    assert facts(env, "careers_page")
    unverified = make_profile(4)
    unverified["evidence"]["website"] = evidence("website", "available", "registry_linked_company_website", "https://x.no/", value={
        "final_url": "https://x.no/", "identity_assessment": {"publishable": False, "score": 0.3}, "site_contact": value["site_contact"]})
    assert not facts(envelope(unverified), "site_email")


def test_name_derived_com_needs_the_printed_org_number():
    footer = "Fjord Bakeri AS · Strandgata 21 · 4307 Sandnes"
    fetch = lambda url, source_type, probe=False, probe_terms=None: (  # noqa: E731
        site_record(url, footer=footer, title="Fjord Bakeri AS"), {"requests": 2, "bytes": 1, "latencies_ms": []})
    # name + two registry details proves a .no candidate ...
    accepted, _, _ = discover_website(bare("Fjord Bakeri AS"), fetch, apply_website_identity_gate, resolver=lambda host: True)
    assert accepted and accepted["source_url"].endswith(".no/")
    # ... but not a .com one
    accepted, _, trail = discover_website(bare("Fjord Bakeri AS"), fetch, apply_website_identity_gate,
                                          resolver=lambda host: host.endswith(".com"))
    assert accepted is None and any(item["candidate"].endswith(".com") and item["result"] == "available" for item in trail)
    with_org = lambda url, source_type, probe=False, probe_terms=None: (  # noqa: E731
        site_record(url, footer=footer + f" · Org.nr {ORG}", title="Fjord Bakeri AS"), {"requests": 2, "bytes": 1, "latencies_ms": []})
    accepted, _, _ = discover_website(bare("Fjord Bakeri AS"), with_org, apply_website_identity_gate, resolver=lambda host: host.endswith(".com"))
    assert accepted and accepted["source_url"].endswith(".com/")
