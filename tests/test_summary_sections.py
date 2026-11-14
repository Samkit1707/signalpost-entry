"""Offline tests for the five-section summary and the site UX (sections, sorting, filters, static compare)."""
from __future__ import annotations

import json
import re
import subprocess
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_v2_product import account, envelope, make_profile  # noqa: E402
from norway_company_agent.site import APP_JS, build_site, company_facts, search_key  # noqa: E402
from norway_company_agent.synthesis import change_phrase, website_unknown_reason, why_it_matters  # noqa: E402


def topic(env: dict, name: str) -> list[dict]:
    return [item for item in env["synthesis"]["sentences"] if item["topic"] == name]


def rich() -> dict:
    return envelope(make_profile(11, records=[account(2024, revenue=1_000_000.0, annual_result=50_000.0, equity=-20_000.0),
                                              account(2025, revenue=1_250_000.0, annual_result=80_000.0, equity=-5_000.0)],
                                 website=True, news=[("Ny avtale signert", "2026-09-01"), ("Nytt kontor", "2026-06-12")]))


def test_every_sentence_is_cited_in_one_of_five_sections_and_the_buyer_fact_comes_first():
    env = rich()
    ids = {item["id"] for item in env["evidence"]}
    sentences = env["synthesis"]["sentences"]
    assert sentences[0]["topic"] == "headline" and sentences[0]["section"] == "brief"
    assert sentences[0]["text"] == "Revenue NOK 1.2m in FY2025, up 25% from FY2024 (Brønnøysund accounts)."
    assert all(item["evidence_ids"] and set(item["evidence_ids"]) <= ids for item in sentences)
    order = [item["section"] for item in sentences]
    assert order == sorted(order, key=["brief", "financials", "people", "work", "activity"].index)
    assert len(topic(env, "why_it_matters")) == 1


def test_every_figure_appears_once_with_operating_margin_and_equity_ratio():
    env = rich()
    text = " ".join(item["text"] for item in env["synthesis"]["sentences"])
    amounts = re.findall(r"NOK -?[\d.,]+(?:m|k|bn)?", text)
    assert len(amounts) == len(set(amounts)), amounts
    financials = topic(env, "financials")[0]["text"]
    assert financials.startswith("FY2025 (company accounts): operating result NOK 10 (operating margin under 0.1%), annual result NOK 80,000")
    assert "equity ratio" in financials and "revenue" not in financials  # revenue is the buyer fact
    assert "%" not in topic(env, "why_it_matters")[0]["text"] and "NOK" not in topic(env, "why_it_matters")[0]["text"]
    assert all("NOK" not in item["text"] for item in topic(env, "signal"))


def test_what_changed_names_direction_and_only_the_earlier_years_amount():
    env = rich()
    changes = {item["text"].split(":")[0]: item for item in topic(env, "changes")}
    assert "Revenue FY2024 → FY2025" not in changes  # already stated in the buyer fact
    assert changes["Annual result FY2024 → FY2025"]["text"] == "Annual result FY2024 → FY2025: up 60% (was NOK 50k)."
    ids = changes["Annual result FY2024 → FY2025"]["evidence_ids"]
    assert len(ids) == 2 and all(i.startswith("ev-financials-") for i in ids)  # one per filed year
    news = next(item["text"] for item in topic(env, "news"))
    assert "“Ny avtale signert” (2026-09-01); “Nytt kontor” (2026-06-12)" in news


def test_change_phrases_name_sign_flips_and_never_divide_by_zero():
    assert change_phrase("annual_result", -2_900_000, 10_100_000, "NOK") == "turned from a loss to a profit (was NOK -2.9m)"
    assert change_phrase("equity", 100_000, -50_000, "NOK") == "turned negative (was NOK 100k)"
    assert change_phrase("revenue", 0, 5_000, "NOK") == "up from zero"
    assert change_phrase("revenue", 2_024_000, 2_025_000, "NOK") == "up less than 1% (was NOK 2,024,000)"
    assert change_phrase("annual_result", 8, 8, "NOK") == "unchanged"


