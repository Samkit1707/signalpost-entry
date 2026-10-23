"""Dated news from the company's own identity-verified website (module `dated_news`).

Sources, in order, all on the verified site's registered domain and all behind robots.txt:
1. RSS/Atom feeds the homepage declares (`<link rel="alternate" type="application/rss+xml|atom+xml">`);
2. dated article listings on the news page the crawl already fetched (0 extra requests);
3. sitemap.xml (the robots.txt `Sitemap:` line first): Google News entries directly; otherwise the newest
   news-like URLs, each fetched once to read the title and the publication date the article itself declares.

Every item has a title, a direct URL, a publication date (never a modification date, never inferred) and the
exact-company proof: it is published on the site that passed the identity gate for this organisation number.
No site, or a site that did not pass the gate, yields `not_found` and zero requests.
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
MAX_REQUESTS = 4
MAX_ITEMS = 10
FEED_MAX_BYTES = 1_000_000
NEWS_PATH_TERMS = ("nyhet", "news", "aktuelt", "presse", "press", "artikkel", "artikler", "article", "blogg", "blog", "post")
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
        if title and url and date:
            items.append({"title": title, "url": url, "published_at": date,
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


def _default_fetch(url: str, *, timeout: float = 15.0, max_bytes: int = FEED_MAX_BYTES) -> dict[str, Any]:
    """One robots-checked GET through the per-host slot. Returns body, final URL, sha256 and request count."""
    from .website import SAFE_OPENER, USER_AGENT, _robots_allowed, assert_public_url, robots_cached

    robots_requests = 0 if robots_cached(url) else 1
    try:
        assert_public_url(url)
        if not _robots_allowed(url, timeout):
            return {"error": "robots.txt disallows", "requests": robots_requests}
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,text/html;q=0.8"})
        with host_slot(url) as started, SAFE_OPENER.open(request, timeout=timeout) as response:
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
        return evidence(MODULE, "not_found", "company_site_news", website.get("source_url") or site_url or "about:no-verified-site",
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
                          "claim_span": entry["claim_span"],
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

    # 3. sitemap.xml when nothing dated was found yet.
    if not items:
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
        if not items:
            candidates = sorted(((loc, lastmod) for loc, lastmod in plain if _newsy(loc) and _same_site(loc, site_domain)
                                 and urllib.parse.urlparse(loc).path.strip("/").count("/") >= 1),
                                key=lambda pair: pair[1], reverse=True)
            for loc, _lastmod in candidates:
                if metrics["requests"] >= MAX_REQUESTS:
                    break
                page = get(loc)
                if not page:
                    continue
                article_span: dict[str, str] = {}
                title, date = article_metadata(page["body"].decode("utf-8", errors="replace"), now=now, spans=article_span)
                if title and date:
                    add([{"title": title, "url": page["final_url"], "published_at": date, "claim_span": article_span.get("claim_span")}], "sitemap_article",
                        {"url": page["final_url"], "content_sha256": page["content_sha256"], "retrieved_at": page["retrieved_at"]})

    ordered = sorted(items.values(), key=lambda item: item["published_at"], reverse=True)[:MAX_ITEMS]
    if not ordered:
        return evidence(MODULE, "not_found", "company_site_news", site_url, retrieved_at=website.get("retrieved_at"),
                        content_sha256=value.get("content_sha256"), checked={"sources_checked": checked, "verified_site": site_url},
                        note="Verified site checked (feed, news page, sitemap); no dated news item with title and publication date"), metrics
    first = ordered[0]
    return evidence(MODULE, "available", "company_site_news", first["source_url"],
                    value={"items": ordered, "latest_published_at": first["published_at"], "sources_checked": checked,
                           "verified_site": site_url, "interpretation": INTERPRETATION},
                    as_of=first["published_at"], retrieved_at=first["retrieved_at"], content_sha256=first["content_sha256"]), metrics
