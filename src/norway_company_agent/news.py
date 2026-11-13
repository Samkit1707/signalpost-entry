"""Dated news from the company's own identity-verified website (module `dated_news`).

Sources, in order, all on the verified site's registered domain and all behind robots.txt:
1. RSS/Atom feeds the homepage declares (`<link rel="alternate" type="application/rss+xml|atom+xml">`);
2. dated article listings on the news page the crawl already fetched (0 extra requests);
3. sitemap.xml (the robots.txt `Sitemap:` line first): Google News entries directly; otherwise the newest
   news-like URLs, each fetched once to read the title and the publication date the article itself declares.

Every item has a title, a direct URL, a publication date (never a modification date, never inferred) and the
exact-company proof: it is published on the site that passed the identity gate for this organisation number.
No site, or a site that did not pass the gate, yields `not_found` and zero requests.

v5: a candidate is published only when all three hold (each checked on the article page itself, fetched once):
(a) it is an article: a news/blog/press path or article markup (og:type article, JSON-LD Article/NewsArticle/
    BlogPosting), and never a contact/about/product/services page;
(b) it has a publication date the article, feed or sitemap declares;
(c) the article's main text (not the site footer) names the company: the legal name, or the verified site's brand.
Each item gets `confidence` (high: news path, legal name, and the date; medium: brand only or article markup only).
Candidates that fail are listed in `not_published` with the reason.
"""
from __future__ import annotations

import email.utils
import gzip
import json
import hashlib
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .evidence import evidence, utc_now
from .politeness import host_slot

MODULE = "dated_news"
MAX_REQUESTS = 18  # per verified site: feed, sitemap (+1 nested), up to 2 archive pages, article checks
MAX_ITEMS = 10
MAX_ARTICLE_CHECKS = 10  # article pages fetched to verify candidates (newest first)
MAX_ARCHIVE_PAGES = 2
ARCHIVE_TEXT = re.compile(r"^(?:neste|eldre|older|next|flere|more|arkiv|archive|nyhetsarkiv|tidligere|forrige)\b|\bside\s*2\b|\bpage\s*2\b|^2$|^»$|^›$", re.I)
ARCHIVE_HREF = re.compile(r"(?:[?&](?:page|side|p|paged)=2\b|/(?:page|side)/2/?$|/arkiv/?$|/archive/?$|/nyhetsarkiv/?$)", re.I)


def archive_links(base_url: str, soup: BeautifulSoup) -> list[str]:
    """'Next page' / archive links of a news listing on the same host (rel=next, ?page=2, /page/2, /arkiv)."""
    host = (urllib.parse.urlparse(base_url).hostname or "").casefold()
    found: list[str] = []
    for anchor in soup.select("a[href]"):
        url = urllib.parse.urljoin(base_url, str(anchor.get("href") or "").strip())
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").casefold() != host:
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        rel = " ".join(anchor.get("rel") or []) if isinstance(anchor.get("rel"), list) else str(anchor.get("rel") or "")
        if "next" in rel.casefold() or ARCHIVE_HREF.search(parsed.path + ("?" + parsed.query if parsed.query else "")) \
                or (ARCHIVE_TEXT.search(text) and (_newsy(url) or ARCHIVE_HREF.search(url))):
            clean = urllib.parse.urlunparse(parsed._replace(fragment=""))
            if clean.rstrip("/") != base_url.rstrip("/") and clean not in found:
                found.append(clean)
    return found[:3]
FEED_MAX_BYTES = 1_000_000
NEWS_PATH_TERMS = ("nyhet", "news", "aktuelt", "presse", "press", "artikkel", "artikler", "article", "blogg", "blog", "post")
# Path segments that make a page something other than an article (checked on every segment but an article slug).
NOT_ARTICLE_TERMS = ("kontakt", "contact", "om-oss", "om_oss", "omoss", "about", "produkt", "product", "tjenester", "tjeneste",
                     "services", "service", "tag", "kategori", "category", "shop", "butikk", "nettbutikk", "priser", "pristilbud", "fa-pristilbud")
ARTICLE_TYPES = {"article", "newsarticle", "blogposting", "pressrelease", "reportagenewsarticle", "analysisnewsarticle", "techarticle"}
_ISO_DATE = re.compile(r"((?:19|20)\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")
INTERPRETATION = ("News the company publishes on its own identity-verified website. Dates are the publication "
                  "dates the feed, sitemap or article declares; modification dates are not used.")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold() if isinstance(tag, str) else ""