def test_signals_are_labelled_observations_without_amounts_at_most_three():
    signals = rich()["synthesis"]["signals"]
    assert [item["label"] for item in signals] == ["Negative equity in FY2025", "Profitable two years running", "2 dated news items"]
    for item in signals:
        assert re.search(r"\d", item["text"]) and item["evidence_ids"]
        assert not re.search(r"\b(will|expect|likely|strong|weak|healthy|impressive)\b", item["text"], re.I)


def test_one_short_unknown_line_per_missing_family_in_its_section():
    env = envelope(make_profile(12, records=[account(2025)]))
    unknown = [(item.get("family"), item["section"], item["text"]) for item in topic(env, "unknown")]
    families = [family for family, _, _ in unknown]
    assert len(families) == len(set(families))
    by = {family: (section, text) for family, section, text in unknown}
    assert by["website"] == ("brief", "Website: no verified site — the registry lists none.")
    assert by["news"] == ("activity", "News: no dated item — no verified website to read it from.")
    assert by["employees"] == ("work", "Employees: not registered.")
    assert by["financial_trend"][0] == "financials" and "only filed year" in by["financial_trend"][1]
    assert all(len(text) < 140 for _, _, text in unknown)


def test_website_reason_counts_candidates_read_without_naming_them():
    claim = {"availability": "not_available"}
    profile = {"website": None, "website_discovery": {"candidates": [
        {"candidate": "wrongco.no", "result": "available"}, {"candidate": "x.no", "result": "no_dns"}, {"candidate": "y.no", "result": "available"}]}}
    reason = website_unknown_reason(profile, claim)
    assert reason == "Website: no verified site — the registry lists none, and none of 2 candidate domains read printed the org number."
    assert "wrongco" not in reason
    assert "did not prove" in website_unknown_reason({"website": "https://a.no"}, claim)
    assert "robots.txt" in website_unknown_reason({}, {"availability": "blocked"})
    ambiguous = website_unknown_reason({"website": "https://k.no", "registry_website_ambiguous": {"reason": "k.no is filed by 3 companies"}}, claim)
    assert "filed by 3 companies" in ambiguous


def test_why_it_matters_names_no_amount_and_keeps_its_plain_meaning():
    def year(fy, revenue, result, equity=None):
        return {"period": {"tilDato": f"{fy}-12-31"}, "revenue": revenue, "annual_result": result, "equity": equity, "currency": "NOK"}
    text = why_it_matters(year(2025, 17_368_185.0, 0.0), year(2024, 17_980_000.0, 0.0))
    assert "the annual result was unchanged" in text and "the change in revenue did not move the result" in text and "NOK" not in text
    flip = why_it_matters(year(2025, 900, -50, -10), year(2024, 1000, 80))
    assert "loss-making after a profitable year" in flip and "equity was negative" in flip and "NOK" not in flip


def test_missing_figures_are_never_zero_in_the_summary():
    env = envelope(make_profile(13, records=[account(2024, revenue=None, annual_result=None, equity=None),
                                             account(2025, revenue=None, annual_result=None, equity=None)]))
    text = env["synthesis"]["narrative"]
    assert "NOK 0" not in text and "not reported" in text


def test_compare_facts_say_not_available_with_the_reason():
    facts = {fact["key"]: fact for fact in company_facts(envelope(make_profile(14, records=[account(2025)])))}
    assert facts["website"]["text"] == "Not available" and facts["website"]["reason"] == "No verified site — the registry lists none."
    assert facts["news"]["reason"] == "No dated item — no verified website to read it from."
    assert facts["employees"]["reason"] == "Not registered."
    assert "reason" not in facts["revenue"]


def test_search_key_ignores_spaces_and_both_norwegian_spellings():
    key = search_key("Bærum Sjø & Fjære AS", "930856916", "62.010 Programmering", "Gate 1, 0150 OSLO")
    for query in ("baerum", "barum", "sjo", "sjoe", "fjaere", "barumsjo", "930856916"):
        assert query in key
    assert "Bærum" not in key and " " not in key


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_browser_fold_matches_the_python_search_key():
    fold = re.search(r"const fold=(v=>.*?);\n", APP_JS).group(1)
    script = f"const fold={fold};console.log(JSON.stringify(['Bærum','930 856 916','Sjø-Fjære','Ålesund'].map(fold)))"
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out == ["baerum", "930856916", "sjofjaere", "alesund"]
    key = search_key("Bærum Sjø-Fjære Ålesund AS", "930856916")
    assert all(query in key for query in out)


