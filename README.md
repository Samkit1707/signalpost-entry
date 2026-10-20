# Signalpost reference agent

This is a runnable starting point for the Signalpost company-research challenge. It is intentionally a solid baseline, not a winning submission.

The public universe contains 411,160 eligible companies. Run the starter on 100 companies before submitting. Larger local tests, including 1,000 or more companies, are encouraged but their precomputed profiles are not submitted or scored.

## What it already does

- reads a batch of Norwegian organisation numbers;
- anchors identity in the Brønnøysund bulk registry;
- fetches official financials, roles, group links and registered workplaces;
- visits the registry-listed website and rejects weak entity matches;
- emits one terminal JSONL envelope per input;
- records sources, retrieval times, content hashes, request counts and latency;
- supports checkpoint/resume and a deterministic refresh replay;
- includes examples for external-footprint discovery and an evidence-bounded research agent.

## First run: try one saved example

Requires Python 3.12+. Open a terminal inside this extracted folder.

Before downloading company data or running a full crawl, try the bundled public
sample. It uses saved responses: no API key, registry download or live web requests.

```bash
python3 scripts/run_refresh_replay.py \
  --manifest tests/fixtures/refresh-snapshots.json \
  --output out/refresh-demo.json
```

Open `out/refresh-demo.json`. The `events` list shows what changed between two
versions of one company profile and the source evidence for each change. The sample
should find two expected changes, no false changes, and no extra changes when the
same data is checked again.

The report's `qualification_passed` field refers only to this public sample check.
It does not qualify an entry for the competition or prove live information coverage.
The printed request counts are reads from saved responses, not network calls.

## Next: research live companies

Requires Python 3.12+ and `uv`. This step downloads data and makes live requests.
The manifest selector can create a local test batch of any size. Use 100 rows for the recommended smoke test before trying a larger batch.

```bash
uv sync
curl -L 'https://builderr.ai/signalpost-company-universe-2025.jsonl.gz' -o signalpost-universe.jsonl.gz
# The Brønnøysund bulk registry is downloaded automatically into cache/ on the first run (reused for 24 h).

uv run python select_entry_batch.py \
  --universe signalpost-universe.jsonl.gz \
  --count 100 \
  --output entry-companies.jsonl

# Use the 100-company batch as your smoke test.
cp entry-companies.jsonl smoke-companies.jsonl

uv run python scripts/run_competition_batch.py \
  --organisations smoke-companies.jsonl \
  --profiles-output out/smoke-profiles.jsonl \
  --output out/smoke-envelopes.jsonl \
  --report out/smoke-report.json \
  --run-id smoke-001 \
  --expected-count 100

# You may test at larger scale locally, but Builderr supplies the official batch for scoring.
uv run python scripts/run_competition_batch.py \
  --organisations entry-companies.jsonl \
  --profiles-output out/profiles.jsonl \
  --output out/envelopes.jsonl \
  --report out/run-report.json \
  --run-id local-001 \
  --expected-count 1000

uv run --with pytest pytest -q
```

The original starter archive was clean-room verified by Builderr on August 24, 2026 (104 tests). This entry has **166 tests and 5 subtests passing offline**, and has been run live on a 100-company smoke batch (`submission/smoke-report.json`) and a 998-company rehearsal.

Increase `--count` and `--expected-count` together for a larger local test. The 100-row smoke test above is practice only; Builderr supplies the companies for every official run.

## The improvement loop

1. Treat the organisation number as the anchor.
2. Generate site/profile candidates from official data, the company site, lawful search providers and named people.
3. Save every candidate and the evidence for or against it.
4. Publish only exact-entity matches. Parent, brand, franchise and similarly named companies are not exact.
5. Crawl static HTML first. Escalate to a browser only when a deterministic completeness check fails.
6. Measure added supported coverage, wrong-company claims, runtime, requests and cost.
7. Promote a strategy only when it improves coverage without weakening the accuracy gates.
8. Freeze strategies and thresholds before the daily evaluation run.

The strongest differentiator is external evidence that remains exact and auditable: official company pages, company-owned profiles, jobs, dated activity, ratings/reviews and permitted public signals. Do not trade accuracy for volume.