def _text(node: ElementTree.Element | None) -> str:
    if node is None:
        return ""
    raw = "".join(node.itertext())
    if "<" in raw:  # RSS titles sometimes carry escaped markup
        raw = BeautifulSoup(raw, "lxml").get_text(" ")
    return " ".join(raw.split())[:300]


def _child(node: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((item for item in node if _local(item.tag) == name), None)


def parse_date(value: str | None, *, now: datetime | None = None) -> str | None:
    """A declared publication date as YYYY-MM-DD; None when missing, unparseable, pre-1995 or in the future."""
    value = str(value or "").strip()
    if not value:
        return None
    parsed: datetime | None = None
    match = _ISO_DATE.search(value)
    if match:
        parsed = datetime(*(int(part) for part in match.groups()), tzinfo=timezone.utc)
    else:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            parsed = None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if parsed.year < 1995 or parsed > now + timedelta(days=1):
        return None
    return parsed.date().isoformat()


def _registered_domain(url: str) -> str:
    from .website import _registered_domain as registered  # local import keeps tldextract out of module import time

    return registered(url)


def _same_site(url: str, site_domain: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and _registered_domain(url) == site_domain


def parse_feed(body: bytes, feed_url: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Items of an RSS 2.0 / RSS 1.0 / Atom feed with title, absolute link and publication date."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []
    items = []
    for node in root.iter():
        kind = _local(node.tag)
        if kind not in {"item", "entry"}:
            continue
        title = _text(_child(node, "title"))
        link, date_tag = "", ""
        if kind == "entry":  # Atom: rel="alternate" (or no rel) carries the article URL
            for candidate in node:
                if _local(candidate.tag) == "link" and candidate.get("rel", "alternate") == "alternate" and candidate.get("href"):
                    link = candidate.get("href", "")
                    break
            published = _text(_child(node, "published")) or _text(_child(node, "issued"))
            date_tag = "published" if _text(_child(node, "published")) else "issued"
        else:
            link = _text(_child(node, "link")) or node.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
            published = _text(_child(node, "pubdate")) or _text(_child(node, "date"))  # dc:date in RSS 1.0
            date_tag = "pubDate" if _text(_child(node, "pubdate")) else "dc:date"
        date = parse_date(published, now=now)
        url = urllib.parse.urljoin(feed_url, link.strip()) if link else ""
        body = next((item for item in node if _local(item.tag) in {"encoded", "content", "description", "summary"} and "".join(item.itertext()).strip()), None)
        text = " ".join(BeautifulSoup("".join(body.itertext()), "lxml").get_text(" ").split())[:5000] if body is not None else ""
        if title and url and date:
            items.append({"title": title, "url": url, "published_at": date, "feed_text": text,
                          "claim_span": f"<title>{title}</title> <{date_tag}>{published}</{date_tag}>"})
    return items


def parse_sitemap(body: bytes, *, now: datetime | None = None) -> tuple[list[str], list[dict[str, Any]], list[tuple[str, str]]]:
    """(child sitemaps, Google News items with title+date, plain (url, lastmod) entries)."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return [], [], []
    children, news, plain = [], [], []
    if _local(root.tag) == "sitemapindex":
        for node in root:
            loc = _text(_child(node, "loc"))
            if loc:
                children.append(loc)
        return children, [], []
    for node in root:
        if _local(node.tag) != "url":
            continue
        loc = _text(_child(node, "loc"))
        if not loc:
            continue
        news_node = _child(node, "news")
        if news_node is not None:
            title = _text(_child(news_node, "title"))
            raw_date = _text(_child(news_node, "publication_date"))
            date = parse_date(raw_date, now=now)
            if title and date:
                news.append({"title": title, "url": loc, "published_at": date,
                             "claim_span": f"<loc>{loc}</loc> <news:title>{title}</news:title> <news:publication_date>{raw_date}</news:publication_date>"})
                continue
        plain.append((loc, _text(_child(node, "lastmod"))))
    return children, news, plain


def article_metadata(html_text: str, *, now: datetime | None = None, spans: dict[str, str] | None = None) -> tuple[str, str | None]:
    """Title and the publication date an article page declares (never dateModified).
    `spans`, when given, gets "claim_span": the title and date exactly as the page states them."""
    soup = BeautifulSoup(html_text, "lxml")
    title_node = soup.select_one('meta[property="og:title"]')
    title = str(title_node.get("content") or "") if title_node else ""
    title_label = 'meta property="og:title" content'
    if not title:
        heading = soup.select_one("article h1, main h1, h1")
        title = heading.get_text(" ", strip=True) if heading else (soup.title.get_text(" ", strip=True) if soup.title else "")
        title_label = "h1" if heading else "title"
    candidates = [(str(node.get("content") or ""), f'meta {next((f"{key}={chr(34)}{node.get(key)}{chr(34)}" for key in ("property", "itemprop", "name") if node.get(key)), "")} content')
                  for node in soup.select('meta[property="article:published_time"], meta[itemprop="datePublished"], meta[name="pubdate"], meta[name="publish-date"]')]
    candidates += [(str(node.get("datetime") or node.get("content") or ""), 'itemprop="datePublished"') for node in soup.select('[itemprop="datePublished"]')]
    for script in soup.select('script[type="application/ld+json"]'):
        candidates += [(found, "JSON-LD datePublished") for found in re.findall(r'"datePublished"\s*:\s*"([^"]+)"', script.get_text() or "")]
    date, date_raw, date_label = None, "", ""
    for raw, label in candidates:
        parsed = parse_date(raw, now=now)
        if parsed:
            date, date_raw, date_label = parsed, raw, label
            break
    title = " ".join(title.split())[:300]
    if spans is not None and title and date:
        spans["claim_span"] = f"{title_label}={json.dumps(title, ensure_ascii=False)}; {date_label}={json.dumps(date_raw)}"
    return title, date


def news_listing_items(base_url: str, soup: BeautifulSoup, *, articles_only: bool = False) -> list[dict[str, Any]]:
    """Dated entries on a news listing: a same-host link next to a `<time datetime>` in one small container."""
    base_host = (urllib.parse.urlparse(base_url).hostname or "").casefold()
    found: dict[str, dict[str, Any]] = {}
    for time_node in soup.select("time[datetime]"):
        if time_node.find_parent(["header", "footer", "nav"]):
            continue
        container = time_node.find_parent("article")
        if container is None and not articles_only:
            container = time_node
            for _ in range(4):
                container = container.parent
                if container is None or container.name in {"body", "main", "html"}:
                    container = None
                    break
                if len({a.get("href") for a in container.select("a[href]")}) >= 1:
                    break
        if container is None:
            continue
        links = {}
        for anchor in container.select("a[href]"):
            url = urllib.parse.urljoin(base_url, str(anchor.get("href") or "").strip())
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme in {"http", "https"} and (parsed.hostname or "").casefold() == base_host and parsed.path.strip("/"):
                links.setdefault(urllib.parse.urlunparse(parsed._replace(fragment="")), anchor)
        if len(links) != 1:
            continue  # several articles share this container: the date cannot be tied to one of them
        url, anchor = next(iter(links.items()))
        heading = container.select_one("h1, h2, h3, h4")
        title = " ".join((heading.get_text(" ", strip=True) if heading else anchor.get_text(" ", strip=True)).split())[:300]
        raw_date = str(time_node.get("datetime") or "")
        if len(title) >= 8 and _ISO_DATE.search(raw_date):
            found.setdefault(url, {"title": title, "url": url, "raw_date": raw_date,
                                   "claim_span": f'{title} <time datetime="{raw_date}">'})
    return list(found.values())[:20]


def feed_links(base_url: str, soup: BeautifulSoup) -> list[str]:
    links = []
    for node in soup.select('link[rel~="alternate"][href]'):
        kind = str(node.get("type") or "").casefold()
        if "rss" in kind or "atom" in kind:
            url = urllib.parse.urljoin(base_url, str(node.get("href")))
            if "comments" not in url.casefold() and url not in links:
                links.append(url)
    return links[:3]


# v6: statutory statements and policies are dated pages, not news about the company.
POLICY_TERMS = ("apenhetslov", "aapenhetslov", "openhetslov", "transparency-act", "likestilling", "redegjorelse", "redegjoerelse",
                "personvern", "privacy", "cookie", "informasjonskapsler", "salgsbetingelser", "vilkar", "terms", "gdpr",
                "aktsomhetsvurdering", "due-diligence", "barekraftsrapport", "arsrapport", "annual-report")


def not_article_reason(url: str) -> str | None:
    """Why a URL is not an article page (contact/about/product/services/listing path), or None."""
    segments = [part.casefold() for part in urllib.parse.unquote(urllib.parse.urlparse(url).path).split("/") if part]
    if not segments:
        return "the site's homepage, not an article"
    checked = segments if len(segments) == 1 else segments[:-1]  # the last of several segments is the article's own slug
    slug = segments[-1]
    policy = next((term for term in POLICY_TERMS if term in slug), None)
    if policy:
        return f"a compliance/policy statement ({policy}), not news"
    for segment in checked:
        term = next((term for term in NOT_ARTICLE_TERMS if segment == term or segment.startswith(term + "-") or segment.startswith(term + "_")
                     or (term in {"kontakt", "contact", "produkt", "product", "tjeneste"} and segment.startswith(term))), None)
        if term:
            return f"a {term} page, not an article"
    return None


# The news gate: an item counts only if a directory of its URL path is a news/blog section, or the page itself declares
# JSON-LD NewsArticle / BlogPosting / Article. Root-level slugs without that markup (novooi.com/london/) never count.
NEWS_SEGMENTS = ("nytt", "nyheter", "nyhet", "news", "blog", "blogg", "artikler", "artikkel", "aktuelt", "innblikk", "presse",
                 "press", "journal", "pressemeldinger", "nyhetsarkiv", "post", "posts", "innlegg", "arkiv")
# Outside a news/blog section only these count: CMSs add a generic "Article" to every post and page.
ROOT_NEWS_JSONLD_TYPES = {"newsarticle", "blogposting"}
NEWS_JSONLD_TYPES = {"newsarticle", "blogposting", "article"}


def news_segment(url: str) -> str | None:
    """The news/blog directory in the URL path (never the article's own slug), or None."""
    parts = [part.casefold() for part in urllib.parse.unquote(urllib.parse.urlparse(url).path).split("/") if part]
    for part in parts[:-1]:
        if part in NEWS_SEGMENTS or any(part.startswith(term + "-") or part.startswith(term + "_") for term in NEWS_SEGMENTS):
            return part
    return None


def jsonld_news_type(html_text: str) -> str | None:
    """'JSON-LD NewsArticle' / 'BlogPosting' / 'Article' when the page declares one of those types."""
    soup = BeautifulSoup(html_text, "lxml")
    for script in soup.select('script[type="application/ld+json"]'):
        for kind in re.findall(r'"@type"\s*:\s*(?:\[[^\]]*?)?"([A-Za-z]+)"', script.get_text() or ""):
            if kind.casefold() in NEWS_JSONLD_TYPES:
                return f"JSON-LD {kind}"
    return None


def article_markup(html_text: str) -> str | None:
    """'og:type article' or 'JSON-LD <type>' when the page marks itself up as an article."""
    soup = BeautifulSoup(html_text, "lxml")
    node = soup.select_one('meta[property="og:type"]')
    if node and str(node.get("content") or "").strip().casefold() == "article":
        return "og:type article"
    for script in soup.select('script[type="application/ld+json"]'):
        for kind in re.findall(r'"@type"\s*:\s*(?:\[\s*)?"([A-Za-z]+)"', script.get_text() or ""):
            if kind.casefold() in ARTICLE_TYPES:
                return f"JSON-LD {kind}"
    return None


def article_text(html_text: str, url: str) -> str:
    """The article's main text without header, footer and navigation (trafilatura, precision mode)."""
    import trafilatura

    return trafilatura.extract(html_text, url=url, include_links=False, include_tables=False, favor_precision=True) or ""


def company_mention(text: str, legal_words: list[str], brands: list[str]) -> tuple[str | None, str]:
    """('legal_name' | 'brand' | None, quote): the company named in `text` as its legal name (word sequence, legal form
    optional) or as the verified site's brand (one word, or two adjacent words written together)."""
    from .identity import _fold

    tokens = [(match.start(), match.end(), _fold(match.group(0))) for match in re.finditer(r"[^\W_]+", text or "")]
    tokens = [(start, end, "".join(re.findall(r"[a-z0-9]+", folded))) for start, end, folded in tokens]
    tokens = [token for token in tokens if token[2]]

    def quote(start: int, end: int) -> str:
        return " ".join(text[max(0, start - 60):end + 60].split())

    if legal_words:
        size = len(legal_words)
        for index in range(len(tokens) - size + 1):
            if [token[2] for token in tokens[index:index + size]] == legal_words:
                return "legal_name", quote(tokens[index][0], tokens[index + size - 1][1])
        joined = "".join(legal_words)
        if len(joined) >= 5:
            for index, token in enumerate(tokens):
                pair = token[2] + (tokens[index + 1][2] if index + 1 < len(tokens) else "")
                if token[2] == joined or pair == joined:
                    return "legal_name", quote(token[0], (tokens[index + 1] if pair == joined and token[2] != joined else token)[1])
    for brand in brands:
        if len(brand) < 4:
            continue
        for index, token in enumerate(tokens):
            pair = token[2] + (tokens[index + 1][2] if index + 1 < len(tokens) else "")
            if token[2] == brand or pair == brand:
                return "brand", quote(token[0], (tokens[index + 1] if pair == brand and token[2] != brand else token)[1])
    return None, ""


BLOCK_TAGS = ["p", "li", "div", "section", "aside", "footer", "header", "address", "blockquote", "td", "span",
              "h1", "h2", "h3", "h4", "h5", "h6"]
MAX_BLOCK_HASHES = 600


def block_hash(text: str) -> str:
    return hashlib.sha256(" ".join(str(text).split()).casefold().encode("utf-8")).hexdigest()[:16]


def text_block_hashes(soup: BeautifulSoup) -> list[str]:
    """Hashes of every visible text block (paragraph, list item, sidebar, footer) on a page, so an article's
    naming paragraph can be checked against the rest of the site without storing the pages' full text."""
    hashes: dict[str, None] = {}
    for node in soup.find_all(BLOCK_TAGS):
        if node.find_parent(["script", "style", "noscript", "template"]):
            continue
        text = node.get_text(" ", strip=True)
        if len(text) >= 20:
            hashes[block_hash(text)] = None
        if len(hashes) >= MAX_BLOCK_HASHES:
            break
    return list(hashes)


def _page_key(url: str | None) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    return parsed.netloc.casefold().removeprefix("www.") + parsed.path.rstrip("/")


def unrepeated_mention(text: str, legal_words: list[str], brands: list[str], url: str | None,
                       site_pages: list[tuple[str | None, set[str]]]) -> tuple[str | None, str, dict[str, Any]]:
    """The company named in a paragraph of the article body that is not boilerplate, i.e. not the same text block
    repeated on 2+ other pages of the site. Returns (kind, quote, check)."""
    others = [hashes for page_url, hashes in site_pages if _page_key(page_url) != _page_key(url)]
    repeated = 0
    for paragraph in [part for part in str(text or "").split("\n") if part.strip()]:
        named, quote = company_mention(paragraph, legal_words, brands)
        if not named:
            continue
        pages = sum(1 for hashes in others if block_hash(paragraph) in hashes)
        if pages >= 2:
            repeated += 1
            continue
        return named, quote, {"other_pages_compared": len(others), "naming_block_on_other_pages": pages}
    return None, "", {"other_pages_compared": len(others), "boilerplate_mentions": repeated}


def _default_fetch(url: str, *, timeout: float = 15.0, max_bytes: int = FEED_MAX_BYTES) -> dict[str, Any]:
    """One robots-checked GET through the per-host slot. Returns body, final URL, sha256 and request count."""
    from .website import USER_AGENT, _robots_allowed, assert_public_url, open_with_retry, robots_cached

    robots_requests = 0 if robots_cached(url) else 1
    try:
        assert_public_url(url)
        if not _robots_allowed(url, timeout):
            return {"error": "robots.txt disallows", "requests": robots_requests}
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,text/html;q=0.8"})
        with host_slot(url) as started, open_with_retry(request, timeout) as response:
            raw = response.read(max_bytes + 1)
            final_url = response.geturl()
            elapsed = int((time.monotonic() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 - one failed optional source never fails the company
        return {"error": f"{type(exc).__name__}: {str(exc)[:120]}", "requests": robots_requests + 1}
    if len(raw) > max_bytes:
        return {"error": "oversized", "requests": robots_requests + 1, "bytes": len(raw), "elapsed_ms": elapsed}
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)[:max_bytes * 5]
        except OSError:
            return {"error": "bad gzip", "requests": robots_requests + 1}
    return {"body": raw, "final_url": final_url, "content_sha256": hashlib.sha256(raw).hexdigest(),
            "retrieved_at": utc_now(), "requests": robots_requests + 1, "bytes": len(raw), "elapsed_ms": elapsed}


def _robots_sitemaps(url: str) -> list[str]:
    from .website import _ROBOTS_CACHE, _ROBOTS_LOCK

    parsed = urllib.parse.urlparse(url)
    with _ROBOTS_LOCK:
        parser = _ROBOTS_CACHE.get(urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", "")))
    try:
        return list(parser.site_maps() or []) if parser else []
    except AttributeError:
        return []


def _newsy(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.casefold()
    return any(term in path for term in NEWS_PATH_TERMS)


def fetch_dated_news(
    profile: dict[str, Any],
    fetch: Callable[[str], dict[str, Any]] = _default_fetch,
    *,
    now: datetime | None = None,
    sitemaps_for: Callable[[str], list[str]] = _robots_sitemaps,
) -> tuple[dict[str, Any], dict[str, Any]]:
    metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
    website = (profile.get("evidence") or {}).get("website") or {}
    value = website.get("value") or {}
    identity = value.get("identity_assessment") or {}
    site_url = value.get("final_url")
    if website.get("status") != "available" or not identity.get("publishable") or not site_url:
        return evidence(MODULE, "not_found", "company_site_news", "about:no-verified-site",
                        note="No identity-gated company website to read dated news from"), metrics
    site_domain = _registered_domain(site_url)
    company_match = {"verified_site": site_url, "registered_domain": site_domain, "identity_status": identity.get("status"),
                     "identity_reasons": list(identity.get("reasons") or [])[:2]}
    items: dict[str, dict[str, Any]] = {}
    checked: list[dict[str, Any]] = []

    def get(url: str) -> dict[str, Any] | None:
        if metrics["requests"] >= MAX_REQUESTS:
            checked.append({"url": url, "status": "skipped_request_cap"})
            return None
        result = fetch(url)
        metrics["requests"] += int(result.get("requests") or 0)
        metrics["bytes"] += int(result.get("bytes") or 0)
        if result.get("elapsed_ms"):
            metrics["latencies_ms"].append(result["elapsed_ms"])
        if result.get("error") or not result.get("body"):
            checked.append({"url": url, "status": "error", "error": result.get("error")})
            return None
        if not _same_site(result.get("final_url") or url, site_domain):
            checked.append({"url": url, "status": "redirected_off_site"})
            return None
        checked.append({"url": url, "status": "ok"})
        return result

    def add(entries: list[dict[str, Any]], kind: str, source: dict[str, Any]) -> None:
        for entry in entries:
            url = entry["url"]
            if not _same_site(url, site_domain) or url in items:
                continue  # exact-company rule: only items on the verified site's own registered domain
            if not entry.get("claim_span") or not source.get("content_sha256"):
                continue  # every item is cited exactly (title and date as the source states them) or not at all
            items[url] = {"title": entry["title"], "url": url, "published_at": entry["published_at"], "source_kind": kind,
                          "claim_span": entry["claim_span"], "_feed_text": entry.get("feed_text") or "", "_page": entry.get("_page"),
                          "source_url": source["url"], "content_sha256": source.get("content_sha256"),
                          "retrieved_at": source.get("retrieved_at") or website.get("retrieved_at"),
                          "company_match": company_match}

    # 1. Declared RSS/Atom feed.
    for feed_url in [url for url in value.get("feed_links") or [] if _same_site(url, site_domain)][:1]:
        result = get(feed_url)
        if result:
            entries = parse_feed(result["body"], result["final_url"], now=now)
            kind = "atom" if b"<feed" in result["body"][:2000] else "rss"
            add(entries, kind, {"url": result["final_url"], "content_sha256": result["content_sha256"], "retrieved_at": result["retrieved_at"]})

    # 2. Dated listings on pages the crawl already fetched (the news page, articles on the homepage).
    for page in [value, *(value.get("pages") or [])]:
        entries = [{"title": entry["title"], "url": entry["url"], "published_at": parse_date(entry.get("raw_date"), now=now),
                    "claim_span": entry.get("claim_span")} for entry in page.get("news_items") or []]
        add([entry for entry in entries if entry["published_at"]], "news_page",
            {"url": page.get("final_url") or page.get("url"), "content_sha256": page.get("content_sha256")})

    # 2b. Archive pages of the news listing (next page, /arkiv): more dated entries from the same listing markup.
    if len(items) < MAX_ITEMS:
        archives: list[str] = []
        for page in value.get("pages") or []:
            if page.get("category") == "news":
                archives += [url for url in page.get("archive_links") or [] if url not in archives]
                listing = page.get("url") or ""
                if not page.get("archive_links") and listing and "?" not in listing:
                    archives.append(listing.rstrip("/") + "?page=2")  # the common pagination when no link is marked
        for archive_url in [url for url in archives if _same_site(url, site_domain)][:MAX_ARCHIVE_PAGES]:
            if len(items) >= MAX_ITEMS:
                break
            result = get(archive_url)
            if not result:
                continue
            soup = BeautifulSoup(result["body"].decode("utf-8", errors="replace"), "lxml")
            entries = [{"title": entry["title"], "url": entry["url"], "published_at": parse_date(entry.get("raw_date"), now=now),
                        "claim_span": entry.get("claim_span")} for entry in news_listing_items(result["final_url"], soup)]
            add([entry for entry in entries if entry["published_at"]], "news_archive",
                {"url": result["final_url"], "content_sha256": result["content_sha256"], "retrieved_at": result["retrieved_at"]})

    # 3. sitemap.xml while fewer than MAX_ITEMS dated candidates were found (news sitemap entries, then news/blog URLs).
    if len(items) < MAX_ITEMS:
        origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(site_url))
        declared = [url for url in sitemaps_for(site_url) if _same_site(url, site_domain)]
        sitemap = get((declared or [origin + "/sitemap.xml"])[0])
        plain: list[tuple[str, str]] = []
        if sitemap:
            children, news, plain = parse_sitemap(sitemap["body"], now=now)
            source = {"url": sitemap["final_url"], "content_sha256": sitemap["content_sha256"], "retrieved_at": sitemap["retrieved_at"]}
            add(news, "sitemap_news", source)
            child = next((url for url in children if _newsy(url) and _same_site(url, site_domain)), None)
            if child and not news:
                nested = get(child)
                if nested:
                    _, news, plain = parse_sitemap(nested["body"], now=now)
                    add(news, "sitemap_news", {"url": nested["final_url"], "content_sha256": nested["content_sha256"],
                                               "retrieved_at": nested["retrieved_at"]})
        if len(items) < MAX_ITEMS:
            candidates = sorted(((loc, lastmod) for loc, lastmod in plain if _newsy(loc) and _same_site(loc, site_domain)
                                 and urllib.parse.urlparse(loc).path.strip("/").count("/") >= 1 and loc not in items
                                 and not not_article_reason(loc)),
                                key=lambda pair: pair[1], reverse=True)
            for loc, _lastmod in candidates:
                if metrics["requests"] >= MAX_REQUESTS or len(items) >= MAX_ITEMS:
                    break
                page = get(loc)
                if not page:
                    continue
                article_span: dict[str, str] = {}
                title, date = article_metadata(page["body"].decode("utf-8", errors="replace"), now=now, spans=article_span)
                if title and date:
                    add([{"title": title, "url": page["final_url"], "published_at": date, "claim_span": article_span.get("claim_span"),
                          "_page": page}], "sitemap_article",
                        {"url": page["final_url"], "content_sha256": page["content_sha256"], "retrieved_at": page["retrieved_at"]})

    # 4. Verify every candidate on its own article page: an article, a publication date, and the company named.
    from .identity import full_legal_name_words, site_brands

    legal_words = full_legal_name_words(profile.get("name"))
    brands = site_brands(value)
    site_pages = [(page.get("final_url") or page.get("url"), set(page.get("block_hashes") or []))
                  for page in [value, *(value.get("pages") or [])] if page.get("block_hashes")]
    candidates, ordered, not_published = [], [], []
    checks = 0
    for item in sorted(items.values(), key=lambda item: item["published_at"], reverse=True):
        if len(candidates) >= MAX_ITEMS + MAX_ARTICLE_CHECKS:
            not_published.append({"title": item["title"], "url": item["url"], "reason": "not checked: older than the newest items checked on this site"})
            continue
        reason = not_article_reason(item["url"])
        page = item.pop("_page", None)
        feed_text = item.pop("_feed_text", "")
        if reason is None and page is None:
            if checks < MAX_ARTICLE_CHECKS and metrics["requests"] < MAX_REQUESTS:
                checks += 1
                page = get(item["url"])
                if page and not_article_reason(page["final_url"]):
                    reason = "redirected to " + str(not_article_reason(page["final_url"]))
            elif not (news_segment(item["url"]) and feed_text):
                reason = "not verified: article page not fetched (request cap) and the feed carries no article text"
        markup, page_date, text = None, None, feed_text
        if page and reason is None:
            html_text = page["body"].decode("utf-8", errors="replace")
            soup = BeautifulSoup(html_text, "lxml")
            site_pages.append((page["final_url"], set(text_block_hashes(soup))))
            markup = jsonld_news_type(html_text)
            spans: dict[str, str] = {}
            title, page_date = article_metadata(html_text, now=now, spans=spans)
            text = article_text(html_text, page["final_url"]) or feed_text
            if page_date and title and spans.get("claim_span"):
                # the article's own title and publication date, cited on the article page
                item.update({"title": title, "published_at": page_date, "claim_span": spans["claim_span"], "source_url": page["final_url"],
                             "content_sha256": page["content_sha256"], "retrieved_at": page["retrieved_at"]})
        candidates.append((item, reason, markup, page_date, text))
    # judged once every article page is in, so a block repeated across articles is seen as boilerplate
    for item, reason, markup, page_date, text in candidates:
        if len(ordered) >= MAX_ITEMS:
            not_published.append({"title": item["title"], "url": item["url"], "reason": f"not checked: only the newest {MAX_ITEMS} items are published"})
            continue
        newsy = bool(news_segment(item["url"]))
        root_markup = bool(markup) and markup.split()[-1].casefold() in ROOT_NEWS_JSONLD_TYPES
        if reason is None and not (newsy or root_markup):
            reason = ("not news: the URL has no news/blog section (nytt, nyheter, news, blog, post, aktuelt, …) and the page "
                      "declares no JSON-LD NewsArticle or BlogPosting (a generic Article counts only under a news/blog section)")
        if reason is None and not item.get("published_at"):
            reason = "no publication date on the article, feed or sitemap"
        named, mention, block_check = (unrepeated_mention(text, legal_words, brands, item["url"], site_pages)
                                       if reason is None else (None, "", {}))
        if reason is None and not named:
            reason = ("the only text naming the company is a block repeated on 2+ other pages of the site (boilerplate)"
                      if block_check.get("boilerplate_mentions") else
                      "the article text does not name the company (legal name or the verified site's brand)")
        if reason:
            not_published.append({"title": item["title"], "url": item["url"], "reason": reason})
            continue
        compared = block_check["other_pages_compared"] >= 2
        confidence = "high" if newsy and named == "legal_name" and compared else "medium"
        item.update({"confidence": confidence, "company_named": named, "company_mention": mention,
                     "boilerplate_check": block_check,
                     "article_evidence": markup or "news/blog/press path",
                     "date_from": "article page" if page_date else item["source_kind"],
                     "confidence_reason": ("news path, legal name in the article body (not a block repeated on other pages), "
                                           "declared publication date" if confidence == "high"
                                           else f"{'brand' if named == 'brand' else 'legal name'} in the article body; "
                                                f"{'news path' if newsy else markup}; declared publication date"
                                                + ("" if compared else "; fewer than 2 other site pages to rule out boilerplate"))})
        ordered.append(item)
    ordered.sort(key=lambda item: item["published_at"], reverse=True)
    # rejections of items that were actually judged first; "not checked" ones last (the list is capped at 20)
    not_published.sort(key=lambda item: str(item.get("reason") or "").startswith("not checked"))
    if not ordered:
        note = "Verified site checked (feed, news page, sitemap); no dated news item with title and publication date"
        if not_published:
            note = f"Verified site checked; {len(not_published)} candidate(s) failed the article rule (article, publication date, names the company)"
        return evidence(MODULE, "not_found", "company_site_news", site_url, retrieved_at=website.get("retrieved_at"),
                        content_sha256=value.get("content_sha256"),
                        checked={"sources_checked": checked, "verified_site": site_url, "not_published": not_published[:20]},
                        note=note), metrics
    first = ordered[0]
    return evidence(MODULE, "available", "company_site_news", first["source_url"],
                    value={"items": ordered, "latest_published_at": first["published_at"], "sources_checked": checked,
                           "not_published": not_published[:20],
                           "verified_site": site_url, "interpretation": INTERPRETATION},
                    as_of=first["published_at"], retrieved_at=first["retrieved_at"], content_sha256=first["content_sha256"]), metrics
