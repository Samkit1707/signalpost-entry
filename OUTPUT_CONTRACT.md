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