## Important source rule

Open-source code does not grant permission to scrape a platform. Follow each source's terms, robots policy, rate limits and licence. LinkedIn, Meta and Indeed are useful identity/discovery targets, but direct automated collection may be restricted. Use permitted APIs, licensed providers, company-owned outbound links, or return `blocked`/`not_available`.

Read `docs/competition-control-loop.md`, `docs/external-connectors.md` and the public source policy before adding connectors.

## Submission contract

Submit a repository with:

- a 100-company smoke-test result or report;
- one documented command that accepts a JSONL batch of organisation numbers;
- exactly one terminal envelope per input;
- pinned dependencies and reproducible setup;
- a previous-snapshot input and material-change output;
- a machine-readable run report with runtime, request count and third-party cost;
- declared models, APIs, licences and source-rights assumptions.

Email the repository URL, run command, models/APIs and expected cost per 100-company run to `submit@builderr.ai`.

## Entry additions (v2, v3)

This entry keeps the starter contract and adds exact-entity recall:

| Module | Source | Extra requests | Identity rule |
|---|---|---|---|
| `wikidata_footprint` | Wikidata SPARQL, `P2333` = organisation number (CC0) | ~5 per 1,000 companies (0 with fresh cache) | exact org-number match; numbers on several items → `not_applicable` |
| website fallback | Wikidata `P856` when the registry has no website | normal website crawl | existing identity gate; Wikidata same-domain support can promote a `review` site |
| name-derived `.no` domains | DNS pre-check, then homepage | ≤2 domains per company without any website | **published only when the site prints the exact organisation number, or the full legal name together with at least two independent registry details (postcode+city, street address, phone) in its own footer/contact block** — name alone never; a phone printed next to a person's name does not count |
| `workforce` | employee count in the official bulk registry | 0 | official |
| `external_profiles` | social links on an identity-gated site + exact Wikidata profiles/Wikipedia | 0 | gated site or exact Wikidata |
| `company_activity` | news pages and publication dates the verified site declares | 0 (same bounded crawl) | gated site only |
| `company_jobs` | careers page + schema.org `JobPosting` on the verified site | 0 | gated site only |

Crawler changes: robots.txt fetched once per host (was once per page), one secondary page per
category (identity, news, careers, people/locations), footer/legal text captured so the printed
"Org.nr" can prove identity, and a per-run request/time guard that still emits every envelope
(`budget_exhausted` state).

v3 additions:
- **Politeness and speed.** `--workers` defaults to 16 (the batch is network-bound), but every outbound
  request goes through a per-host slot (`politeness.host_slot`), so no single host (a company site,
  `data.brreg.no`, `query.wikidata.org`) ever has more than 2 requests in flight from us. Brønnøysund
  JSON calls reuse keep-alive connections (`urllib3` is pinned in `uv.lock` 2.7.0 through `trafilatura`).
  Latency metrics start when the slot is acquired, so they measure the source, not our queue.
- **Single-word names.** When a legal name reduces to one word (often also the domain itself), the site
  is `review` (0.85, not published) unless the org number is shown, or a registry postcode+city, street
  address or phone appears in the site's own footer/contact block. Wikidata exact-item promotion still applies.
- **Redirects and subpages.** When the final URL's domain label differs from the requested one (TLD
  ignored, so `epiguard.no` → `epiguard.com` is the same brand), or the crawl lands below the site root
  (chain, directory or parent-group pages such as `systra.com/norway`), the site publishes only on the
  exact organisation number. Name and registry-address matches cap at 0.6, and Wikidata promotion is skipped.
- Every website assessment carries `registry_detail_proof` (`details`, `name_with_detail`, `page`) and
  `landing` (`cross_domain_redirect`, `deep_path_landing`) for audit.
- **Registry details.** A phone number printed right after a person's name (a registry role holder, or a
  capitalised personal name) is a contact person's line and never counts as a registry detail. Street names
  match across `veg`/`vei` spellings (`Håndverkervegen` = `Håndverkerveien`).
