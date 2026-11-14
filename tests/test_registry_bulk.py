"""Offline tests: automatic Brønnøysund bulk download and envelopes for organisations absent from the snapshot."""
from __future__ import annotations

import csv
import gzip
import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent import registry_bulk  # noqa: E402
from norway_company_agent.batch import profiles_from_bulk  # noqa: E402
from norway_company_agent.evidence import evidence  # noqa: E402


def bulk_bytes(rows: list[dict]) -> bytes:
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=["organisasjonsnummer", "navn", "antallAnsatte", "hjemmeside"])
    writer.writeheader()
    writer.writerows(rows)
    return gzip.compress(text.getvalue().encode("utf-8"))


class FakeResponse:
    def __init__(self, data: bytes, length: int | None = None):
        self._stream = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        return self._stream.read(size)


def fake_opener(payloads: list[tuple[bytes, int | None]], calls: list):
    def opener(request, timeout=None):
        calls.append(request.full_url)
        data, length = payloads[min(len(calls), len(payloads)) - 1]
        return FakeResponse(data, length)
    return opener


ROWS = [{"organisasjonsnummer": "923609016", "navn": "Firma AS", "antallAnsatte": "12", "hjemmeside": ""}]


def test_supplied_snapshot_is_used_without_any_request(tmp_path):
    supplied = tmp_path / "bulk.csv"
    supplied.write_bytes(bulk_bytes(ROWS))
    calls: list = []
    path, report = registry_bulk.ensure_bulk(str(supplied), tmp_path / "cache" / "b.gz", opener=fake_opener([], calls))
    assert path == supplied and report == {"source": "supplied", "path": str(supplied), "requests": 0}
    assert calls == []


def test_missing_snapshot_is_downloaded_once_into_cache(tmp_path):
    calls: list = []
    cache = tmp_path / "cache" / "brreg.csv.gz"
    path, report = registry_bulk.ensure_bulk(str(tmp_path / "absent.csv"), cache,
                                             opener=fake_opener([(bulk_bytes(ROWS), None)], calls))
    assert path == cache and calls == [registry_bulk.BULK_URL]
    assert report["source"] == "downloaded" and report["requests"] == 1 and report["supplied_missing"]
    assert profiles_from_bulk(path, ["923609016"])[0][0]["name"] == "Firma AS"
    assert not cache.with_name(cache.name + ".part").exists()

    again, report2 = registry_bulk.ensure_bulk(None, cache, opener=fake_opener([], calls))
    assert again == cache and report2["source"] == "cache" and report2["requests"] == 0 and len(calls) == 1


def test_stale_cache_is_refreshed(tmp_path):
    cache = tmp_path / "brreg.csv.gz"
    cache.write_bytes(bulk_bytes(ROWS))
    old = time.time() - 48 * 3600
    os.utime(cache, (old, old))
    calls: list = []
    _, report = registry_bulk.ensure_bulk(None, cache, opener=fake_opener([(bulk_bytes(ROWS), None)], calls))
    assert report["source"] == "downloaded" and len(calls) == 1


def test_truncated_download_is_retried_and_never_cached(tmp_path):
    good = bulk_bytes(ROWS)
    calls: list = []
    cache = tmp_path / "brreg.csv.gz"
    _, report = registry_bulk.ensure_bulk(None, cache, sleep=lambda s: None,
                                          opener=fake_opener([(good[:10], len(good)), (good, None)], calls))
    assert report["requests"] == 2 and len(calls) == 2

    calls.clear()
    with pytest.raises(registry_bulk.BulkUnavailable) as exc:
        registry_bulk.ensure_bulk(None, tmp_path / "other.gz", sleep=lambda s: None,
                                  opener=fake_opener([(b"<html>error</html>", None)], calls))
    assert len(calls) == 3 and exc.value.requests == 3 and not (tmp_path / "other.gz").exists()


class SlowResponse(FakeResponse):
    def read(self, size=-1):
        time.sleep(0.05)
        return self._stream.read(16)


def test_slow_download_stops_at_its_time_budget(tmp_path):
    calls: list = []

    def opener(request, timeout=None):
        calls.append(timeout)
        return SlowResponse(bulk_bytes(ROWS) * 50)

    started = time.monotonic()
    with pytest.raises(registry_bulk.BulkUnavailable) as exc:
        registry_bulk.ensure_bulk(None, tmp_path / "slow.gz", opener=opener, sleep=lambda s: None,
                                  deadline_at=time.monotonic() + 0.3)
    assert time.monotonic() - started < 2.0
    assert exc.value.requests == 1 and len(calls) == 1  # no retry once the budget is spent
    assert not (tmp_path / "slow.gz").exists()


