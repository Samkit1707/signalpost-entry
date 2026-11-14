# Submission evidence (v6)

v6 (14 Nov 2026), after Builderr's v5 review (15 of 73 checked claims about another company). Both examples were rejected
candidates the v5 envelope still carried; v6 envelopes hold only verified links (see README "v6 of this entry").

| File | Content |
|---|---|
| `smoke-report.json`, `smoke-envelopes.jsonl`, `smoke-run.log` | 100-company smoke on v6: 100/100 envelopes, validation passed, 895 requests, p50 255 ms, p95 3,428 ms, 128 s. 3,795 published claims; 0 citation, state or site-ownership problems. Every link in the envelopes is a published claim, Brønnøysund/Wikidata, or a page on the company's verified site |
| `site/` | the static site the v6 smoke run wrote; the summary opens with a sourced key fact |
| `audit-v6.html` | hand-check page for 100 companies never run before: every published website, social profile and news link with proof (25 links), v5-only links on the same companies (1), every rejected candidate (`scripts/build_audit_v6.py`). Checked by hand by the owner: 25/25 correct |
| `tests.txt`, `fresh-clone-check.txt` | offline tests (263 now); clone → `uv sync --frozen` → tests → replay → live 5-company run |

**Coverage update (org-number proof on terms/privacy pages, better domain candidates, retries).** The smoke files, `site/`,
`tests.txt` and `fresh-clone-check.txt` above are now from this update: smoke 100/100, validation passed, 1,208 requests,
p50 267 ms, p95 3,546 ms, 151 s, 3,796 published claims, 0 problems, 10 retries. `audit-coverage.html`: only the links
the update adds on the same 100 unseen companies as `audit-v6.html` (websites 9 → 13, social profiles 12 → 17, news 4 → 13,
careers 0 → 1, none lost), each website with the line that prints its org number; checked by hand by the owner: 19/19 correct.

**Coverage update (more verified news, job postings, sub-unit sites, socials from contact pages).** The smoke files,
`site/`, `tests.txt` and `fresh-clone-check.txt` above are now from this update: smoke 100/100, validation passed, 1,220
requests, p50 273 ms, p95 3,596 ms, 168 s, 3,794 published claims, 0 problems. On the same 100 unseen companies as
`audit-v6.html` (`audit-coverage2.html`): websites 13, social profiles 17, news 13 and careers pages 1, unchanged; 7
sub-unit locations now carry their registry website, each the same domain as the company's verified site; 5 other
sub-unit sites stay in the diagnostics file. News now needs a news/blog section in the URL (or JSON-LD NewsArticle /
BlogPosting at a root-level slug): on the smoke batch 9 items are kept and 8 root-level posts with only a generic Article
type drop.

**Summary sections, site UX and mobile fixes, registry download fallback (14 Nov).** Presentation only: identity rules,
recall and the published links are unchanged (the same 18 links on the smoke batch as the run before). The smoke files,
`site/`, `tests.txt` and `fresh-clone-check.txt` above are now from this update: smoke 100/100, validation passed, 1,203
requests, p50 258 ms, p95 4,241 ms, 199 s, 3,794 published claims, 0 citation, state or site-ownership problems; 100/100
envelopes have a summary. Each summary opens with the buyer fact, then one line on why the year-on-year change matters
(two cited filed years only), what it does, 2–3 labelled signals from sourced numbers, what changed (with direction and
%), and what is unknown with the reason; every sentence carries evidence ids and a source link.
`screenshots/`: index, a rich company (MUSIKK I INNLANDET AS), a sparse company (B FRAKT AS) and the compare view at
desktop width and at 390 px; `screenshots/audit.json` is the headless-Chrome check: no horizontal page scroll, no tap
target under 44 px at 390 px, one h1 and a title per page, search finds "930 856 916", "fjellglod", "fjellgloed" and
"FJELLGLØD", and company pages and all 100 index rows read with JavaScript off. The registry bulk download now always has
a time limit and falls back to an older cached snapshot, then the live registry (offline tests in `tests/test_registry_bulk.py`).