- **Registry claims without the bulk file.** When the snapshot is missing or lacks a company, `legal_name`,
  `legal_form`, `industry` and the addresses come from the live Enhetsregisteret entity, with that call as evidence.

### Official run command

```bash
uv sync
uv run python scripts/run_competition_batch.py \
  --organisations "$INPUT" \
  --profiles-output out/profiles.jsonl --output out/envelopes.jsonl --report out/run-report.json \
  --run-id "$RUN_ID" --expected-count "$COUNT" --deadline-minutes 40
```

No data files need to be present: when `--bulk` is omitted (or points to a missing file) the runner downloads the
official Brønnøysund bulk entity CSV (gzip, NLOD 2.0) once into `cache/brreg-enheter.csv.gz`, counts it as one
request, and records it under `registry_download` in the report. Organisation numbers absent from the snapshot
(e.g. deleted since the batch was drawn) still get an envelope with registry state `not_found`.

`--deadline-minutes` counts from process start, so the download is inside the limit. The download may use at
most 30% of it; if the link is slower than that, the run continues without the snapshot (registry
`source_error`) and takes each company's identity basics from the live registry calls. Companies the live registry
reports as deleted (`SlettetEnhet`) get `registry_status` = `{"active": false, "deleted": true, "deleted_date": …}`,
are never crawled on a guessed domain, and are labelled deleted in the viewer.

Optional: `--bulk PATH` (use a supplied snapshot), `--bulk-cache PATH`, `--max-requests N` (global budget),
`--no-domain-guess`, `--wikidata-cache PATH`.
No API keys, no paid services, third-party cost $0.

### v2 of this entry: saved site, sourced synthesis, dated news

The same official command now also writes a **static site** from the run's envelopes into `--site-dir`
(default `out/site/`; `--site-dir ''` disables it). Open `out/site/index.html`; it works from disk, offline.

| Page | What it does |
|---|---|
| `index.html` | search by name or organisation number (spaces and diacritics ignored), status / verified-website / dated-news filters, pick 2–3 companies to compare |
| `compare.html?orgs=a,b,c` | companies side by side; each cell shows the value, a source link, the date it refers to and the retrieval date |
| `companies/<org>.html` | sourced summary, fact table (value · source ↗ · as-of · retrieved · evidence id), every filed year of accounts, dated news, roles, full evidence list |

Pages are responsive (fact tables and the compare grid stack on phones). Missing values read "Not found in checked
sources" (or blocked / not checked), never 0. Built and tested on 300 and 1,000 profiles (`tests/test_v2_product.py`).
Rebuild from saved envelopes: `uv run python scripts/build_site.py --envelopes out/envelopes.jsonl --report out/run-report.json --output out/site`.

**Synthesis** (`synthesis.py`): every envelope has a `synthesis` with short sentences on what the company is and
does, its latest financials, what changed and what is unknown; every sentence lists the evidence ids it rests on.
"Latest" accounts are the newest filed fiscal year (FY2025 when filed; company accounts before group accounts in
the same year). A change is stated only when two filed years of the same account type both report the figure;
a missing figure is "not reported", never 0. The company page shows the same sentences with links to the evidence.

**Dated news** (`news.py`, module `dated_news`): only for websites that passed the identity gate. Reads the RSS/Atom
feed the homepage declares, dated listings on the news page the crawl already fetched (`<time datetime>` tied to
exactly one same-site link), then `sitemap.xml` (Google News entries, or the newest news-like URLs read for the
title and the publication date the article declares). Every item has a title, direct URL, publication date and the
verified site it came from, and is emitted as a `dated_news` claim. Items on another registered domain, future
dates, modification-only dates and undated items are dropped. At most 4 extra requests per verified site, robots.txt
honoured, 2 concurrent requests per host.

Identity and precision rules (website gate, guessed domains, social links, Wikidata) are unchanged from v1.

The starter's `scripts/build_prototype.py` viewer is kept for reference. The new site reuses its evidence-inspection
pattern (source, class, retrieval time, SHA-256), its palette and its diacritic-insensitive search. Unlike that viewer,
the official run writes the site, and "latest" accounts are the newest filed year rather than the first record returned.
