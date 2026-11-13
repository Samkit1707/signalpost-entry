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

The original starter archive was clean-room verified by Builderr on August 24, 2026 (104 tests). This entry has **196 tests and 5 subtests passing offline**, and has been run live on a 100-company smoke batch (`submission/smoke-report.json`) and a 998-company rehearsal.

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

### v3 of this entry: fact-level recall, more verified websites, stricter identity

**Fact-level claims** (`facts.py`). Next to the unchanged aggregate claims, every fact is also its own claim
(`fact_level: true`), each with its own evidence span and `as_of` date:

| Source | Claims (one per fact) |
|---|---|
| Roles API | `role` per active role holder (name, role, person/organisation; `as_of` = the role group's last-changed date — the API has no per-role start date) |
| Sub-units API | `registered_location` per underenhet (name, address, municipality, NACE, employees, start date) |
| Accounts register | `annual_revenue`, `operating_result`, `annual_result`, `equity`, `total_assets` per filed year, with reporting period; null figures omitted, never 0 |
| Registry snapshot (or live entity) | `nace_code` (all ranks), `founding_date`, `registration_date`, `share_capital`, `vat_registered`, `register_membership`, `institutional_sector`, `registered_purpose`, `registered_activity`, `auditor_opt_out`, `registry_email`/`registry_phone` (not for sole proprietorships), `parent_entity` |
| Group structure | `group_relationship` per related entity (relationship label), `group_website` for a site labelled as the group's |
| Verified site | `external_profile` per profile, `careers_page`, `job_posting`, `site_address`/`site_phone`/`site_email`/`site_logo` (JSON-LD Organization on the site's own domain, own-domain `mailto:`, footer `tel:`) |

**Website candidates** (`domain_guess.discover_website`), after the registry website and Wikidata, cheapest first:
the registry e-mail domain (generic providers such as gmail.com and online.no skipped), then name variants on `.no`
(joined, hyphenated, first two tokens, first distinctive token, sub-unit names), then `.com`. DNS is checked first
(apex, then `www.`); at most 6 candidate fetches per company; the run deadline is checked before each candidate.
A candidate is probed with its homepage and at most two contact/about/legal pages, and is published only when one of
those pages prints the exact organisation number, or the full legal name with two registry details in the site's
own footer/contact block. A name-derived `.com` publishes only on the printed organisation number. Accepted sites
then get the normal crawl (news, careers, people pages).

**Stricter identity rules** (apply to every site): the organisation number must be a standalone token (no longer a
substring of all homepage digits run together); a homepage listing other organisation numbers next to this one is a
multi-entity page and is never published; a site showing the organisation number of the parent or of a group member
(Brønnøysund konsernstruktur, overordnet enhet) is labelled `group_website` and never published as the company's own.

Measurement on 300 companies (before = v2, after = v3, same batch and snapshot): `submission/recall-300.json`;
audit of every newly published site: `submission/audit-v3.html`. Tools: `scripts/measure_recall.py`,
`scripts/audit_websites.py`.

### v4 of this entry: exact citations, honest states, nothing from sites that are not the company's own

**Citations** (`citations.py`, `contract.py`). Every claim has its own evidence entry. `content_sha256` is the hash
of the response the value was read from (the bulk CSV download, one registry/API JSON response, one SPARQL response,
or the one web page the value is on, not the homepage by default). `claim_span` contains the value exactly, and
`span_kind` says how:

| `span_kind` | Sources | Example |
|---|---|---|
| `field_path` | bulk registry CSV, Brønnøysund/Regnskapsregisteret JSON, Wikidata SPARQL JSON | `row[organisasjonsnummer=926765663].navn="PROSPECTUS MANAGEMENT AS"`, `$[0].resultatregnskapResultat.driftsresultat.driftsinntekter.sumDriftsinntekter=2119889.0`, `$.results.bindings[3].linkedin.value="firma-as"` |
| `quote` | company web pages, feeds, sitemaps | `© 2025 Prospectus Management AS · Org.nr. 926 765 663`, `href="mailto:post@prospectus.no"`, `JSON-LD Organization.telephone="51 68 57 00"`, `<title>…</title> <pubDate>…</pubDate>` |
| `absence` | claims with no value: what was checked | `row[…].postadresse.adresse=""`, `$.page.totalElements=0 (no registered sub-units)` |

A value that cannot be cited this way is not published. An aggregate claim then becomes `ambiguous` with `value: null`
and a `note`, and a fact-level claim is left out. Both are listed in the envelope's `uncited_withheld`.
`accounting_obligation` now cites the bulk-row cells it reads (the rule page itself is not fetched, so it is named in
`rule_source` and carries no hash).

**Citation check, never a run failure** (`citations.py`, called in `batch.terminal_envelope`). Before the synthesis is
built, every claim is checked. Problem claims are withheld in place, so the check never fails validation, never changes
the exit code and never drops an envelope. Problems are: a published claim without a hash or span, a span that does not
contain the value, a state that disagrees with the value, or a claim from a site that is not the company's own. A
withheld claim gets `value: null` and state `ambiguous` (a value existed) or `not_available` (none), plus a `note`.
Records are repaired the same way: a not-found record never carries a value (it moves to `checked`), and an available
record with nothing in it becomes `not_found`. Every action is logged in the envelope (`citation_withheld`) and in the
run report (`validation.citations.withheld_by_check`, `uncited_withheld`), next to the audit counts, which are 0 after
withholding. Only the six contract states can appear; `not_found` maps to `not_available`.

**Honest states.** A 200 answer with nothing in it (no sub-units, no roles, no accounts) is now `not_found`, not
`complete` with an empty value. A `dated_news` check that found nothing keeps what it checked in `checked`, not in `value`.

**Sites that are not the company's own.** After the identity decision, a fetched site that did not pass it (a group/parent
site, another entity's site, or an unverified site) keeps only its URL, title, hash, page list and identity verdict.
Its social links, e-mails, phones, JSON-LD contacts/logos, job postings, news items, feeds and page text are dropped,
and only counts are kept in `quarantined`. A group site appears only as the labelled `group_website` claim, cited by the
quoted text that shows the group's organisation number. A new stricter identity rule also applies: a site whose own
footer/legal text prints organisation numbers, none of them this company's, is another entity's site and is never
published (e.g. perfekthage.no prints "Perfekt Hus og Hage AS … Org.nr. 925 353 191"; v3 had published it for
PERFEKT HAGE AS, 829962462, on a name match). Without any organisation number, a site proven only by name is not
published when its own footer/legal text names a different legal entity ("… AS/ASA/ANS/DA/SA …") and this company's
full legal name, legal form included, appears nowhere on the site.

Guessed-domain probes use a 5 s socket timeout (registry-listed sites keep 15 s). On the smoke batch, guessed hosts that
resolve but never answer sat at the 15 s timeout and set p95 at 6–8 s.

Measurement, v3 (15974f5) vs v4 on the same 300 companies and snapshot: `submission/v4-compare-300.json` and
`submission/recall-300-v4.json` (`scripts/compare_v4.py`, `scripts/measure_recall.py`).

### v5 of this entry: confidence on every link, stricter news, social profiles that show

**Confidence on every link** (`confidence.py`, `contract.label_link_claims`). Every published website, social profile,
news item, careers page, job posting and site contact detail carries `confidence_level` (`high` or `medium`), a matching
numeric `confidence` (0.95 / 0.8) and a `proof` ("linked from https://conow.no/ footer", "JSON-LD sameAs on …",
"the site prints this organisation number"). The website claim also carries `discovery_method` (`registry_hjemmeside`,
`registry_email_domain`, `wikidata_p856`, `name_variant`, `subunit_name`). A link whose confidence cannot be established
is not published: the claim becomes `not_available` with a note. Candidates that were found but failed a rule are
listed on the family's claim as `not_published` (URL and reason, never a value). The run report has a `links` section:
published links per family and level, websites per discovery method, candidates not published.

**News** (`news.py`, `enrichment.company_activity_record`). A news item or activity article is published only if:
1. it is an article: a news/blog/press/artikkel/nyheter path or article markup (`og:type article`, JSON-LD Article
   types), never a contact/about/product/services/listing page;
2. it has a publication date from the article page, feed or sitemap (modification dates are not used);
3. a paragraph of the article body names the company (legal name, or the verified site's brand of 4+ characters), and
   that paragraph is not boilerplate: the same text block on 2+ other pages of the site does not count. Pages store
   hashes of their visible text blocks (`block_hashes`) for this check.

High confidence needs a news path, the legal name and at least 2 other site pages to compare against; anything else
that passes is medium. In v4 the contact and product links came from `company_activity`, which published any crawled
page with a declared date.

**Social profiles** (`identity.assess_social_identity`, `website._social_links`). Facebook, LinkedIn, Instagram,
YouTube, X and TikTok links count only from the verified site's header, footer or JSON-LD `sameAs`, or from the exact
Wikidata item (P2333). Platforms are never fetched. A handle is high when it equals the legal name or the site's brand
(registered-domain label), optionally with a suffix such as `as`, `norge`, `official`. It is medium when it is made
only of legal-name words, the brand and such suffixes, with at least one distinctive word. Numeric ids, YouTube `UC…`
channel ids, people's handles and handles with other words (another branch or region) are rejected. LinkedIn slugs that
drop ø/æ/å and Facebook's `Name-<page id>` URLs are handled. Profiles are published as `external_profile` (one claim
each) and `external_profiles` as before, and also as **`social_profiles`**: the same links with confidence and proof,
social platforms only. Company pages show each profile as a clickable link with its confidence and proof.

**Websites.** The Wikidata P856 website is now tried when the registry-listed site exists but does not pass the identity
gate. Email-domain and name-variant discovery record why each candidate failed (`note` in the discovery trail).

**Synthesis.** A `why_it_matters` sentence per company, built only from the two filed years' cited revenue, result and
equity ("Revenue fell (-43.9%) while the loss narrowed: it lost less on lower revenue"). With one filed year it says
no change can be judged. No forecasts.

Measurement, v4 (c194d02) vs v5 on the same 300 companies, snapshot and day: `submission/v5-compare-300.json`
(`scripts/compare_v5.py`). Hand-check page for 60 never-run companies: `submission/audit-v5.html`
(`scripts/build_audit_v5.py`).

### v6 of this entry: verified-only envelopes, no wrong-company links, buyer-first summary

Builderr's v5 review found 15 of 73 checked claims about another company. Both examples were **rejected** candidates the
envelope still carried: ISABELLA CAFE AS's name-variant guess `isabellacafe.com` (a US cafe) in
`profile.website_discovery.candidates[].final_url`, and LØWEHJERTE DESIGN AS's Squarespace template `sameAs`
(`twitter.com/squarespace`) in the raw website record and the claims' `not_published` lists.

**Only verified links leave the agent** (`public.py`). The envelope's `profile` (and `--profiles-output`) is rebuilt from a
whitelist: registry records, the website record only when the site passed the gate (no raw page text, JSON-LD or
unassessed links), enrichment records without diagnostics, and no candidate trail. Claims carry no `not_published`.
A last-line guard removes any link or domain not on Brønnøysund, Wikidata, the verified site or a published profile; the
report's `link_guard` counts what it removed. Rejections go to `<output>.diagnostics.jsonl` for audits. Raw profiles
for `--resume` are checkpointed in `<profiles>.raw-checkpoint.jsonl`.

**Websites** (`identity.py`, `domain_guess.py`). Any domain not listed in the registry (e-mail domain, Wikidata P856,
name variants on `.no` or `.com`, sub-unit names) publishes only if the exact org number is printed on it. Any site
whose own address, phone or identity text points to another country (JSON-LD `addressCountry` not NO, US state + ZIP,
"City, ST" title, +1 phone, "USA") is rejected unless it prints the org number. `group_website` claims are no longer emitted.

**Social profiles.** Platform and vendor accounts (Squarespace, Wix, WordPress, Shopify, Webflow, GoDaddy, one.com, the
platforms themselves, …) are blocklisted; links in "powered by" / "designed by" / "levert av" credit lines are ignored;
Wikidata profiles must also match the name or verified brand. A rejected site cascades: nothing from it is published.

**News and jobs.** `dated_news` values carry `title`, `url`, `published_date` and `company_match`; statutory statements
and policy pages are not news. Careers pages list `open_postings`; job ads linked from a careers page (own job pages or
ATS postings: Webcruiter, Jobbnorge, Teamtailor, FINN, …) become `job_posting` claims with `title`, `url`,
`published_date` and `company_match`.

**Summary.** The first sentence is one buyer-relevant fact with its source ("Revenue NOK 17.4m in FY2025, down 3% from
FY2024 (Brønnøysund accounts)"); distress status, result, hiring, news, headcount or founding date when revenue is not
filed. The registry identity follows. `synthesis.headline` holds it.

Hand-check page for 100 never-run companies: `submission/audit-v6.html` (`scripts/build_audit_v6.py`), 25/25 links
checked correct by the owner.

### Coverage: org-number proof on terms/privacy pages, better domain candidates, retries

More verified websites with the identity rules unchanged (a domain that is not the registry's own entry still needs the
exact organisation number printed on it).

- **Proof pages** (`website.fetch_website(proof_org=...)`): when the homepage does not print the org number, up to 6 pages
  per site (homepage included) are checked: links whose path or text names contact, about, privacy, terms or imprint
  pages, then `sitemap.xml`, then `/kontakt`, `/om-oss`, `/personvern`, `/privacy`, `/vilkar`, `/kjopsvilkar`,
  `/salgsbetingelser`, `/impressum`. It stops at the first page that prints the number. Each page keeps the text lines
  that carry a 9-digit number (`org_number_lines`); a page proves identity only with this exact number and no other
  9-digit number on it. The quoted proof is that line.
- **Candidates** (`domain_guess.py`): ø/å/æ as o/a/ae and as oe/aa/ae, joined and hyphenated, first two words, sub-unit
  names; `.no` first, then full names on `.com`. DNS pre-check; at most 8 candidate sites fetched per company.
- **Retries**: one retry on HTTP 429/503 (Retry-After honoured up to 10 s) or a timeout, for site pages and Brønnøysund.
  The run report counts them under `retries`.

Same 100 unseen companies as `submission/audit-v6.html`, same day: websites 9 → 13, social profiles 12 → 17, news
items 4 → 13, careers/jobs 0 → 1, no link lost; 914 → 1,248 requests, p95 3.9 → 4.0 s. Hand-check page with only the
new links and their org-number lines: `submission/audit-coverage.html` (`scripts/build_audit_coverage.py`), 19/19
checked correct by the owner.

### Coverage: more verified news, job postings, sub-unit sites, socials from contact pages

Identity rules unchanged: every external link needs the exact org number or the registry's own website entry, and the
envelope carries only verified material.

- **News** (`news.py`): besides the feed and the news page, archive pages of the news listing (next page / `/arkiv`, with
  `?page=2` as a fallback) and the sitemap's news/blog URLs are read whenever fewer than 10 dated candidates are known;
  up to 10 articles are checked per site. An item counts as news only if a directory of its URL path is a news/blog
  section (`nytt`, `nyheter`, `nyhet`, `news`, `blog`, `blogg`, `artikler`, `artikkel`, `aktuelt`, `innblikk`, `presse`,
  `press`, `journal`, `post`, `posts`, `innlegg`, `arkiv`) or, outside such a section, the page declares JSON-LD
  `NewsArticle` or `BlogPosting`; and the publication-date and company-name rules pass. A generic JSON-LD `Article` (which
  CMSs add to every post) counts only under a news/blog section, and `og:type article` alone never counts.
- **Job postings** (`website.careers_listing_items`, `website.fetch_ats_list`): each ad on the verified site's careers page,
  and each ad on the company's list page on a recruiting system linked from the verified site (Teamtailor, Webcruiter,
  Recman, Jobylon, ReachMee, FINN, …), is its own `job_posting` claim with title, URL and the dates the page labels
  (`Publisert` → `published_date`, `Søknadsfrist` → `valid_through`; unlabelled dates are not used). The careers page stays a
  separate `careers_page` claim with `open_postings`.
- **Sub-unit websites** (`official.normalize_locations`, `domain_guess.subunit_registry_domains`): a sub-unit's registry
  `hjemmeside` is tried as a candidate for the parent and published only if it prints the parent's exact org number. It is
  labelled on the sub-unit's `registered_location` claim only when it is the same domain as the company's verified website;
  any other sub-unit site goes to the diagnostics file (`subunit_sites_not_labelled`), never the envelope.
- **Social profiles**: links in the body of the verified site's own contact/about pages count too (`contact_page`), under
  the same handle-match rules and vendor blocklist.

Hand-check page with only the new links: `submission/audit-coverage2.html` (`scripts/build_audit_coverage.py`).