def build(tmp_path, envs):
    build_site(envs, tmp_path / "site", {"run_id": "t", "completed_at": "2026-11-14T00:00:00Z"})
    return tmp_path / "site"


def test_every_company_page_has_the_same_five_sections_and_a_check_link_per_fact(tmp_path):
    envs = [rich(), envelope(make_profile(15, records=None))]
    site = build(tmp_path, envs)
    for env in envs:
        page = (site / "companies" / f"{env['organisation_number']}.html").read_text()
        headings = re.findall(r'<section class="sec" aria-labelledby="(\w+)"><h2 id="\1">([^<]+)</h2>', page)
        assert [title for _, title in headings] == ["Company brief", "Latest financials", "Who runs it", "Working here", "Recent activity"]
        assert '<div class="sticky">' in page and "Org. no. " in page.split('<div class="sticky">')[1][:400]
        for row in re.findall(r"<tr><td>[^<]+</td><td[^>]*>.*?</td><td>(.*?)</td></tr>", page.split('<h2 id="evidence">')[0]):
            assert row.count("Check the evidence ↗") <= 1
        assert re.search(r'<details><summary>Evidence list \(\d+ records', page)  # the long evidence list is collapsed
        assert 'class="skip" href="#main"' in page and '<main id=main class="wrap">' in page and page.count("<h1>") == 1
    page = (site / "companies" / f"{envs[0]['organisation_number']}.html").read_text()
    block = page.split('<h2 id="brief">')[1]
    assert block.index("<h3>Buyer fact</h3>") < block.index("<h3>Why it matters</h3>") < block.index("<h3>What it does</h3>")
    lines = re.findall(r'<(?:li|p class="lead")>(.*?)</(?:li|p)>', page.split('<h2 id="evidence">')[0])
    assert lines and all("Check the evidence ↗" in item for item in lines if "[ev-" in item)


def test_index_sorts_filters_and_shows_key_figures_without_js(tmp_path):
    envs = [envelope(make_profile(15, records=None)), rich(), envelope(make_profile(16, records=[account(2025)]))]
    index = (build(tmp_path, envs) / "index.html").read_text()
    for control in ('id="sort"', ">Most data found<", ">Name A–Z<", ">Revenue (highest first)<", 'id="place"', 'id="industry"', 'id="area"'):
        assert control in index
    order = re.findall(r'<li class="row" data-org="(\d+)"', index)
    assert order[0] == envs[1]["organisation_number"] and order[-1] == envs[0]["organisation_number"]  # most data found first
    assert "<b>Revenue</b> NOK 1,250,000 (FY2025)" in index and "<b>Place</b> Oslo" in index and "<b>Industry</b> 62.010" in index
    assert 'data-areas="website profiles news' in index or 'data-areas="website news' in index
    stats = index.split('<div class="stats">')[1].split("</div></div>")[0]
    assert "with verified website" in stats and "with register changes" in stats and "with sub-unit websites" in stats


def test_compare_page_is_pre_rendered_and_reads_without_js(tmp_path):
    envs = [rich(), envelope(make_profile(16, records=[account(2025)]))]
    compare = (build(tmp_path, envs) / "compare.html").read_text()
    table = compare.split('<table class="static">')[1]
    assert table.count("<tr><td><a href=\"companies/") == 2 and "NOK 1,250,000" in table and 'class="check"' in table
    assert "Not available" in table and "<noscript>" not in compare
    assert '<div id="pickers" hidden>' in compare  # the side-by-side picker is a JS enhancement


def test_link_targets_are_at_least_44px_on_desktop_too():
    from norway_company_agent.site import CSS
    assert re.search(r"(^|\n)a\{display:inline-flex;align-items:center;min-height:44px\}", CSS)
