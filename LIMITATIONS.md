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
- v6: every website not listed in the registry (e-mail domain, Wikidata P856, name variants) publishes only on the
  printed organisation number; many real sites that show only the name stay unpublished by design. Registry e-mail domains are often an accountant's
  or property manager's and are rejected by the same rule.
- Role start dates are not in the Brønnøysund roles API; `role` claims carry the role group's last-changed date.
- Group detection uses Norwegian group structure only: a foreign parent's site is caught by the `.com`
  org-number rule, not labelled as a group site.
- v4 quotes page text as extracted (visible text, whitespace collapsed) or as the raw attribute; it is not a byte
  offset into the HTML. Bulk-CSV spans name the row and column, not a byte range in the 150 MB file.
- A site whose footer prints any organisation number other than this company's (for example a web agency's, with no
  number of its own) is not published (precision over recall).
- `accounting_obligation` is a rule classification; its evidence is the registry cells it reads, not the rule page.
- v6: a site with foreign-country signals (US address/phone, non-Norwegian JSON-LD country) is not published without the
  org number, even when it is the registry's own entry. Job ads from careers pages carry no date unless the page
  embeds a JobPosting date. Quotes in evidence may show "[unverified link withheld]" where a page named another domain.
