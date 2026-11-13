"""Offline tests for v3: per-host politeness, registry-detail proof for guessed domains, single-word names."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent import http, politeness  # noqa: E402
from norway_company_agent.domain_guess import guess_website  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate, assess_website_identity  # noqa: E402

ORG = "923609016"
FILLER = " Vi baker ferskt brød hver dag og leverer til kafeer og butikker i hele regionen." * 3


def profile(name: str, **row: str) -> dict:
    registry_row = {
        "forretningsadresse.adresse": "Strandgata 21",
        "forretningsadresse.postnummer": "4307",
        "forretningsadresse.poststed": "SANDNES",
        "telefon": "51 68 57 00",
        **row,
    }
    return {"organisation_number": ORG, "name": name,
            "evidence": {"registry": evidence("registry", "available", "official_registry_bulk", "https://data.brreg.no/x", value=registry_row)}}


def site(url: str, title: str, footer: str, pages: list | None = None, source_type: str = "registry_linked_company_website") -> dict:
    return evidence("website", "available", source_type, url, value={
        "final_url": url, "title": title, "description": "", "identity_text_excerpt": footer,
        "main_text_excerpt": title + FILLER, "pages": pages or [], "structured_organisations": [],
    })


class ConcurrencyProbe:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active: dict[str, int] = {}
        self.peak: dict[str, int] = {}

    def enter(self, host: str) -> None:
        with self.lock:
            self.active[host] = self.active.get(host, 0) + 1
            self.peak[host] = max(self.peak.get(host, 0), self.active[host])

    def leave(self, host: str) -> None:
        with self.lock:
            self.active[host] -= 1


def run_threads(target, args_list) -> None:
    threads = [threading.Thread(target=target, args=args) for args in args_list]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_host_slot_caps_two_per_host_but_lets_hosts_run_in_parallel():
    probe = ConcurrencyProbe()

    def work(url: str) -> None:
        with politeness.host_slot(url):
            host = politeness.host_of(url)
            probe.enter(host)
            time.sleep(0.05)
            probe.leave(host)

    urls = [f"https://site{i % 3}.example.no/page{i}" for i in range(18)]
    run_threads(work, [(url,) for url in urls])
    assert max(probe.peak.values()) == politeness.MAX_PER_HOST
    assert len(probe.peak) == 3


def test_fetch_json_respects_per_host_cap(monkeypatch):
    probe = ConcurrencyProbe()

    class FakeResponse:
        status = 200
        data = b'{"ok": true}'

    class FakePool:
        def request(self, method, url, headers=None, timeout=None):
            host = politeness.host_of(url)
            probe.enter(host)
            time.sleep(0.05)
            probe.leave(host)
            return FakeResponse()

    monkeypatch.setattr(http, "_POOL", FakePool())
    results = []
    run_threads(lambda i: results.append(http.fetch_json(f"https://data.brreg.example/api/{i}")), [(i,) for i in range(10)])
    assert len(results) == 10 and all(result.body == {"ok": True} for result in results)
    assert probe.peak["data.brreg.example"] == 2


def guess(name: str, footer: str, pages: list | None = None):
    def fetch(url, source_type=None):
        return site(url, f"{name.title()} – ferskt brød", footer, pages, "name_derived_domain_candidate"), {"requests": 2, "bytes": 100, "latencies_ms": [10]}
    return guess_website(profile(name), fetch, apply_website_identity_gate, resolver=lambda host: True)


def test_v6_guessed_domain_never_published_on_name_and_registry_details():
    # v5 published this (full name + street + postcode + phone); v6: a domain we chose needs the printed org number.
    accepted, _, trail = guess("NORDLYS BAKERI AS", "© 2026 Nordlys Bakeri AS · Strandgata 21, 4307 Sandnes · Tlf 51 68 57 00")
    assert accepted is None and trail[0]["result"] == "available"
    assert any("only the exact organisation number" in reason for reason in trail[0]["reasons"])
    contact = {"url": "https://nordlysbakeri.no/kontakt", "category": "identity",
               "identity_text_excerpt": "Nordlys Bakeri AS, 4307 Sandnes, telefon +47 51 68 57 00"}
    assert guess("NORDLYS BAKERI AS", "© 2026", pages=[contact])[0] is None


def test_guessed_domain_needs_two_registry_details_without_org_number():
    # One detail (street address only, wrong city) — the RESULTAT REGNSKAP case.
    accepted, _, _ = guess("NORDLYS BAKERI AS", "© 2026 Nordlys Bakeri AS · Strandgata 21, 4300 Stavanger")
    assert accepted is None
    phone_only = {"url": "https://nordlysbakeri.no/kontakt", "category": "identity",
                  "identity_text_excerpt": "Nordlys Bakeri AS, telefon +47 51 68 57 00"}
    assert guess("NORDLYS BAKERI AS", "© 2026", pages=[phone_only])[0] is None
    accepted, _, _ = guess("NORDLYS BAKERI AS", "© 2026 Nordlys Bakeri AS · Org.nr 923 609 016")
    assert accepted is not None and accepted["value"]["identity_assessment"]["score"] == 1.0


def test_phone_next_to_a_person_does_not_count_as_registry_detail():
    # The MINIBOLIG case: the registry mobile is a contact person's line, printed under their name.
    footer = "Ring oss Send epost Ole-Johan Langmo 932 60 133 info@nordlysbakeri.no Nordlys Bakeri AS 4307 Sandnes"
    accepted, _, trail = guess("NORDLYS BAKERI AS", footer)
    assert accepted is None
    p = profile("NORDLYS BAKERI AS", telefon="", mobil="932 60 133")
    p["evidence"]["website"] = site("https://nordlysbakeri.no/", "Nordlys Bakeri", footer)
    assert assess_website_identity(p)["registry_detail_proof"]["details"] == ["postcode_city"]
    # Known role holders count as people even with a label in between; company and label words do not.
    from norway_company_agent.identity import phone_belongs_to_person, registry_contact_details
    p["evidence"]["roles"] = evidence("roles", "available", "official_roles", "https://data.brreg.no/r",
                                      value={"roles": [{"name": "Kari Nordmann", "role": "Daglig leder"}]})
    details = registry_contact_details(p)
    assert phone_belongs_to_person("Daglig leder Kari Nordmann, tlf. ", details)
    assert not phone_belongs_to_person("Nordlys Bakeri AS · Tlf ", details)
    assert not phone_belongs_to_person("Kontakt oss: Ring Oss ", details)
    assert not phone_belongs_to_person("Strandgata 21, 4307 Sandnes · Tlf ", details)


def test_guessed_domain_rejected_with_name_alone():
    accepted, _, trail = guess("NORDLYS BAKERI AS", "© 2026 Nordlys Bakeri AS · Alle rettigheter reservert")
    assert accepted is None
    assert all(item["identity_score"] < 1.0 for item in trail)


def test_guessed_domain_rejected_when_footer_details_belong_elsewhere():
    accepted, _, _ = guess("NORDLYS BAKERI AS", "© 2026 Nordlys Bakeri AS · Kongens gate 1, 7011 Trondheim · Tlf 73 00 00 00")
    assert accepted is None


def test_guessed_domain_rejected_when_detail_matches_but_name_is_another_company():
    accepted, _, _ = guess("NORDLYS BAKERI AS", "© 2026 Sandnes Regnskap AS · Strandgata 21, 4307 Sandnes")
    assert accepted is None


def test_single_word_name_needs_registry_detail_unless_org_number_shown():
    def assess(footer: str) -> dict:
        p = profile("AIOTA AS")
        p["evidence"]["website"] = site("https://www.aiota.no/", "Aiota – smarte sensorer", footer)
        return assess_website_identity(p)

    name_only = assess("© 2026 Aiota")
    assert not name_only["publishable"] and name_only["score"] == 0.85  # registry-listed site

    with_postcode = assess("Aiota · 4307 Sandnes")
    assert with_postcode["publishable"] and with_postcode["registry_detail_proof"]["details"] == ["postcode_city"]

    with_org_number = assess("Aiota · Org.nr 923 609 016")
    assert with_org_number["publishable"] and with_org_number["score"] == 1.0


def test_wikidata_chunks_are_posted_not_sent_as_long_get_urls(monkeypatch):
    from norway_company_agent import wikidata

    sent = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"results": {"bindings": []}}'

    def fake_urlopen(request, timeout=None):
        sent.append(request)
        return FakeResponse()

    monkeypatch.setattr(wikidata.urllib.request, "urlopen", fake_urlopen)
    orgs = [str(900000000 + i) for i in range(450)]
    data, metrics = wikidata.fetch_raw(orgs=orgs)
    assert metrics["requests"] == 3 and len(sent) == 3
    assert all(request.get_method() == "POST" and request.full_url == wikidata.SPARQL_URL for request in sent)
    assert all(len(request.full_url) < 100 for request in sent)
    assert b"900000449" in sent[-1].data
    assert data["_meta"]["orgs"] == sorted(orgs)


def landed(name: str, requested: str, final: str, title: str, footer: str, **row: str) -> dict:
    p = profile(name, **row)
    record = site(final, title, footer)
    record["value"]["requested_url"] = requested
    p["evidence"]["website"] = record
    return p


def test_chain_subpage_never_publishes_on_name_haugens_ror():
    p = landed("HAUGENS RØR AS", "https://www.haugensror.no/", "https://www.vvseksperten.no/rorlegger/alta/haugens-ror-as",
               "Haugens Rør AS - Rørlegger i Alta | VVS Eksperten", "Haugens Rør AS · Strandgata 21, 4307 Sandnes")
    assessment = assess_website_identity(p)
    assert not assessment["publishable"]
    assert assessment["landing"] == {"cross_domain_redirect": True, "deep_path_landing": True}


def test_parent_group_subpage_never_publishes_on_name_even_with_wikidata_systra():
    from norway_company_agent.enrichment import apply_wikidata_identity_support

    p = landed("SYSTRA NORGE AS", "https://www.systra.com/norway/", "https://www.systra.com/norway/",
               "SYSTRA Norge AS – engineering", "SYSTRA Norge AS · Strandgata 21, 4307 Sandnes")
    gated = apply_website_identity_gate(p, p["evidence"]["website"])
    assert not gated["assessment"]["publishable"]
    assert gated["assessment"]["landing"] == {"cross_domain_redirect": False, "deep_path_landing": True}
    p["evidence"]["website"] = gated["website"]
    wiki = evidence("wikidata_footprint", "available", "wikidata_org_number_match", "https://www.wikidata.org/wiki/Q1",
                    value={"official_websites": ["https://www.systra.com/"], "wikidata_item": "Q1"})
    apply_wikidata_identity_support(p, wiki)
    assert not p["evidence"]["website"]["value"]["identity_assessment"]["publishable"]


def test_cross_domain_redirect_without_org_number_rejected_olivia_and_hera_store():
    olivia = landed("OLIVIA AS", "https://olivia.no/", "https://oliviarestauranter.no/",
                    "Olivia – italienske restauranter", "Olivia · Strandgata 21, 4307 Sandnes")
    hera = landed("HERA STORE AS", "https://herastore.no/", "https://herastudio.no/",
                  "Hera Store – Hera Studio", "© Hera Store AS · Strandgata 21, 4307 Sandnes")
    for p in (olivia, hera):
        assessment = assess_website_identity(p)
        assert not assessment["publishable"] and assessment["landing"]["cross_domain_redirect"]
        assert assessment["registry_detail_proof"]["details"]  # the address matched, and still is not enough


def test_same_label_other_tld_is_not_a_cross_domain_redirect_epiguard():
    p = landed("EPIGUARD AS", "https://epiguard.no/", "https://epiguard.com/",
               "EpiGuard – smart epilepsy alarm", "EpiGuard · Strandgata 21, 4307 Sandnes")
    assessment = assess_website_identity(p)
    assert assessment["landing"] == {"cross_domain_redirect": False, "deep_path_landing": False}
    assert assessment["publishable"] and assessment["score"] == 0.95


def test_org_number_still_proves_identity_on_cross_domain_deep_page_symfoni():
    p = landed("SYMFONI SOFTWARE AS", "https://www.symfoni.com/", "https://tord91.wixsite.com/symfoni-software-as",
               "Symfoni Software AS", "Symfoni Software AS · Org.nr 923 609 016")
    assessment = assess_website_identity(p)
    assert assessment["landing"] == {"cross_domain_redirect": True, "deep_path_landing": True}
    assert assessment["publishable"] and assessment["score"] == 1.0


def test_language_root_and_index_pages_count_as_site_root():
    for final in ("https://nordlysbakeri.no/no/", "https://nordlysbakeri.no/index.php", "https://nordlysbakeri.no"):
        p = landed("NORDLYS BAKERI AS", "https://nordlysbakeri.no/", final, "Nordlys Bakeri", "© Nordlys Bakeri AS")
        assert assess_website_identity(p)["landing"]["deep_path_landing"] is False


def test_post_box_is_not_a_street_address_and_postcode_needs_its_city():
    p = profile("NORDLYS BAKERI AS", **{"forretningsadresse.adresse": "Postboks 32"})
    p["evidence"]["website"] = site("https://nordlysbakeri.no/", "Nordlys Bakeri", "Nordlys Bakeri AS · Postboks 32 · 4307 Oslo")
    assert assess_website_identity(p)["registry_detail_proof"]["details"] == []


def test_street_matches_across_veg_and_vei_spellings():
    from norway_company_agent.identity import registry_detail_matches, registry_contact_details
    details = registry_contact_details(profile("SJOLAND AS", **{"forretningsadresse.adresse": "Håndverkervegen 12",
                                                                "forretningsadresse.postnummer": "9018",
                                                                "forretningsadresse.poststed": "TROMSØ"}))
    assert registry_detail_matches("Sjøland AS Håndverkerveien 12 9018 Tromsø", details) == ["postcode_city", "street_address"]
    assert registry_detail_matches("Håndverkerveien 14", details) == []
