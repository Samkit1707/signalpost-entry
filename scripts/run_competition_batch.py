#!/usr/bin/env python3
from __future__ import annotations

import time

# The run's time limit counts from process start: imports, the bulk download and every request are inside it.
PROCESS_START = time.monotonic()

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from urllib.parse import urlparse  # noqa: E402
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import (  # noqa: E402
    profile_complete_for_modules,
    profiles_from_bulk,
    profiles_without_snapshot,
    read_organisation_inputs,
    terminal_envelope,
    validate_envelopes,
)
from norway_company_agent.evidence import utc_now  # noqa: E402
from norway_company_agent.identity import apply_website_identity_gate  # noqa: E402
from norway_company_agent.official import fetch_official_modules  # noqa: E402
from norway_company_agent.registry_bulk import BulkUnavailable, ensure_bulk  # noqa: E402
from norway_company_agent.website import fetch_website, normalize_homepage  # noqa: E402
from norway_company_agent.confidence import link_report  # noqa: E402
from norway_company_agent import wikidata as wikidata_source  # noqa: E402
from norway_company_agent.enrichment import (  # noqa: E402
    ENRICHMENT_MODULES,
    apply_wikidata_identity_support,
    company_activity_record,
    quarantine_unowned_site,
    company_jobs_record,
    external_profiles_record,
    wikidata_website_candidate,
    workforce_record,
)
from norway_company_agent.evidence import evidence  # noqa: E402
from norway_company_agent.domain_guess import discover_website  # noqa: E402
from norway_company_agent.news import MAX_REQUESTS as NEWS_MAX_REQUESTS, fetch_dated_news  # noqa: E402
from norway_company_agent.site import build_site  # noqa: E402

