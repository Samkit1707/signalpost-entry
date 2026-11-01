#!/usr/bin/env python3
"""v4 -> v5 on one batch: recall per family, news and social links before/after, websites per discovery method, and
every v4 news link v5 no longer publishes with the reason v5 gives. Writes JSON and prints a summary.

    uv run python scripts/compare_v5.py --before out/v5eval/before-300-envelopes.jsonl --after out/v5eval/after-300-envelopes.jsonl \
        --before-report out/v5eval/before-300-report.json --after-report out/v5eval/after-300-report.json --output submission/v5-compare-300.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from norway_company_agent.confidence import link_report  # noqa: E402
from measure_recall import measure  # noqa: E402

NEWS_FIELDS = ("dated_news", "company_activity")


def load(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def available(envelope: dict, field: str) -> list[dict]:
    return [claim for claim in envelope.get("claims") or [] if claim.get("field") == field and claim.get("availability") == "available"]


def news_links(envelope: dict) -> dict[str, str]:
    """url -> field for every published news/activity link."""
    links = {}
    for claim in available(envelope, "dated_news"):
        links[claim["value"]["url"]] = "dated_news"
    for claim in available(envelope, "company_activity"):
        for item in claim.get("value") or []:
            links.setdefault(item["url"], "company_activity")
    return links


def social_links(envelope: dict) -> set[str]:
    return {claim["value"]["url"] for claim in available(envelope, "external_profile")
            if (claim.get("value") or {}).get("platform") != "wikipedia"}


def rejected(envelope: dict) -> dict[str, str]:
    return {item.get("url"): item.get("reason") for claim in envelope.get("claims") or [] if claim.get("field") != "social_profiles"
            for item in claim.get("not_published") or []}


def counts(envelopes: list[dict]) -> dict:
    news = [news_links(envelope) for envelope in envelopes]
    social = [social_links(envelope) for envelope in envelopes]
    profiles = [claim for envelope in envelopes for claim in available(envelope, "social_profiles")]
    return {
        "news_links": sum(len(links) for links in news),
        "news_links_by_field": {field: sum(list(links.values()).count(field) for links in news) for field in NEWS_FIELDS},
        "news_companies": sum(1 for links in news if links),
        "social_profile_links": sum(len(links) for links in social),
        "social_companies": sum(1 for links in social if links),
        "social_profiles_claims": {"companies": len(profiles), "profiles": sum(len(claim.get("value") or []) for claim in profiles)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--before-report")
    parser.add_argument("--after-report")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    before, after = load(args.before), load(args.after)
    after_by_org = {envelope["organisation_number"]: envelope for envelope in after}
    dropped_news, added_news, dropped_social, added_social = [], [], [], []
    for old in before:
        org = old["organisation_number"]
        new = after_by_org.get(org) or {}
        name = (old.get("profile") or {}).get("name")
        old_news, new_news = news_links(old), news_links(new)
        reasons = rejected(new)
        dropped_news += [{"org": org, "name": name, "url": url, "v4_field": field, "v5_reason": reasons.get(url, "not a candidate in v5")}
                         for url, field in old_news.items() if url not in new_news]
        added_news += [{"org": org, "name": name, "url": url, "v5_field": field} for url, field in new_news.items() if url not in old_news]
        old_social, new_social = social_links(old), social_links(new)
        dropped_social += [{"org": org, "name": name, "url": url, "v5_reason": reasons.get(url, "not a candidate in v5")}
                           for url in sorted(old_social - new_social)]
        added_social += [{"org": org, "name": name, "url": url} for url in sorted(new_social - old_social)]
    reports = {}
    for label, path in (("v4", args.before_report), ("v5", args.after_report)):
        if path:
            report = json.loads(Path(path).read_text(encoding="utf-8"))
            citations = (report.get("validation") or {}).get("citations") or {}
            reports[label] = {"operations": report.get("operations"), "validation_passed": (report.get("validation") or {}).get("passed"),
                              "citation_problems": citations.get("citation_problems"), "state_problems": citations.get("state_problems"),
                              "site_ownership_problems": citations.get("site_ownership_problems"),
                              "withheld_by_check": len(citations.get("withheld_by_check") or [])}
    result = {
        "companies": {"v4": len(before), "v5": len(after)},
        "recall": {"v4": measure(Path(args.before)), "v5": measure(Path(args.after))},
        "counts": {"v4": counts(before), "v5": counts(after)},
        "links_v5": link_report(after),
        "news_dropped": dropped_news, "news_added": added_news,
        "social_dropped": dropped_social, "social_added": added_social,
        "runs": reports,
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("companies", "counts", "runs")}, ensure_ascii=False, indent=1))
    print(f"news dropped {len(dropped_news)}, added {len(added_news)}; social dropped {len(dropped_social)}, added {len(added_social)}")


if __name__ == "__main__":
    main()
