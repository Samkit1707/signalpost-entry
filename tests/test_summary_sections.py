"""Offline tests for the sectioned summary (buyer fact, what it does, what changed, unknowns, signals) and the site UX."""
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
from norway_company_agent.synthesis import change_phrase, website_unknown_reason  # noqa: E402


def topic(env: dict, name: str) -> list[dict]:
    return [item for item in env["synthesis"]["sentences"] if item["topic"] == name]


def rich() -> dict:
    return envelope(make_profile(11, records=[account(2024, revenue=1_000_000.0, annual_result=50_000.0, equity=-20_000.0),
                                              account(2025, revenue=1_250_000.0, annual_result=80_000.0, equity=-5_000.0)],
                                 website=True, news=[("Ny avtale signert", "2026-09-01"), ("Nytt kontor", "2026-06-12")]))


def test_every_sentence_is_cited_and_the_buyer_fact_comes_first():
    env = rich()
    ids = {item["id"] for item in env["evidence"]}
    sentences = env["synthesis"]["sentences"]
    assert sentences[0]["topic"] == "headline"
    assert sentences[0]["text"] == "Revenue NOK 1.2m in FY2025, up 25% from FY2024; annual result NOK 80k (Brønnøysund accounts)."
    assert all(item["evidence_ids"] and set(item["evidence_ids"]) <= ids for item in sentences)
    assert {"headline", "what_it_does", "changes", "signal", "unknown"} <= {item["topic"] for item in sentences}


def test_what_it_does_is_one_register_sentence_plus_the_quoted_site_description():
    what = topic(rich(), "what_it_does")
    assert len(what) == 2
    assert what[0]["text"].startswith("Testfirma 11 AS (org. no. 900 000 011) is a private limited company (AS) in Oslo")
    assert "(NACE 62.010 Programmeringstjenester)" in what[0]["text"]
    assert what[1]["text"] == "Its verified website (testfirma11.no) describes it: “Vi lager programvare for norske bedrifter”."


def test_what_changed_states_direction_and_percent_with_each_years_figure_cited():
    env = rich()
    changes = {item["text"].split(":")[0]: item for item in topic(env, "changes")}
    assert changes["Revenue FY2024 → FY2025"]["text"] == "Revenue FY2024 → FY2025: up 25% (NOK 1.0m → NOK 1.2m)."
    assert "up 60%" in changes["Annual result FY2024 → FY2025"]["text"]
    assert "Equity FY2024 → FY2025" in changes
    revenue_ids = changes["Revenue FY2024 → FY2025"]["evidence_ids"]
    assert len(revenue_ids) == 2 and all(i.startswith("ev-financials-") for i in revenue_ids)  # one per filed year
    news = next(item["text"] for item in topic(env, "changes") if item["text"].startswith("News"))
    assert "“Ny avtale signert” (2026-09-01); “Nytt kontor” (2026-06-12)" in news
    assert any(item["text"].startswith("Register status: active") for item in topic(env, "changes"))


def test_change_phrases_name_sign_flips_and_never_divide_by_zero():
    assert change_phrase("annual_result", -2_900_000, 10_100_000, "NOK") == "turned from a loss to a profit (NOK -2.9m → NOK 10.1m)"
    assert change_phrase("equity", 100_000, -50_000, "NOK") == "turned negative (NOK 100k → NOK -50k)"
    assert change_phrase("revenue", 0, 5_000, "NOK").startswith("up from zero")
    assert change_phrase("revenue", 2_024_000, 2_025_000, "NOK") == "up less than 1% (NOK 2,024,000 → NOK 2,025,000)"
    assert change_phrase("annual_result", 8, 8, "NOK") == "unchanged at NOK 8"


def test_signals_are_labelled_numeric_observations_at_most_three():
    env = rich()
    signals = env["synthesis"]["signals"]
    labels = [item["label"] for item in signals]
    assert 2 <= len(signals) <= 3
    assert labels[:3] == ["Negative equity in FY2025", "Profitable two years running", "Revenue up 25% in FY2025"]
    for item in signals:
        assert re.search(r"\d", item["text"]) and item["evidence_ids"]
        assert not re.search(r"\b(will|expect|likely|strong|weak|healthy|impressive)\b", item["text"], re.I)


def test_unknowns_list_each_missing_family_with_a_reason():
    env = envelope(make_profile(12, records=[account(2025)]))
    unknown = {item.get("family"): item["text"] for item in topic(env, "unknown")}
    assert unknown["website"] == "No verified website: the registry lists none."
    assert unknown["news"] == "No dated news: there is no verified website to read it from."
    assert unknown["jobs"].startswith("No job postings:") and unknown["profiles"].startswith("No social profiles:")
    assert unknown["employees"] == "No employee count: the register holds none for this entity."
    assert "Only one filed year (FY2025)" in unknown["financial_trend"]
    assert unknown["registry_history"].startswith("Registry changes: this run reads the current register only")


