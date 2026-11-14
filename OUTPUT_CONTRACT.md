# Minimal output contract

Emit one JSON object per input organisation number.

```json
{
  "organisation_number": "123456789",
  "run": {
    "run_id": "2026-08-24-a",
    "started_at": "2026-08-24T06:00:00Z",
    "completed_at": "2026-08-24T06:00:08Z",
    "terminal_status": "completed"
  },
  "claims": [
    {
      "field": "official_website",
      "value": "https://example.no/",
      "availability": "available",
      "confidence": 0.99,
      "evidence_ids": ["ev-1"]
    }
  ],
  "evidence": [
    {
      "id": "ev-1",
      "source_url": "https://example.no/",
      "source_class": "company_owned",
      "retrieved_at": "2026-08-24T06:00:04Z",
      "content_sha256": "...",
      "claim_span": "Example AS, organisation number 123 456 789"
    }
  ],
  "changes": [],
  "errors": [],
  "operations": {
    "requests": 4,
    "runtime_ms": 8120,
    "third_party_cost_usd": 0
  }
}
```

Allowed availability states are `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous` and `failed`. A checked source that has zero jobs or zero locations is different from a source that was not checked.

## Entry additions (v2 of this entry)

Each envelope also carries:

- `claims[].as_of`: the date the fact refers to when the source states one (fiscal-year end for accounts,
  registration date for employees, publication date for news), otherwise `null`. `evidence[].retrieved_at` is the
  retrieval time.
- `dated_news` claims, one per item: `{"title", "url", "published_at", "source_kind"}` with its own evidence entry
  (`source_url` = the feed, sitemap or page read; `item_url`; `published_at`; `company_match` = the identity-verified
  site it was published on). With no item, one `dated_news` claim with `value: null`.
- `synthesis`: `{"method", "sentences": [{"topic", "text", "evidence_ids"}], "narrative", "latest_fiscal_year"}`.
  Topics are `what_it_does`, `financials`, `changes`, `unknown`. Every sentence cites evidence ids present in
  `evidence[]`; `narrative` is the sentences joined with their ids in brackets.

v3 adds fact-level claims (`"fact_level": true`): one claim per role, sub-unit, filed figure per year, registry
attribute, group relationship, profile, careers page, job posting and site contact detail, each with its own evidence
entry and `as_of`. Aggregate claims from v1/v2 are unchanged. Field list: README, "v3 of this entry".

v4: every evidence entry has `content_sha256` (hash of the response the value was read from) and a `claim_span` that
contains the claim's value, plus `span_kind`: `field_path` (CSV column or JSON path and value), `quote` (page text,
attribute or JSON-LD field), or `absence` (what was checked, for claims without a value). Values that cannot be cited
are withheld: the aggregate claim becomes `ambiguous` with a `note`; the envelope lists them in `uncited_withheld`.
Records may carry `checked` (what a not-found check looked at; never a value). A website record for a site that is
not the company's own carries `quarantined` (counts of dropped items). Claims the
citation check rejects are withheld in place (`ambiguous`/`not_available`, `value: null`, `note`) and logged in
`citation_withheld` and in the run report's `validation.citations`. The check never fails a run or drops an envelope.

v5: link claims (`official_website`, `external_profile(s)`, `social_profiles`, `dated_news`, `company_activity`,
`careers_page`, `job_posting`, `company_jobs`, `group_website`, `site_*`) carry `confidence_level` (`high`/`medium`),
numeric `confidence` (0.95/0.8) and `proof`; `official_website` also carries `discovery_method`. A link without a level
is `not_available` with a note. `external_profiles` and `dated_news` claims may carry `not_published`
(`[{url, reason, …}]`, candidates that failed the rule; never a value). New field `social_profiles`: the
`external_profiles` value restricted to social platforms, each entry `{platform, url, confidence, proof}`, same evidence.
Synthesis topics add `why_it_matters`; method `template_from_claims_v2`.

Summary (method `template_from_claims_five_sections`): every sentence has a `section` — `brief`, `financials`, `people`,
`work`, `activity` (Company brief, Latest financials, Who runs it, Working here, Recent activity; listed in
`synthesis.sections`) — and a `topic`: `headline` (the buyer fact, first), `why_it_matters` (one line, from the two cited
filed years, naming no amount), `what_it_does` (register sentence; the verified site's own description, quoted; a
registry-listed site that could not be read; sub-unit websites), `signal` (≤3 labelled observations, key `label`, no
amounts), `financials` (the newest filed year's figures not already named, with operating margin and equity ratio),
`changes` (earlier filed year with direction and the earlier amount only; dated registry change events; register status),
`news`, `people`, `work`, `profile` and `unknown` (one short line per missing family, key `family`: `website`, `news`,
`jobs`, `profiles`, `employees`, `roles`, `financials`, `financial_trend`, `registry_history`). Each figure appears once.
`synthesis.signals` lists the signal sentences as `{label, text, evidence_ids}`.

New claims:
- `registry_event` (fact-level, one per stated change, newest 30): `{date, type, entity, organisation_number, field,
  field_label, old, old_stated, new}`. Only `Ny` (registered), `Sletting`/`Fjernet` (deleted/removed), an `Endring` that
  names at least one changed field are published; an `Endring` without change
  details and `Ukjent` go to the diagnostics file (`registry_events_not_published`). `date` and `as_of` are the date as
  written in the feed timestamp (its first 10 characters, no timezone shift), cited in the update-feed response
  (`/oppdateringer/enheter?organisasjonsnummer=…&includeChanges=true`). `old` is set only when the feed stated it
  (`old_stated`); website and e-mail values are not published (`value_note`). Aggregate `registry_events`
  `{events, newest_date, oldest_date, entities}`; when the run skipped the feed past half its deadline it is
  `not_available` with `note: "skipped to protect the run deadline"`.
- `related_website` (fact-level): a sub-unit's registered website `{url, relationship, subunit_organisation_number,
  subunit_name, label, site_proof}`, confidence `medium`; never the company's own website. Published only when the domain
  resolves, stays on the filed domain, and a page prints the sub-unit's or the parent's org number or exact legal name;
  `site_proof` is `{kind: org_number|legal_name, who: sub-unit|parent, value, page_url, content_sha256, retrieved_at}`.
- `official_website` may carry `website_source: "registry_record"` with a `note` ("site unreachable at crawl time"):
  published from the registry record when the registry-listed site could not be read, its domain is this company's
  alone and still resolves in DNS (`discovery_method: "registry_record"`, confidence `medium`).
- A module that passed the per-company time limit (`--company-timeout-seconds`) is `failed` with a `company_time_limit`
  note; the envelope is still emitted.

Source links: an evidence `source_url` opens the per-company record — `/enhetsregisteret/api/enheter/<org>` (registry
facts, including those read from the bulk file), `/underenheter/<org>` (a sub-unit fact), `/regnskapsregisteret/regnskap/<org>`
(accounts), the Wikidata item page (`https://www.wikidata.org/wiki/Q…`) — never the bulk CSV or the SPARQL endpoint. When
the cited span and hash come from another response (the bulk file row, a sub-unit list, the Wikidata query), `read_from`
names it.
The website reason counts candidate domains checked but never names a rejected one.
