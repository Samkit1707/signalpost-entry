# Known limitations

- JavaScript-only sites are not rendered; they usually stay `review`/`not_found`.
- Name-derived domains only try `.no` and publish only with the printed org number; many real sites are missed by design.
- Wikidata covers ~10k Norwegian organisations; most small companies have no item.
- `company_jobs` shows careers pages and embedded `JobPosting` data only; a careers page alone does not prove an open vacancy.
- `company_activity` uses dates the page itself declares; undated news pages are listed without a date.
- Registry employee counts are as reported to the register, not live headcount.
- Sentiment and reviews are not produced (no rights-cleared source).
