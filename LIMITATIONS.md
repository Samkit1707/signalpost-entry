# Known limitations

- JavaScript-only sites are not rendered; they usually stay `review`/`not_found`.
- Name-derived domains only try `.no` and publish only with the printed org number; many real sites are missed by design.
- Wikidata covers ~10k Norwegian organisations; most small companies have no item.
- `company_jobs` shows careers pages and embedded `JobPosting` data only; a careers page alone does not prove an open vacancy.
- `company_activity` uses dates the page itself declares; undated news pages are listed without a date.
- Registry employee counts are as reported to the register, not live headcount.
- Sentiment and reviews are not produced (no rights-cleared source).
- `dated_news` reads only the identity-verified site (≤4 requests): a declared RSS/Atom feed, dated listings on the
  news page already crawled, then sitemap.xml. Sites without a feed, dated listing or sitemap get `not_available`.
  Atom entries with only `<updated>` and sitemap `<lastmod>` are not treated as publication dates.
- The synthesis is template-based from the envelope's own claims. It states no trend without two filed years of the
  same account type that both report the figure, and it does not interpret beyond the sources.
- The compare view needs JavaScript; company pages and the index list are plain HTML.
- Website candidates publish only on the printed organisation number (or full name + two registry details on `.no`);
  many real sites that print neither stay unpublished by design. Registry e-mail domains are often an accountant's
  or property manager's and are rejected by the same rule.
- Role start dates are not in the Brønnøysund roles API; `role` claims carry the role group's last-changed date.
- Group detection uses Norwegian group structure only: a foreign parent's site is caught by the `.com`
  org-number rule, not labelled as a group site.