DEFAULT_MODULES = ",".join([
    "registry", "accounting_obligation", "registry_live", "financials", "roles", "group", "locations",
    "wikidata_footprint", "website", *ENRICHMENT_MODULES, "dated_news",
])
# Share of --deadline-minutes the bulk download may use; past it the run continues on live registry data.
BULK_DOWNLOAD_SHARE = 0.3
LIVE_IDENTITY_FIELDS = ("name", "legal_form", "employees", "bankrupt", "liquidating", "website", "latest_submitted_accounts")
LOCAL_MODULES = {"registry", "accounting_obligation", "website", "wikidata_footprint", "dated_news", *ENRICHMENT_MODULES}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluator-owned Signalpost batch contract")
    parser.add_argument("--organisations", required=True, help="JSON, JSONL, or text organisation-number list")
    parser.add_argument("--bulk", default=None,
                        help="Brreg bulk entity snapshot (gzip CSV). If omitted or missing, it is downloaded once into --bulk-cache")
    parser.add_argument("--bulk-cache", default="cache/brreg-enheter.csv.gz", help="Where a downloaded bulk snapshot is kept")
    parser.add_argument("--bulk-max-age-hours", type=float, default=24.0, help="Reuse a cached download younger than this")
    parser.add_argument("--output", required=True, help="Terminal envelope JSONL")
    parser.add_argument("--profiles-output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, default=100)
    parser.add_argument("--workers", type=int, default=16, help="Network-bound thread pool; each host still gets at most 2 concurrent requests")
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--modules", default=DEFAULT_MODULES)
    parser.add_argument("--wikidata-cache", default="cache/wikidata-p2333.json",
                        help="Declared cache of the single Wikidata P2333 query; refreshed when older than --wikidata-max-age-hours")
    parser.add_argument("--wikidata-max-age-hours", type=float, default=24.0)
    parser.add_argument("--no-domain-guess", action="store_true", help="Disable name-derived .no domain candidates")
    parser.add_argument("--max-requests", type=int, default=0, help="Global outbound request budget (0 = unlimited)")
    parser.add_argument("--deadline-minutes", type=float, default=0.0, help="Stop new network work after this many minutes (0 = none)")
    parser.add_argument("--site-dir", default="out/site",
                        help="Static site written from this run's envelopes (index, compare view, one page per company); '' disables")
    args = parser.parse_args()
    clock_start = PROCESS_START

    started_at = utc_now()
    organisation_inputs = read_organisation_inputs(args.organisations)
    orgs = [item["organisation_number"] for item in organisation_inputs]
    if len(orgs) != args.expected_count:
        raise SystemExit(f"Expected {args.expected_count} organisations, received {len(orgs)}")
    bulk_deadline = clock_start + args.deadline_minutes * 60 * BULK_DOWNLOAD_SHARE if args.deadline_minutes else None
    try:
        bulk_path, bulk_report = ensure_bulk(args.bulk, args.bulk_cache, max_age_hours=args.bulk_max_age_hours,
                                             deadline_at=bulk_deadline)
        profiles, registry_metadata = profiles_from_bulk(bulk_path, orgs)
    except BulkUnavailable as exc:
        # Every company still gets an envelope; identity basics come from the live registry calls instead.
        bulk_report = {"source": "unavailable", "error": str(exc)[:500], "requests": exc.requests}
        profiles, registry_metadata = profiles_without_snapshot(orgs, str(exc))
    annotations = {item["organisation_number"]: item for item in organisation_inputs}
    for profile in profiles:
        for key in ("evaluation_split", "sample_slice"):
            if key in annotations[profile["organisation_number"]]:
                profile[key] = annotations[profile["organisation_number"]][key]
    requested_modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    fetch_modules = set(requested_modules) - LOCAL_MODULES
    operations = {"requests": bulk_report["requests"], "bytes": bulk_report.get("bytes", 0), "latencies_ms": []}

    # One Wikidata request per batch (zero when the declared cache is fresh). Failure is non-fatal.
    wikidata_index: dict = {}
    wikidata_meta: dict = {}
    wikidata_report: dict = {"enabled": "wikidata_footprint" in requested_modules}
    if wikidata_report["enabled"]:
        cache_path = Path(args.wikidata_cache) if args.wikidata_cache else None
        if cache_path and cache_path.exists() and (time.time() - cache_path.stat().st_mtime) > args.wikidata_max_age_hours * 3600:
            cache_path.unlink()
        try:
            raw, wikidata_metrics = wikidata_source.fetch_raw(cache_path, orgs=orgs)
            wikidata_meta = raw.get("_meta", {})
            wikidata_index = wikidata_source.build_index(raw)
            operations["requests"] += wikidata_metrics["requests"]
            wikidata_report.update({"status": "ok", "indexed_organisations": len(wikidata_index), **wikidata_metrics,
                                    "retrieved_at": wikidata_meta.get("retrieved_at"), "content_sha256": wikidata_meta.get("content_sha256")})
        except Exception as exc:  # noqa: BLE001 - a failed optional source must not drop envelopes
            wikidata_report.update({"status": "source_error", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})

    budget_lock = threading.Lock()
    budget = {"used": operations["requests"], "stopped": False}
    # worst case: 5 official + homepage/robots + 4 pages + 2 guessed domains + dated-news feed/sitemap/articles
    PER_COMPANY_RESERVE = 16 + NEWS_MAX_REQUESTS

    def reserve() -> bool:
        with budget_lock:
            if budget["stopped"]:
                return False
            over_time = args.deadline_minutes and (time.monotonic() - clock_start) > args.deadline_minutes * 60
            over_budget = args.max_requests and budget["used"] + PER_COMPANY_RESERVE > args.max_requests
            if over_time or over_budget:
                budget["stopped"] = True
                return False
            budget["used"] += PER_COMPANY_RESERVE
            return True

    def settle(actual: int) -> None:
        with budget_lock:
            budget["used"] += actual - PER_COMPANY_RESERVE

    def wikidata_record(org: str) -> dict:
        if not wikidata_report["enabled"]:
            return None
        if wikidata_report.get("status") != "ok":
            return evidence("wikidata_footprint", "source_error", "wikidata_org_number_match", wikidata_source.SPARQL_URL,
                            note=wikidata_report.get("error"))
        return wikidata_source.evidence_for(org, wikidata_index, wikidata_meta)

    def enrich(profile: dict) -> tuple[dict, dict]:
        org = profile["organisation_number"]
        wiki = wikidata_record(org)
        if wiki is not None:
            profile["evidence"]["wikidata_footprint"] = wiki
        if not reserve():
            note = "budget_exhausted: run request/time budget reached before this company was fetched"
            for module in [*fetch_modules, *[name for name in ("website", "dated_news") if name in requested_modules]]:
                profile["evidence"][module] = evidence(module, "not_fetched", "budget_guard", "about:budget", note=note)
            metrics, website_metrics = [], {"requests": 0, "bytes": 0, "latencies_ms": []}
        else:
            records, metrics = fetch_official_modules(org, fetch_modules)
            profile["evidence"].update(records)
            live = records.get("registry_live") or {}
            if live.get("status") == "available":
                live_value = live.get("value") or {}
                if live_value.get("deleted"):
                    profile["deleted"], profile["deleted_date"] = True, live_value.get("deleted_date")
                if profile.get("name") is None:  # no bulk row: take identity basics from the live registry
                    for key in LIVE_IDENTITY_FIELDS:
                        if profile.get(key) is None and live_value.get(key) is not None:
                            profile[key] = live_value[key]
            website_metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
            if "website" in requested_modules:
                url = profile.get("website")
                source_type = "registry_linked_company_website"
                if not url:
                    candidate = wikidata_website_candidate(wiki)
                    if candidate:
                        url, source_type = candidate, "wikidata_linked_company_website"
                website_record, website_metrics = fetch_website(url, source_type=source_type)
                profile["evidence"]["website"] = apply_website_identity_gate(profile, website_record)["website"]
                apply_wikidata_identity_support(profile, wiki)
                current = profile["evidence"]["website"]
                published = bool(((current.get("value") or {}).get("identity_assessment") or {}).get("publishable"))
                wiki_url = wikidata_website_candidate(wiki)
                fetched_urls = [(current.get("value") or {}).get("final_url")]
                if not published and wiki_url and source_type != "wikidata_linked_company_website" \
                        and (urlparse(wiki_url).hostname or "").removeprefix("www.") != (urlparse(normalize_homepage(url) or "").hostname or "").removeprefix("www."):
                    # v5: the registry website failed or was not proven; the exact Wikidata item's P856 is the next candidate
                    wiki_record, wiki_metrics = fetch_website(wiki_url, source_type="wikidata_linked_company_website")
                    for key in ("requests", "bytes"):
                        website_metrics[key] += wiki_metrics[key]
                    website_metrics["latencies_ms"] = website_metrics["latencies_ms"] + wiki_metrics["latencies_ms"]
                    gated = apply_website_identity_gate(profile, wiki_record)["website"]
                    fetched_urls.append((gated.get("value") or {}).get("final_url"))
                    if ((gated.get("value") or {}).get("identity_assessment") or {}).get("publishable"):
                        profile["evidence"]["website"] = current = gated
                        published = True
                    url = url or wiki_url
                if not published and not args.no_domain_guess:
                    # only hosts a page was actually fetched from: a registry 'www.' host that did not resolve must not
                    # stop the email-domain candidate on the bare domain (300 sample: www.boliginkasso.no -> boliginkasso.no)
                    tried = {urlparse(normalize_homepage(item) or "").hostname or "" for item in fetched_urls if item}
                    tried = {host.removeprefix("www.") for host in tried if host}
                    deadline_ok = lambda: not (args.deadline_minutes and (time.monotonic() - clock_start) > args.deadline_minutes * 60)  # noqa: E731
                    accepted, guess_metrics, trail = discover_website(profile, fetch_website, apply_website_identity_gate,
                                                                      tried=tried, keep_going=deadline_ok)
                    for key in ("requests", "bytes"):
                        website_metrics[key] += guess_metrics[key]
                    website_metrics["latencies_ms"] = website_metrics["latencies_ms"] + guess_metrics["latencies_ms"]
                    if trail:
                        profile["website_discovery"] = {"method": "ordered_candidates_with_org_number_proof_v3", "candidates": trail,
                                                        "replaced": None}
                    if accepted:
                        if current.get("status") == "available":
                            profile["website_discovery"]["replaced"] = {
                                "url": (current.get("value") or {}).get("final_url"), "source_type": current.get("source_type"),
                                "identity_status": ((current.get("value") or {}).get("identity_assessment") or {}).get("status")}
                        profile["evidence"]["website"] = accepted
                quarantine_unowned_site(profile)
            if "dated_news" in requested_modules:
                news_record, news_metrics = fetch_dated_news(profile)
                profile["evidence"]["dated_news"] = news_record
                for key in ("requests", "bytes"):
                    website_metrics[key] += news_metrics[key]
                website_metrics["latencies_ms"] = website_metrics["latencies_ms"] + news_metrics["latencies_ms"]
            settle(len(metrics) + website_metrics["requests"])
        builders = {
            "workforce": lambda: workforce_record(profile),
            "external_profiles": lambda: external_profiles_record(profile, wiki),
            "company_activity": lambda: company_activity_record(profile),
            "company_jobs": lambda: company_jobs_record(profile),
        }
        for module, build in builders.items():
            if module in requested_modules:
                profile["evidence"][module] = build()
        metric = {
            "requests": len(metrics) + website_metrics["requests"],
            "bytes": sum(item.bytes_received for item in metrics) + website_metrics["bytes"],
            "latencies_ms": [item.elapsed_ms for item in metrics] + website_metrics["latencies_ms"],
        }
        profile["run_metrics"] = metric
        return profile, metric

    state: dict[str, dict] = {}
    resumed_profiles = 0
    profiles_output = Path(args.profiles_output)
    if args.resume and profiles_output.exists():
        prior = [json.loads(line) for line in profiles_output.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not set(item["organisation_number"] for item in prior).issubset(set(orgs)):
            raise SystemExit("Resume profile membership is not a subset of this batch")
        state = {
            item["organisation_number"]: item
            for item in prior
            if profile_complete_for_modules(item, requested_modules)
        }
        resumed_profiles = len(state)
    pending_profiles = [profile for profile in profiles if profile["organisation_number"] not in state]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(enrich, profile): profile["organisation_number"] for profile in pending_profiles}
        for index, future in enumerate(as_completed(futures), 1):
            profile, metric = future.result()
            state[profile["organisation_number"]] = profile
            operations["requests"] += metric["requests"]
            operations["bytes"] += metric["bytes"]
            operations["latencies_ms"].extend(metric["latencies_ms"])
            if index % args.checkpoint_every == 0 or index == len(pending_profiles):
                checkpoint = [state[org] for org in orgs if org in state]
                write_jsonl(profiles_output, checkpoint)

    completed_at = utc_now()
    ordered_profiles = [state[org] for org in orgs]
    envelopes = [
        terminal_envelope(profile, run_id=args.run_id, modules=requested_modules, started_at=started_at, completed_at=completed_at)
        for profile in ordered_profiles
    ]
    validation = validate_envelopes(envelopes, args.expected_count)
    write_jsonl(profiles_output, ordered_profiles)
    write_jsonl(Path(args.output), envelopes)
    latencies = sorted(operations.pop("latencies_ms"))
    operations["p50_ms"] = latencies[len(latencies) // 2] if latencies else None
    operations["p95_ms"] = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
    operations["requests_per_company"] = round(operations["requests"] / max(1, len(pending_profiles)), 2)
    operations["projected_requests_for_1000"] = round(operations["requests_per_company"] * 1000)
    operations["runtime_ms"] = int((time.monotonic() - clock_start) * 1000)
    report = {
        "run_id": args.run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "expected_count": args.expected_count,
        "emitted_envelopes": len(envelopes),
        "resumed_profiles": resumed_profiles,
        "profiles_fetched_this_run": len(pending_profiles),
        "modules": requested_modules,
        "registry": registry_metadata,
        "registry_download": bulk_report,
        "operations": operations,
        "budget": {"max_requests": args.max_requests or None, "deadline_minutes": args.deadline_minutes or None,
                   "stopped_early": budget["stopped"]},
        "wikidata": wikidata_report,
        "coverage": {
            module: sum(1 for profile in ordered_profiles if (profile.get("evidence", {}).get(module) or {}).get("status") == "available")
            for module in requested_modules
        },
        "validation": validation,
        "links": link_report(envelopes),
    }
    if args.site_dir:
        # The saved site is built from exactly these envelopes; a site failure is reported, never drops envelopes.
        try:
            report["site"] = {"status": "ok", "path": str(Path(args.site_dir) / "index.html"),
                              **build_site(envelopes, args.site_dir, {"run_id": args.run_id, "completed_at": completed_at})}
        except Exception as exc:  # noqa: BLE001
            report["site"] = {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    report["synthesis"] = {"envelopes_with_summary": sum(1 for item in envelopes if item["synthesis"]["sentences"]),
                           "sentences": sum(len(item["synthesis"]["sentences"]) for item in envelopes)}
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if validation["passed"] else 1)


if __name__ == "__main__":
    main()