def _runner(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "run_competition_batch.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def _run(runner, tmp_path, monkeypatch, *extra: str) -> tuple[dict, list[dict]]:
    inputs = tmp_path / "orgs.txt"
    inputs.write_text("923609016\n999999999\n", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "run", "--organisations", str(inputs), "--output", str(out / "env.jsonl"),
        "--profiles-output", str(out / "profiles.jsonl"), "--report", str(out / "report.json"),
        "--run-id", "t", "--expected-count", "2", "--workers", "1",
        "--modules", "registry,registry_live,website,workforce",
        "--bulk-cache", str(tmp_path / "cache" / "brreg.csv.gz"), "--no-domain-guess", *extra])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 0
    envelopes = [json.loads(line) for line in (out / "env.jsonl").read_text().splitlines()]
    return json.loads((out / "report.json").read_text()), envelopes


def test_deadline_counts_from_process_start_including_the_download(tmp_path, monkeypatch):
    runner = _runner("runner_deadline")
    calls: list = []
    real_ensure = registry_bulk.ensure_bulk
    monkeypatch.setattr(runner, "ensure_bulk", lambda supplied, cache, **kw: real_ensure(
        supplied, cache, opener=fake_opener([(bulk_bytes(ROWS), None)], calls), **kw))
    monkeypatch.setattr(runner, "fetch_official_modules", lambda org, modules: pytest.fail("deadline should stop network work"))
    # Pretend the process started 41 minutes ago: the 40-minute limit is already spent before any download.
    monkeypatch.setattr(runner, "PROCESS_START", time.monotonic() - 41 * 60)
    report, envelopes = _run(runner, tmp_path, monkeypatch, "--deadline-minutes", "40")
    assert calls == []
    assert report["registry_download"]["source"] == "unavailable" and report["registry_download"]["requests"] == 0
    assert report["operations"]["runtime_ms"] >= 41 * 60 * 1000
    assert report["budget"]["stopped_early"] is True and report["validation"]["passed"]
    assert len(envelopes) == 2
    assert {env["modules"]["registry_live"]["state"] for env in envelopes} == {"budget_exhausted"}
    assert {env["modules"]["registry"]["state"] for env in envelopes} == {"source_error"}


def test_slow_download_falls_back_to_live_registry_and_finishes(tmp_path, monkeypatch):
    runner = _runner("runner_fallback")
    real_ensure = registry_bulk.ensure_bulk
    monkeypatch.setattr(runner, "ensure_bulk", lambda supplied, cache, **kw: real_ensure(
        supplied, cache, opener=lambda request, timeout=None: SlowResponse(bulk_bytes(ROWS) * 50), sleep=lambda s: None, **kw))

    def fake_official(org, modules):
        live = {"organisation_number": org, "name": "Firma AS", "legal_form": "AS", "employees": 4, "website": None,
                "bankrupt": False, "liquidating": False, "deleted": False, "deleted_date": None}
        return {m: evidence(m, "available", "official", "https://data.brreg.no/", value=live if m == "registry_live" else {})
                for m in modules}, []

    monkeypatch.setattr(runner, "fetch_official_modules", fake_official)
    monkeypatch.setattr(runner, "fetch_website", lambda url, source_type=None, **_: (
        evidence("website", "not_found", source_type, "https://data.brreg.no/", note="No valid registry website URL"),
        {"requests": 0, "bytes": 0, "latencies_ms": []}))
    # 0.05 min = 3 s limit, so the download may use 0.9 s; the slow fake needs far longer.
    report, envelopes = _run(runner, tmp_path, monkeypatch, "--deadline-minutes", "0.05")
    assert report["registry_download"]["source"] == "unavailable"
    assert report["registry"]["snapshot_unavailable"] is True
    assert report["validation"]["passed"] and report["emitted_envelopes"] == 2
    assert all(env["modules"]["registry"]["state"] == "source_error" for env in envelopes)
    assert all(env["modules"]["registry_live"]["state"] == "complete" for env in envelopes)
    assert envelopes[0]["profile"]["name"] == "Firma AS" and envelopes[0]["profile"]["employees"] == 4


def test_organisation_absent_from_snapshot_still_gets_a_profile(tmp_path):
    bulk = tmp_path / "bulk.csv"
    bulk.write_bytes(bulk_bytes(ROWS))
    profiles, meta = profiles_from_bulk(bulk, ["923609016", "999999999"])
    assert [p["organisation_number"] for p in profiles] == ["923609016", "999999999"]
    assert profiles[1]["evidence"]["registry"]["status"] == "not_found"
    assert profiles[1]["name"] is None and profiles[1]["employees"] is None
    assert meta["absent_from_snapshot"] == ["999999999"] and meta["selected"] == 1


