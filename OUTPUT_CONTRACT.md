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

Summary sections (method `template_from_claims_sections`): sentence topics are `headline` (the buyer fact, first),
`why_it_matters`,
`what_it_does` (one register sentence: legal form, place, registered activity, NACE; plus the verified site's own
meta/og description, quoted and attributed), `financials` (full latest figures), `changes` (filed-year revenue, annual
result and equity with direction and %, each year's figure claim cited; register status; dated news and job postings with
dates), `signal` (2–3 labelled observations from sourced numbers, sentence key `label`), `unknown` (one sentence per
missing family with the reason, sentence key `family`: `website`, `news`, `jobs`, `profiles`, `employees`, `roles`,
`financials`, `financial_trend`, `registry_history`) and `profile` (founding, roles, sub-units, profiles).
`why_it_matters` (topic and key) stays one plain line on what the year-on-year change means, built only from the two
cited filed years. `synthesis.signals` lists the signal sentences as `{label, text, evidence_ids}`.
The website reason counts candidate domains checked but never names a rejected one.
