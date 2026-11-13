"""Coverage update: org-number proof on contact/privacy/terms pages, wider domain candidates, one retry.

Identity rules are unchanged: a domain that is not the registry's own entry still needs this exact organisation
number, printed on the site, with no other 9-digit number on that page.
"""
from __future__ import annotations

import io
import socket
import sys
import urllib.error
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent import website  # noqa: E402
from norway_company_agent.domain_guess import discover_website, website_candidates  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate, assess_website_identity  # noqa: E402
from norway_company_agent.website import _retry_wait, org_number_lines, page_shows_org, proof_links  # noqa: E402

ORG = "923609016"


def candidate(pages: list[dict], footer: str = "Fjord Bakeri · Strandgata 21") -> dict:
    return evidence("website", "available", "name_derived_domain_candidate", "https://fjordbakeri.no/", content_sha256="a" * 64, value={
        "requested_url": "https://fjordbakeri.no/", "final_url": "https://fjordbakeri.no/", "registered_domain": "fjordbakeri.no",
        "title": "Fjord Bakeri", "identity_text_excerpt": footer, "main_text_excerpt": "Fjord Bakeri baker brød. " * 10,
        "content_sha256": "a" * 64, "structured_organisations": [], "pages": pages})


def profile() -> dict:
    return {"organisation_number": ORG, "name": "FJORD BAKERI AS", "evidence": {}}


def test_proof_links_rank_contact_about_privacy_terms_imprint():
    soup = BeautifulSoup('<a href="/blogg">Blogg</a><a href="/vilkar">Kjøpsvilkår</a><a href="/personvernerklaering">Personvern</a>'
                         '<a href="https://other.no/kontakt">x</a><a href="/kontakt-oss">Kontakt</a><a href="/hvem">Om oss</a>', "lxml")
    assert proof_links("https://fjordbakeri.no/", soup) == [
        "https://fjordbakeri.no/kontakt-oss", "https://fjordbakeri.no/hvem", "https://fjordbakeri.no/personvernerklaering",
        "https://fjordbakeri.no/vilkar"]


def test_org_number_on_a_privacy_page_proves_a_candidate_and_is_quoted():
    soup = BeautifulSoup("<main><h1>Personvernerklæring</h1><p>Behandlingsansvarlig er Fjord Bakeri AS, org.nr. 923 609 016, "
                         "Strandgata 21, 4307 Sandnes.</p><p>Telefon 51 68 57 00</p></main>", "lxml")
    lines = org_number_lines(soup)
    assert lines == ["Behandlingsansvarlig er Fjord Bakeri AS, org.nr. 923 609 016, Strandgata 21, 4307 Sandnes."]
    page = {"url": "https://fjordbakeri.no/personvern", "category": "identity", "content_sha256": "b" * 64, "identity_text_excerpt": "",
            "org_number_lines": lines}
    assert page_shows_org(page, ORG)
    gated = apply_website_identity_gate(profile(), candidate([page]))
    assessment = gated["assessment"]
    assert assessment["publishable"] and assessment["score"] == 1.0
    assert any("personvern" in reason for reason in assessment["reasons"])


def test_privacy_page_with_another_number_or_only_another_number_does_not_prove():
    two = {"url": "https://fjordbakeri.no/personvern", "category": "identity", "identity_text_excerpt": "",
           "org_number_lines": ["Fjord Bakeri AS, org.nr. 923 609 016", "Databehandler Webbyrå AS, org.nr. 987 654 321"]}
    assert not assess_website_identity({**profile(), "evidence": {"website": candidate([two])}})["publishable"]
    other = {**two, "org_number_lines": ["Databehandler Webbyrå AS, org.nr. 987 654 321"]}
    assert not assess_website_identity({**profile(), "evidence": {"website": candidate([other])}})["publishable"]
    # a name-and-address match without the number still never publishes a guessed domain
    assert not assess_website_identity({**profile(), "evidence": {"website": candidate([], "Fjord Bakeri AS · Strandgata 21, 4307 Sandnes")}})["publishable"]


