# Sources, rights and request policy

| Source | Access | Licence / terms | Rate & budget |
|---|---|---|---|
| Brønnøysund Enhetsregisteret bulk + API, Regnskapsregisteret | official open API, no key | NLOD 2.0 | starter limits; history endpoint paced |
| Wikidata Query Service | official SPARQL endpoint, descriptive User-Agent | CC0 data | ≤1 request per 200 companies; cache declared at `cache/wikidata-p2333.json` (24 h) |
| Company websites | direct HTTP(S), robots.txt honoured (fetched once per host) | company-owned public pages | homepage + ≤4 same-host pages; 2 MB / 1 MB caps; public IPs only |
| Company feeds / sitemaps | `<link rel=alternate>` RSS/Atom on the verified homepage; robots.txt `Sitemap:` or `/sitemap.xml` | company-owned public pages | only for identity-verified sites; ≤4 requests per company; robots.txt honoured; same registered domain only |
| Website candidates | registry e-mail domain, name variants `.no` then `.com` | company-owned public pages | DNS first; ≤6 candidate fetches per company, ≤3 pages each; robots.txt honoured |
| DNS | system resolver, pre-check for name-derived `.no` candidates | — | no HTTP request for unresolved names |

Not used: LinkedIn, Meta, Glassdoor, Indeed, Google/Bing scraping, paid search, proxies, LLMs.
Search/guess output is never evidence: published facts always come from a fetched page with URL,
retrieval time and SHA-256, or from an exact registry/Wikidata match.
No secrets are required. NAV job feed is not enabled pending organiser confirmation of token terms.
