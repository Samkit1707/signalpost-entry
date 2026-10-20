# Submission evidence (v3)

Official command (after `uv sync`), run on 20 Oct 2026; the registry bulk snapshot was reused from `cache/` (same day):

```bash
uv run python scripts/run_competition_batch.py --organisations "$INPUT" \
  --profiles-output out/profiles.jsonl --output out/envelopes.jsonl --report out/run-report.json \
  --run-id "$RUN_ID" --expected-count "$COUNT" --deadline-minutes 40
```

| File | Content |
|---|---|
| `smoke-report.json`, `smoke-envelopes.jsonl`, `smoke-run.log` | 100-company smoke: 100/100 envelopes, validation passed, 887 requests, p95 6,065 ms, 259 s, $0 |
| `site/` | the static site the smoke run wrote to `out/site/` — open `site/index.html` |
| `run-1000-report.json` | 1,000-company run: validation passed, 1,000 envelopes, 1,085 s (18 min, deadline 40), 8,269 requests (8.27 per company), p50 186 ms, p95 4,159 ms, 0 companies `budget_exhausted`, 60 verified websites, 17 with dated news |
| `recall-300.json` | per-family recall, v2 (45c0977) vs v3 on the same 300 companies and snapshot (`scripts/measure_recall.py`) |
| `audit-v3.html` | every website newly published on the 300 sample, weakest proof first (`scripts/audit_websites.py`) |
| `synthesis-news-audit.json` | rule audit over the 100 smoke envelopes |
| `tests.txt`, `fresh-clone-check.txt` | offline tests; clone → `uv sync --frozen` → tests → replay → live 5-company run |

`audit-v3.html` was generated before the final rule "a name-derived `.com` publishes only on the printed
organisation number" was added; under that rule its weakest row (safetron.com, name + registry details) is no longer
published, so the 300-sample website count is 21, not 22. springbrands.no shows as "published by v2, not by v3"
because the site answered HTTP 429 on that run, not because of a rule change.

300-company sample, v2 → v3 (companies covered / facts): registry identity 300/1,868 → 300/4,733; contact 0/0 →
110/214; roles 300/1,144 → 300/1,144 (now 1,444 separate role claims); sub-units 222/225 → 222/225 (now one claim
each); financials 299/1,436 → 299/3,968 (every filed year); group 17/51 → 17/51; verified website 18 → 21;
external profiles 10/25 → 13/32; jobs 2/2 → 4/4; news/activity 10/54 → 12/80. Requests per company 6.18 → 8.19
(projected 8,190 per 1,000), p95 1,581 → 3,875 ms.

Smoke audit: 100/100 summaries, 902 sentences all with existing evidence ids; latest accounts = newest filed year
100/100; 91 trend sentences all backed by two filed years; 0 nulls rendered as 0; 29 dated-news claims, 0 off-site;
2,835 fact-level claims, all with evidence and a value. One newly published smoke website (insbo.no) prints the
exact organisation number on its own page.