def test_discovery_passes_the_org_number_and_publishes_on_a_terms_page():
    seen = {}

    def fetch(url, source_type, probe=False, probe_terms=None, proof_org=None, **_):
        seen.setdefault(url, []).append((probe, proof_org))
        page = {"url": url + "salgsbetingelser", "category": "identity", "content_sha256": "c" * 64, "identity_text_excerpt": "",
                "org_number_lines": [f"Selger: Fjord Bakeri AS, organisasjonsnummer {ORG}"]}
        record = candidate([page])
        record["value"].update({"requested_url": url, "final_url": url})
        return record, {"requests": 3, "bytes": 1, "latencies_ms": [5]}

    accepted, _, trail = discover_website(profile(), fetch, apply_website_identity_gate, resolver=lambda host: host == "fjordbakeri.no")
    assert accepted and accepted["value"]["identity_assessment"]["score"] == 1.0
    assert seen["https://fjordbakeri.no/"][0] == (True, ORG)  # the probe looks for the org number; the full crawl keeps its pages
    assert trail[-1]["result"].startswith("published")


def test_candidates_transliterate_and_cap_at_eight_fetches():
    bare = {"organisation_number": ORG, "name": "BØLGEN KAFFEBRENNERI AS", "evidence": {}}
    domains = [item["domain"] for item in website_candidates(bare)]
    assert domains[:4] == ["bolgenkaffebrenneri.no", "bolgen-kaffebrenneri.no", "boelgenkaffebrenneri.no", "boelgen-kaffebrenneri.no"]
    assert domains.index("bolgen.no") < domains.index("bolgenkaffebrenneri.com")
    assert "bolgen.com" not in domains  # single words only on .no
    calls = []

    def fetch(url, **_):
        calls.append(url)
        return candidate([]), {"requests": 1, "bytes": 1, "latencies_ms": [1]}

    discover_website(bare, fetch, apply_website_identity_gate, resolver=lambda host: True)
    assert len(calls) == 8


def test_retry_rules():
    def http_error(code, retry_after=None):
        headers = {"Retry-After": retry_after} if retry_after is not None else {}
        return urllib.error.HTTPError("https://x.no/", code, "x", headers, io.BytesIO(b""))

    assert _retry_wait(http_error(429, "3")) == 3.0
    assert _retry_wait(http_error(503)) == 1.0
    assert _retry_wait(http_error(429, "60")) is None  # longer than 10 s: not retried
    assert _retry_wait(http_error(404)) is None
    assert _retry_wait(socket.timeout("timed out")) == 0.5
    assert _retry_wait(urllib.error.URLError(TimeoutError("timed out"))) == 0.5
    assert _retry_wait(urllib.error.URLError("Name or service not known")) is None


def test_open_with_retry_retries_once(monkeypatch):
    attempts = []

    class Opener:
        def open(self, request, timeout):
            attempts.append(request)
            if len(attempts) == 1:
                raise urllib.error.HTTPError("https://x.no/", 503, "busy", {"Retry-After": "0"}, io.BytesIO(b""))
            return "ok"

    monkeypatch.setattr(website, "SAFE_OPENER", Opener())
    assert website.open_with_retry("req", 5) == "ok" and len(attempts) == 2


def test_guard_keeps_the_org_no_abbreviation_in_proof_quotes():
    from norway_company_agent.public import guard_urls
    env = {"profile": {"evidence": {}}, "claims": [],
           "evidence": [{"id": "e1", "source_url": "https://data.brreg.no/x", "claim_span": "Training Norge AS (org.no. 913 365 798) er behandlingsansvarlig"}]}
    assert guard_urls(env) == [] and "(org.no. 913 365 798)" in env["evidence"][0]["claim_span"]
