# Submission evidence (v4)

Official command (after `uv sync`), run on 23 Oct 2026; the registry bulk snapshot of 20 Oct was passed with `--bulk`
so that v3 and v4 read the same snapshot:

```bash
uv run python scripts/run_competition_batch.py --organisations "$INPUT" \
  --profiles-output out/profiles.jsonl --output out/envelopes.jsonl --report out/run-report.json \
  --run-id "$RUN_ID" --expected-count "$COUNT" --deadline-minutes 40
```

| File | Content |
|---|---|
| `smoke-report.json`, `smoke-envelopes.jsonl`, `smoke-run.log` | 100-company smoke on v4: 100/100 envelopes, validation passed, 883 requests, p50 285 ms, p95 3,567 ms, 141 s, $0. Citation audit: 3,798 published claims, 0 missing a hash, 0 missing a span, 0 spans without their value, 0 state/value disagreements, 0 items from sites that are not the company's own, 0 withheld |
| `site/` | the static site the smoke run wrote to `out/site/`; open `site/index.html` |
| `v4-compare-300.json` | v3 (15974f5) vs v4 on the same 300 companies, same day and same snapshot: citation audit before/after, state fixes, every site-ownership change, recall per family and claims withheld per family (`scripts/compare_v4.py`) |
| `recall-300-v4.json` | per-family recall and operations, v3 vs v4 (`scripts/measure_recall.py`) |
| `v4-site-rescan-1000.json` | every website published in the 20 Oct v3 1,000-company run, re-assessed under v4 identity rules (2 of 60 no longer published) |
| `run-1000-report.json`, `recall-300.json`, `audit-v3.html` | v3 evidence (20 Oct), kept for reference |
| `tests.txt`, `fresh-clone-check.txt` | offline tests; clone → `uv sync --frozen` → tests → replay → live 5-company run |

## v3 → v4 on the 300 sample

| | v3 (15974f5) | v4 |
|---|---|---|
| published claims | 11,358 | 11,358 |
| published claims missing `content_sha256` | 3 | **0** |
| published claims missing `claim_span` | 0 | **0** |
| published spans not containing their value | 1,249 | **0** |
| evidence items of unpublished claims missing a hash / a span | 1,438 / 2,355 | **0 / 0** |
| state/value disagreements | 95 (78 `locations` complete with no sub-units, 17 `dated_news` not_found carrying a value) | **0** |
| envelopes keeping content from a site that is not the company's own | 9 | **0** |
| claims withheld as uncitable | – | 0 |
| requests, p50, p95 | 2,441, 249 ms, 3,973 ms | 2,441, 192 ms, 2,813 ms |

Recall (companies covered / facts) is unchanged in every family: registry identity 300/4,733, contact 110/212, roles
299/1,139, locations 222/225, financials 299/3,968, workforce 43/43, group 18/81, website 23/23, external profiles
12/29, jobs 3/3, news/activity 12/60.

Unowned-site content removed from the envelopes (none of it was published as a claim in v3, but it sat in the embedded
profile): ECCERA NORDIC HOLDING AS / eccera.com (3 social links, 5 e-mails, 1 phone, 1 JSON-LD contact+logo),
PERMIAN AS / permian.no (2 social, 1 e-mail), EFACTORY AS / efactory.no (1 social, 1 e-mail), SÅSTADBRÅTEN BORETTSLAG
/ vbbl.no (the housing cooperative's parent: 1 social, 5 e-mails, 3 phones), BORETTSLAGET DALSBERGSTIEN 22 A / solibo.no
(the manager's site, prints org.nr 920 800 572: 2 social, 1 e-mail). No group site occurred in this sample.

1,000-run re-scan (v3 output of 20 Oct): perfekthage.no for PERFEKT HAGE AS (829962462) prints "Perfekt Hus og Hage AS …
Org.nr. 925 353 191", another company, and is no longer published. forebyggskade.no for BEDRIFTSHELSETJENESTEN FOREBYGG
SKADE AS (984195893) names only "Forebygg Skade AS" in its footer (FOREBYGG SKADE ANS, 948638533, is a different
registered company), so it is no longer published: possibly the company's own site, withheld on the strict rule.

p95 cause (v3 smoke 6.1 s, first v4 smoke 7.8 s): guessed-domain probes to hosts that resolve but never answer waited
for the 15 s socket timeout (some twice, 30 s). Probes now time out after 5 s. Smoke p95 is now 3.6 s; websites fetched (11) and
published (7) are the same as v3's smoke.