**Registry changes, registry-listed and sub-unit websites, run stages (15 Nov).** The smoke files, `site/`, `tests.txt`
and `fresh-clone-check.txt` above are now from this update: smoke 100/100, validation passed, 1,323 requests, p95
3,753 ms, 176 s, 4,227 published claims, 0 citation, state or site-ownership problems. New on the smoke batch against
the run before (`audit-final.html`): 332 dated registry change events on 100 companies (main-unit feed only, stated
changes only) and one link, NOVOOI AS's Instagram (`instagram.com/novooi.no`, a handle the link guard used to read as
a domain); no link the run before published is lost. A registry website that cannot be read is published from the
registry record only when its domain resolves in DNS and no other company files it; a sub-unit website only when its
site resolves, stays on its domain and prints the sub-unit's or parent's org number or exact legal name (none on the
smoke batch). Identity text no longer reads `<script>`, `<style>` or `<template>` content: on a 1,100 run one website
had been proven only by an org number inside a tracking script's URL, and it is no longer published.
`registry-events-sample.txt`: 10 random published events, each next to the feed entry it cites (re-fetched; same org
number and instant, 10/10). `run-1100-report.json`: 1,100 companies with a cold start, 1,100/1,100 envelopes,
validation passed, 12,962 requests, p95 3,395 ms, 27 min 7 s; the bulk download was cut off by the network and hit its
time limit, so the run took identity basics from the live registry, and the change feed was skipped for all 1,100
companies (it starts only after every company's core registry work, and not past half of `--deadline-minutes`).
`run-1100-report-download.json`: the same 1,100 the hour before (code before the script-text and label fixes), with
the bulk download completing in 8 min: 1,100/1,100, validation passed, 27 min 20 s, change feed also skipped for all.

The v5 evidence below is kept for reference (its smoke files and site were replaced by v6's).

## v5 evidence (2 Nov)

Official command (after `uv sync`), run on 1–2 Nov 2026 with the 20 Oct registry bulk snapshot passed as `--bulk` and the
same Wikidata P2333 cache, so v4 and v5 read the same inputs:

```bash
uv run python scripts/run_competition_batch.py --organisations "$INPUT" \
  --profiles-output out/profiles.jsonl --output out/envelopes.jsonl --report out/run-report.json \
  --run-id "$RUN_ID" --expected-count "$COUNT" --deadline-minutes 40
```

| File | Content |
|---|---|
| `smoke-report.json`, `smoke-envelopes.jsonl`, `smoke-run.log` | 100-company smoke on v5: 100/100 envelopes, validation passed, 912 requests, p50 252 ms, p95 2,295 ms, 107 s, $0. 3,773 published claims; 0 citation, state or site-ownership problems; 0 withheld |
| `site/` | the static site the smoke run wrote; open `site/index.html`. Company pages have a "Links and confidence" table |
| `v5-compare-300.json` | v4 (c194d02) vs v5 on the same 300 companies, run back to back on 2 Nov: recall per family, news and social links before/after, every v4 news/social link v5 no longer publishes with v5's reason, websites per discovery method, run operations (`scripts/compare_v5.py`) |
| `run-1000-report.json` | 1,000-company timing run on v5: 1,000/1,000, validation passed, 8,369 requests, p50 195 ms, p95 1,963 ms, 9 min 34 s, 39,107 published claims, 0 problems |
| `audit-v5.html` | hand-check page for 60 companies never run before (30 random, 30 random with a registry website): every published website, social profile, news and job link with proof and confidence, weakest first, plus every rejected candidate (`scripts/build_audit_v5.py`). Checked by hand |
| `tests.txt`, `fresh-clone-check.txt` | offline tests (204); clone → `uv sync --frozen` → tests → replay → live 5-company run |
| `v4-compare-300.json`, `recall-300-v4.json` | v3 → v4 evidence (23 Oct), kept for reference |
| `run-1000-report-v3.json`, `recall-300.json`, `audit-v3.html` | v3 evidence (20 Oct), kept for reference |

## v4 → v5 on the 300 sample

| | v4 (c194d02) | v5 |
|---|---|---|
| published news links | 67 on 13 companies (45 `dated_news`, 22 `company_activity`) | **11 on 3 companies** (all `dated_news`; 4 high, 7 medium) |
| published social profile links | 26 on 12 companies | **22 on 11 companies** (16 high, 8 medium); `social_profiles` claim on 11 companies, 22 profiles |
| published websites | 23 | 23 (21 high, 2 medium): registry hjemmeside 9, email domain 2, name variant 12 |
| links with `confidence_level` and `proof` | – | all |
| citation / state / site-ownership problems | 0 / 0 / 0 | 0 / 0 / 0 |
| requests, p50, p95 | 2,447, 199 ms, 1,690 ms | 2,489, 254 ms, 1,885 ms |

Recall is unchanged in every other family: registry identity 300/4,733, contact 110/212, roles 299/1,139, locations
221/224, financials 299/3,968, workforce 43/43, group 18/56, website 23/23, jobs 3/3. External profiles go from 12/29 to
11/25 and news/activity from 13/68 to 3/11.

News dropped (56 links), each with v5's reason in `v5-compare-300.json`:
- 22 `company_activity` pages: contact, about, product, store and listing pages, and homepages (`/kontakt/`, `/om-oss/`, `/fa-pristilbud/`,
  `/vare-butikker/`, `/aktuelt/`) that v4 published because they declared a date;
- 18 posts whose text does not name the company (recipes, portfolio and product posts);
- 2 pages that are not articles; 4 not verified within the per-site request cap; 10 older items of sites whose 10 newest
  articles were checked.

Social dropped (5): 3 linked only from the page body, not the header, footer or JSON-LD sameAs; 2 handles naming more
than the company (`jæder-ådne-espeland-as`, `alabasterstudiooslo`). Added (1): `facebook.com/hotspring.no` for NORWEGIAN
SPA AS, matched on the site's brand. Facebook's `facebook.com/p/Name-<id>` page URLs are now read (JÆDER AS).

On the v4 smoke, AIOTA AS had www.aiota.no published; the site now redirects to a rebranded page whose footer names
"Tagtech Solutions AS" and no longer shows the registry phone, so it is not published (v4 rules give the same result today).

---

# Earlier evidence (v4)

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