def test_website_reason_counts_candidates_read_without_naming_them():
    claim = {"availability": "not_available"}
    profile = {"website": None, "website_discovery": {"candidates": [
        {"candidate": "wrongco.no", "result": "available"}, {"candidate": "x.no", "result": "no_dns"}, {"candidate": "y.no", "result": "available"}]}}
    reason = website_unknown_reason(profile, claim)
    assert reason == ("No verified website: the registry lists none, and none of the 2 candidate domains read "
                      "(from the name or registry e-mail) printed the org number.")
    assert "wrongco" not in reason
    assert "did not prove" in website_unknown_reason({"website": "https://a.no"}, claim)
    assert "robots.txt" in website_unknown_reason({}, {"availability": "blocked"})


def test_missing_figures_are_never_zero_in_the_summary():
    env = envelope(make_profile(13, records=[account(2024, revenue=None, annual_result=None, equity=None),
                                             account(2025, revenue=None, annual_result=None, equity=None)]))
    text = env["synthesis"]["narrative"]
    assert "NOK 0" not in text and "not reported" in text
    assert not topic(env, "signal") or all("revenue" not in item["text"].lower() for item in topic(env, "signal"))


def test_compare_facts_say_not_available_with_the_reason():
    facts = {fact["key"]: fact for fact in company_facts(envelope(make_profile(14, records=[account(2025)])))}
    assert facts["website"]["text"] == "Not available" and facts["website"]["reason"] == "The registry lists none."
    assert facts["news"]["reason"] == "There is no verified website to read it from."
    assert facts["employees"]["reason"] == "The register holds none for this entity."
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


def test_company_page_has_sticky_header_summary_block_and_one_tap_sources(tmp_path):
    envs = [rich(), envelope(make_profile(15, records=None))]
    build_site(envs, tmp_path / "site", {"run_id": "t", "completed_at": "2026-11-14T00:00:00Z"})
    page = (tmp_path / "site" / "companies" / f"{envs[0]['organisation_number']}.html").read_text()
    assert '<div class="sticky">' in page and "Org. no. 900 000 011" in page.split('<div class="sticky">')[1][:400]
    block = page.split('<h2 id="summary">Summary · What changed · Unknowns</h2>')[1].split('<h2 id="facts">')[0]
    for heading in ("Buyer fact", "What it does", "Signals", "What changed", "What’s unknown"):
        assert f"<h3>{heading}</h3>" in block
    items = re.findall(r"<(?:li|p class=\"lead\")>(.*?)</(?:li|p)>", block)
    assert items and all('target="_blank"' in item and "↗" in item for item in items)  # each sentence: a source link, one tap
    assert 'class="skip" href="#main"' in page and '<main id=main class="wrap">' in page
    assert page.count("<h1>") == 1 and "<title>" in page
    assert all(' alt="' in tag for tag in re.findall(r"<img[^>]*>", page))
    index = (tmp_path / "site" / "index.html").read_text()
    assert 'id="has-jobs"' in index and 'data-jobs="0"' in index and "<p class=lead>Revenue NOK 1.2m" in index
    data = (tmp_path / "site" / "data" / f"{envs[1]['organisation_number']}.js").read_text()
    payload = json.loads(data[data.index(",{") + 1:data.rindex(");")])
    website = next(fact for fact in payload["facts"] if fact["key"] == "website")
    assert website["text"] == "Not available" and website["reason"] == "The registry lists none."


def test_why_it_matters_is_one_cited_line_from_the_two_filed_years_shown_after_the_buyer_fact(tmp_path):
    env = rich()
    why = topic(env, "why_it_matters")
    assert len(why) == 1 and env["synthesis"]["why_it_matters"] == why[0]["text"]
    assert why[0]["text"].startswith("Why it matters (FY2024 → FY2025): ")
    assert "FY2023" not in why[0]["text"] and not re.search(r"\b(will|expect|likely)\b", why[0]["text"])
    figure_ids = {item["evidence_ids"][0] for item in env["claims"] if item.get("fact_level") and item["field"] in
                  {"annual_revenue", "annual_result", "equity"} and item["value"]["fiscal_year"] in (2024, 2025)}
    assert figure_ids <= set(why[0]["evidence_ids"])
    build_site([env], tmp_path / "site", {"run_id": "t"})
    page = (tmp_path / "site" / "companies" / f"{env['organisation_number']}.html").read_text()
    block = page.split('<h2 id="summary">')[1]
    assert block.index("<h3>Buyer fact</h3>") < block.index("<h3>Why it matters</h3>") < block.index("<h3>What it does</h3>")
    single = envelope(make_profile(16, records=[account(2025)]))
    assert "only filed year" in single["synthesis"]["why_it_matters"]


def test_unchanged_result_reads_plainly_and_signals_do_not_interpret():
    from norway_company_agent.synthesis import why_it_matters
    text = why_it_matters({"period": {"tilDato": "2025-12-31"}, "revenue": 17_368_185.0, "annual_result": 0.0},
                          {"period": {"tilDato": "2024-12-31"}, "revenue": 17_980_000.0, "annual_result": 0.0})
    assert "the annual result was unchanged at NOK 0" in text and "broke even" not in text and "NOK 0 → NOK 0" not in text
    env = envelope(make_profile(17, records=[account(2025, revenue=0.0, annual_result=-6500.0, operating_result=-6500.0)]))
    labels = {item["label"]: item["text"] for item in env["synthesis"]["signals"]}
    assert labels["No operating revenue in FY2025"].endswith("while a result was reported.")
    assert "holding" not in env["synthesis"]["narrative"]