def test_runner_without_bulk_flag_downloads_and_counts_one_request(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("runner_bulk", ROOT / "scripts" / "run_competition_batch.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    calls: list = []
    real_ensure = registry_bulk.ensure_bulk
    monkeypatch.setattr(runner, "ensure_bulk", lambda supplied, cache, **kw: real_ensure(
        supplied, cache, opener=fake_opener([(bulk_bytes(ROWS), None)], calls), **kw))
    monkeypatch.setattr(runner, "fetch_official_modules", lambda org, modules: (
        {m: evidence(m, "available", "official", "https://data.brreg.no/", value={}) for m in modules}, []))
    monkeypatch.setattr(runner, "fetch_website", lambda url, source_type=None, **_: (
        evidence("website", "not_found", source_type, "https://data.brreg.no/", note="No valid registry website URL"),
        {"requests": 0, "bytes": 0, "latencies_ms": []}))
    inputs = tmp_path / "orgs.txt"
    inputs.write_text("923609016\n999999999\n", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "run", "--organisations", str(inputs), "--output", str(out / "env.jsonl"),
        "--profiles-output", str(out / "profiles.jsonl"), "--report", str(out / "report.json"),
        "--run-id", "t", "--expected-count", "2", "--workers", "1", "--modules", "registry,website,workforce",
        "--bulk-cache", str(tmp_path / "cache" / "brreg.csv.gz"), "--no-domain-guess"])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 0
    report = json.loads((out / "report.json").read_text())
    assert report["registry_download"]["source"] == "downloaded" and report["registry_download"]["requests"] == 1
    assert report["operations"]["requests"] == 1
    assert report["registry"]["absent_from_snapshot"] == ["999999999"]
    assert report["validation"]["passed"] and report["emitted_envelopes"] == 2


def test_failed_download_falls_back_to_an_older_cached_snapshot(tmp_path):
    cache = tmp_path / "cache" / "b.gz"
    cache.parent.mkdir()
    cache.write_bytes(bulk_bytes(ROWS))
    old = time.time() - 30 * 3600
    import os
    os.utime(cache, (old, old))
    calls: list = []
    path, report = registry_bulk.ensure_bulk(None, cache, sleep=lambda s: None,
                                             opener=fake_opener([(b"<html>error</html>", None)], calls))
    assert path == cache and report["source"] == "stale_cache" and report["age_hours"] >= 30
    assert len(calls) == 3 and report["requests"] == 3 and len(report["download_errors"]) == 3
    assert cache.read_bytes() == bulk_bytes(ROWS)  # the good snapshot was never overwritten by a failed transfer


def test_slow_download_with_a_stale_cache_stops_at_the_budget_and_uses_the_cache(tmp_path):
    cache = tmp_path / "slow.gz"
    cache.write_bytes(bulk_bytes(ROWS))
    import os
    os.utime(cache, (time.time() - 48 * 3600,) * 2)
    started = time.monotonic()
    path, report = registry_bulk.ensure_bulk(None, cache, sleep=lambda s: None, deadline_at=time.monotonic() + 0.3,
                                             opener=lambda request, timeout=None: SlowResponse(bulk_bytes(ROWS) * 50))
    assert time.monotonic() - started < 2.0 and report["source"] == "stale_cache" and path == cache


def test_runner_always_bounds_the_download_even_without_a_deadline(tmp_path, monkeypatch):
    runner = _runner("runner_bound")
    seen: dict = {}

    def fake(supplied, cache, **kw):
        seen.update(kw)
        raise registry_bulk.BulkUnavailable("no network", 0)

    monkeypatch.setattr(runner, "ensure_bulk", fake)
    monkeypatch.setattr(runner, "fetch_official_modules", lambda org, modules: (
        {m: evidence(m, "not_found", "official", "https://data.brreg.no/", note="offline test") for m in modules}, []))
    monkeypatch.setattr(runner, "fetch_website", lambda url, source_type=None, **_: (
        evidence("website", "not_found", source_type, "https://data.brreg.no/", note="No valid registry website URL"),
        {"requests": 0, "bytes": 0, "latencies_ms": []}))
    report, envelopes = _run(runner, tmp_path, monkeypatch, "--bulk-max-download-minutes", "0.01")
    assert seen["deadline_at"] is not None and seen["deadline_at"] - runner.PROCESS_START <= 0.01 * 60 + 0.001
    assert report["registry_download"]["source"] == "unavailable" and len(envelopes) == 2
