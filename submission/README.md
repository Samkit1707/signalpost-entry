# Submission evidence (v2)

All files here come from one run of the official command on the 100-company smoke batch (20 Oct 2026):

```bash
uv run python scripts/run_competition_batch.py --organisations smoke-companies.jsonl \
  --profiles-output out/profiles.jsonl --output out/envelopes.jsonl --report out/run-report.json \
  --run-id smoke-v2-2026-10-20 --expected-count 100 --deadline-minutes 40
```

| File | Content |
|---|---|
| `smoke-report.json` | run report: 100/100 envelopes, validation passed, 634 requests, p50 277 ms, p95 1,542 ms, $0; `site` and `synthesis` sections |
| `smoke-envelopes.jsonl` | the 100 envelopes (claims, evidence, `synthesis`, `dated_news` claims) |
| `site/` | the static site the same command wrote to `out/site/` — open `site/index.html` |
| `smoke-run.log` | stdout of the run |
| `synthesis-news-audit.json` | rule audit over the 100 envelopes (see below) |
| `tests.txt` | offline test result |
| `fresh-clone-check.txt` | clone → `uv sync --frozen` → tests → replay demo → command help |

Run time 714 s, of which 499 s was the one-off ~155 MB registry bulk download (slow link, one retry; inside the
40-minute deadline). Without the download the 100 companies took ~3.5 minutes.

Audit (`synthesis-news-audit.json`): 100/100 envelopes have a summary; 627 sentences, 0 without evidence ids,
0 citing ids missing from the envelope; "latest" accounts = newest filed year for 100/100 (FY2025 for 99, FY2024 is
the newest filing for one); 91 change sentences, all backed by two filed years; 0 null figures rendered as 0;
29 `dated_news` claims for 4 companies, 0 off the verified site's domain.

Coverage on this batch: verified website 10 (6 publishable), dated news 4, external profiles 2, company activity 3.
