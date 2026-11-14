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
- The compare page is pre-rendered (every company's key facts) and reads without JavaScript; picking 2–3 companies for
  the side-by-side view needs JavaScript.
- Registry changes come from the Brønnøysund update feeds, which give each changed field's new value only: the old value
  is stated only when an earlier event in the same feed set that field. Only the main unit's feed is read (no role or
  sub-unit feeds), and only updates that state a change are published. Past website and e-mail values are not
  published. The feed is fetched last and skipped once half of the run deadline has passed, so a slow run can have no
  change events for some companies (stated on the claim).
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
- Org-number proof pages: at most 6 pages per site; a number printed only in an image or a PDF is not seen. A page that
  prints another 9-digit number next to ours (a data processor's org number, for example) does not count as proof.
- News needs a news/blog section in the URL, or JSON-LD NewsArticle/BlogPosting at a root-level slug: dated posts at
  root-level slugs with only a generic JSON-LD Article are not counted. Articles without a machine-readable publication date
  (only a date in the visible text) are not counted.
- Recruiting systems that render their ad list with JavaScript only (no links in the HTML) yield no job postings.
- A sub-unit's registry website is published as that sub-unit's site (`related_website`), never as the company's own,
  and only when its domain is filed as the website of no other main unit in the bulk entity file, is not a known
  platform or chain domain, resolves, does not redirect elsewhere, and prints the sub-unit's or parent's org number or
  exact legal name. The filer count covers main units (the bulk entity file), not other sub-units. A sub-unit site that
  shows its owner only in images or scripts is not published.
- A registry website that could not be read at crawl time is published from the registry record alone (marked
  "site unreachable at crawl time", medium confidence) when its domain is this company's alone and still resolves in
  DNS. A domain that resolves may still have changed hands since it was registered; nothing from the site is used.
- A company that passes the per-company time limit in a phase keeps its row, with that phase's unfinished modules
  `failed`; a slow host can therefore cost that company its website or news for the run.
