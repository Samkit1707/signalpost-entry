from __future__ import annotations

import html as html_lib
import json
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup
import extruct
import tldextract
import trafilatura

from .evidence import evidence
from .news import feed_links, news_listing_items, text_block_hashes
from .politeness import host_slot

USER_AGENT = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"
SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
}
PRIORITY_TERMS = (
    "om-oss", "om_oss", "about", "kontakt", "contact", "ledelse", "management",
    "team", "people", "locations", "lokasjoner", "avdelinger", "butikker",
    "news", "press", "aktuelt", "nyheter",
)
# One slot per category keeps identity proof while adding the external field families that score recall.
PRIORITY_CATEGORIES = (
    ("identity", ("kontakt", "contact", "om-oss", "om_oss", "about", "hvem-er-vi")),
    ("news", ("nyheter", "news", "aktuelt", "press", "presse", "blogg", "blog", "artikler")),
    ("careers", ("karriere", "career", "ledige-stillinger", "stillinger", "jobb", "jobs", "rekruttering", "vacancies")),
    ("people_locations", ("ledelse", "management", "team", "people", "ansatte", "locations", "lokasjoner", "avdelinger", "butikker", "kontorer")),
)


def assert_public_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Local hosts are blocked")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("Hostname did not resolve") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private, loopback, link-local, multicast, and reserved addresses are blocked")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        assert_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SAFE_OPENER = urllib.request.build_opener(SafeRedirectHandler())


def normalize_homepage(value: str | None) -> str | None:
    value = str(value or "").strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))


def _registered_domain(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    ext = tldextract.extract(parsed.hostname or "")
    return ext.top_domain_under_public_suffix


_ROBOTS_CACHE: dict[str, Any] = {}
_ROBOTS_LOCK = __import__("threading").Lock()


def _robots_allowed(url: str, timeout: float) -> bool:
    """Return robots permission, fetching robots.txt at most once per scheme+host per process.

    Returns True when robots.txt is unavailable (one ordinary bounded GET), matching the starter policy.
    The first lookup per host counts as one request in callers; cached lookups are free.
    """
    assert_public_url(url)
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    with _ROBOTS_LOCK:
        cached = _ROBOTS_CACHE.get(robots_url)
    if cached is None:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
            with host_slot(robots_url), SAFE_OPENER.open(request, timeout=timeout) as response:
                parser.parse(response.read().decode("utf-8", errors="replace").splitlines())
            cached = parser
        except Exception:
            cached = False
        with _ROBOTS_LOCK:
            _ROBOTS_CACHE[robots_url] = cached
    if cached is False:
        return True
    return cached.can_fetch(USER_AGENT, url)


def robots_cached(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    with _ROBOTS_LOCK:
        return robots_url in _ROBOTS_CACHE


def raw_attribute(html_text: str | None, name: str, value: str) -> str:
    """The attribute as written in the page: `name="value"`, entity-escaped when that is how the HTML has it."""
    escaped = html_lib.escape(value, quote=True)
    if html_text is not None and value not in html_text and escaped in html_text:
        return f'{name}="{escaped}"'
    return f'{name}="{value}"'


_REGION_HINT = re.compile(r"(?:^|[\s_-])(footer|header|masthead|colophon)(?:$|[\s_-])", re.I)


def link_location(node: Any) -> str:
    """Where a link sits: 'footer' or 'header' (semantic element, or a container whose id/class says so), else 'body'."""
    for parent in node.parents:
        name = getattr(parent, "name", None)
        if name in {"footer", "header"}:
            return name
        if name in {"body", "html", None}:
            break
        hint = " ".join([str(parent.get("id") or ""), *[str(item) for item in parent.get("class") or []], str(parent.get("role") or "")])
        match = _REGION_HINT.search(hint)
        if match or "contentinfo" in hint or "banner" == str(parent.get("role") or ""):
            word = (match.group(1).casefold() if match else "footer" if "contentinfo" in hint else "header")
            return "footer" if word in {"footer", "colophon"} else "header"
    return "body"


# v6: a "powered by" / "designed by" / theme credit line links the vendor or agency, never the company.
CREDIT_TEXT = re.compile(
    r"powered\s+by|drevet\s+av|levert\s+av|laget\s+av|utviklet\s+av|designet\s+av|design\s*(?:by|av)\b|designed\s+by|"
    r"website\s+by|web\s*site\s+by|site\s+by|nettside\s+(?:av|fra|levert)|hjemmeside\s+(?:av|fra|levert)|made\s+(?:by|with)|"
    r"created\s+(?:by|with)|built\s+(?:by|with)|theme\s+(?:by|from)|template\s+(?:by|from)|proudly\s+(?:created|designed|made|built)|"
    r"webdesign|web\s*design|wordpress\s+theme|levert\s+gjennom|publisert\s+med|laget\s+med|bygget\s+(?:av|med)",
    re.I)


def in_credit_area(node: Any) -> bool:
    """The link sits in a short credit line ("Proudly designed by X", "Levert av Y", "Powered by Squarespace")."""
    for depth, parent in enumerate([node, *list(node.parents)[:3]]):
        if getattr(parent, "name", None) in {"body", "html", "footer", "header", "nav", None}:
            break
        if depth and len(parent.select("a[href]")) > 1:
            break  # a block with other links (the footer's own profile icons) is not the credit line itself
        text = " ".join(parent.get_text(" ", strip=True).split()) if hasattr(parent, "get_text") else ""
        if len(text) > 300:
            break
        if CREDIT_TEXT.search(text) or CREDIT_TEXT.search(" ".join([str(parent.get("class") or ""), str(parent.get("id") or "")])):
            return True
    return False


def _social_links(base_url: str, soup: BeautifulSoup, html_text: str | None = None, page_sha256: str | None = None) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    candidates = [("href", str(node.get("href") or ""), "credit" if in_credit_area(node) else link_location(node)) for node in soup.select("a[href]")]
    candidates.extend(("data-href", str(node.get("data-href") or ""), link_location(node) + "_embed") for node in soup.select("[data-href]"))
    candidates.extend(("src", str(node.get("src") or ""), link_location(node) + "_embed") for node in soup.select("iframe[src]"))
    # a profile linked both from the body and the footer counts as linked from the footer
    candidates.sort(key=lambda item: item[2] not in {"footer", "header"})
    for attribute, candidate, location in candidates:
        url = urllib.parse.urljoin(base_url, candidate)
        parsed_candidate = urllib.parse.urlparse(url)
        if (parsed_candidate.hostname or "").casefold().removeprefix("www.") == "facebook.com" and parsed_candidate.path.startswith("/plugins/"):
            embedded = urllib.parse.parse_qs(parsed_candidate.query).get("href", [])
            if embedded:
                url = embedded[0]
        normalized = normalize_social_url(url)
        if not normalized:
            continue
        found.setdefault((normalized["platform"], normalized["url"]), {
            **normalized, "page_url": base_url, "content_sha256": page_sha256, "raw": raw_attribute(html_text, attribute, candidate),
            "location": location})
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def structured_social_links(value: Any, page_url: str | None = None, page_sha256: str | None = None) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            same_as = node.get("sameAs")
            urls = same_as if isinstance(same_as, list) else [same_as]
            for raw in urls:
                if not isinstance(raw, str):
                    continue
                normalized = normalize_social_url(raw.strip())
                if normalized:
                    found.setdefault((normalized["platform"], normalized["url"]), {
                        **normalized, "page_url": page_url, "content_sha256": page_sha256, "raw": f"JSON-LD sameAs={json.dumps(raw, ensure_ascii=False)}",
                        "location": "jsonld_same_as"})
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def normalize_social_url(url: str) -> dict[str, str] | None:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    platform = next((label for domain, label in SOCIAL_HOSTS.items() if host == domain or host.endswith("." + domain)), None)
    if not platform:
        return None
    parts = [part.strip() for part in parsed.path.split("/") if part.strip()]
    lowered = [part.casefold() for part in parts]
    rejected_first = {
        "facebook": {"sharer", "sharer.php", "share.php", "dialog", "policy.php", "privacy", "events", "groups", "plugins"},
        "instagram": {"p", "reel", "reels", "stories", "explore"},
        "x": {"intent", "share", "home", "search", "i"},
    }
    if not parts or lowered[0] in rejected_first.get(platform, set()):
        return None
    if platform == "facebook" and lowered[0] == "profile.php":
        return None
    if platform == "linkedin" and (lowered[0] != "company" or len(parts) < 2):
        return None
    if platform == "youtube" and lowered[0] not in {"channel", "user", "c"} and not parts[0].startswith("@"):
        return None
    if host == "youtu.be":
        return None
    if platform == "tiktok" and not parts[0].startswith("@"):
        return None
    if platform == "x" and len(parts) != 1:
        return None
    canonical_host = {
        "linkedin": "linkedin.com",
        "facebook": "facebook.com",
        "instagram": "instagram.com",
        "x": "x.com",
        "youtube": "youtube.com",
        "tiktok": "tiktok.com",
    }[platform]
    if platform == "linkedin":
        parts = parts[:2]
    elif platform == "youtube":
        parts = parts[:1] if parts[0].startswith("@") else parts[:2]
    return {"platform": platform, "url": f"https://{canonical_host}/{'/'.join(parts)}"}


def identity_excerpt(soup: BeautifulSoup) -> str:
    """Footer/address/legal text where Norwegian sites print 'Org.nr' (trafilatura drops footers)."""
    nodes = soup.select('footer, address, [itemprop="legalName"], [itemprop="address"], [itemprop="telephone"], [itemprop="email"], [itemprop="vatID"], [itemprop="taxID"]')
    parts = [node.get_text(" ", strip=True) for node in nodes]
    for node in soup.find_all(string=__import__("re").compile(r"org\.?\s*(?:nr|nummer|no)|foretaksregisteret|mva", __import__("re").I)):
        parent = node.parent
        if parent is not None:
            parts.append(parent.get_text(" ", strip=True)[:300])
    return " ".join(" ".join(parts).split())[:3000]


# v6: careers is matched on whole words, not substrings ("faa-jobben-gjort", a DIY advice page, is not a careers page).
CAREERS_WORDS = {"karriere", "career", "careers", "stillinger", "stilling", "jobb", "jobs", "rekruttering", "vacancies", "vacancy"}
CAREERS_PHRASES = ("ledige stillinger", "ledige jobber", "jobb hos oss", "jobbe hos oss", "work with us", "join us", "open positions")


def _is_careers(url: str, text: str) -> bool:
    words = re.findall(r"[a-zæøå0-9]+", (urllib.parse.unquote(urllib.parse.urlparse(url).path) + " " + text).casefold().replace("_", " "))
    joined = " ".join(words)
    return bool(CAREERS_WORDS & set(words)) or any(phrase in joined for phrase in CAREERS_PHRASES)


def page_category(url: str, text: str = "") -> str | None:
    haystack = (urllib.parse.urlparse(url).path + " " + text).casefold()
    for name, terms in PRIORITY_CATEGORIES:
        if name == "careers":
            if _is_careers(url, text):
                return name
            continue
        if any(term in haystack for term in terms):
            return name
    return None


def _priority_links(base_url: str, soup: BeautifulSoup, limit: int = 4) -> list[str]:
    """Pick at most one same-host link per category (identity, news, careers, people/locations)."""
    base = urllib.parse.urlparse(base_url)
    best: dict[str, tuple[int, str]] = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != base.netloc.lower():
            continue
        clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
        if clean.rstrip("/") == base_url.rstrip("/"):
            continue
        haystack = (parsed.path + " " + anchor.get_text(" ", strip=True)).casefold()
        for name, terms in PRIORITY_CATEGORIES:
            rank = next((index for index, term in enumerate(terms) if term in haystack), None)
            if name == "careers" and rank is not None and not _is_careers(url, anchor.get_text(" ", strip=True)):
                rank = None  # "jobben" in a DIY article is not a careers link
            if rank is None:
                continue
            current = best.get(name)
            candidate = (rank, clean)
            if current is None or candidate < current:
                best[name] = candidate
            break
    ordered = [best[name][1] for name, _ in PRIORITY_CATEGORIES if name in best]
    unique: list[str] = []
    for url in ordered:
        if url not in unique:
            unique.append(url)
    return unique[:limit]


# Pages that carry a company's legal identity: contact, about, then legal/privacy (checked when probing a candidate).
PROBE_TERMS = (("kontakt", "contact"), ("om-oss", "om_oss", "about", "hvem-er-vi", "selskapet", "firma"),
               ("personvern", "privacy", "vilkar", "vilkår", "juridisk", "legal", "impressum", "salgsbetingelser", "terms"))


def probe_links(base_url: str, soup: BeautifulSoup, limit: int = 2) -> list[str]:
    """At most `limit` same-host identity pages (contact, about, legal), in that order of preference."""
    base = urllib.parse.urlparse(base_url)
    found: dict[int, str] = {}
    for anchor in soup.select("a[href]"):
        url = urllib.parse.urljoin(base_url, str(anchor.get("href") or "").strip())
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != base.netloc.lower():
            continue
        clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
        if clean.rstrip("/") == base_url.rstrip("/"):
            continue
        haystack = (urllib.parse.unquote(parsed.path) + " " + anchor.get_text(" ", strip=True)).casefold()
        for rank, terms in enumerate(PROBE_TERMS):
            if rank not in found and any(term in haystack for term in terms):
                found[rank] = clean
                break
    ordered = []
    for rank in sorted(found):
        if found[rank] not in ordered:
            ordered.append(found[rank])
    return ordered[:limit]


def _postal_address(value: Any) -> str | None:
    if isinstance(value, str):
        return " ".join(value.split()) or None
    if isinstance(value, list):
        return _postal_address(value[0]) if value else None
    if not isinstance(value, dict):
        return None
    parts = [value.get("streetAddress"), " ".join(str(part) for part in (value.get("postalCode"), value.get("addressLocality")) if part)]
    return ", ".join(" ".join(str(part).split()) for part in parts if part and str(part).strip()) or None


def _image_url(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return value if isinstance(value, str) and value.startswith(("http://", "https://")) else None


def site_contact(base_url: str, soup: BeautifulSoup, organisations: list[dict[str, Any]], pages_soups: list[tuple[str, BeautifulSoup]] = (),
                 *, html_text: str | None = None, page_sha256: str | None = None) -> dict[str, Any]:
    """Contact facts a site states about itself: JSON-LD Organization on its own domain, own-domain mailto, footer tel links.
    `sources` maps "email:<value>" / "phone:<value>" to the page, its hash and the exact attribute it was read from."""
    domain = _registered_domain(base_url)
    own = []
    sources: dict[str, dict[str, Any]] = {}
    for item in organisations:
        url = str(item.get("url") or item.get("@id") or "")
        if url.startswith("http") and _registered_domain(url) != domain:
            continue  # e.g. the web agency credited in the markup
        entry = {"name": item.get("name") if isinstance(item.get("name"), str) else None,
                 "address": _postal_address(item.get("address")),
                 "telephone": " ".join(str(item.get("telephone") or "").split()) or None,
                 "email": str(item.get("email") or "").removeprefix("mailto:").strip().casefold() or None,
                 "logo": _image_url(item.get("logo"))}
        if entry["email"] and _registered_domain("https://" + entry["email"].split("@")[-1]) != domain:
            entry["email"] = None
        if any(entry[key] for key in ("address", "telephone", "email", "logo")):
            raw_fields = {"address": item.get("address"), "telephone": item.get("telephone"), "email": item.get("email"), "logo": item.get("logo")}
            entry["page_url"], entry["content_sha256"] = base_url, page_sha256
            entry["spans"] = {key: f"JSON-LD {_jsonld_type(item)}.{key}={json.dumps(raw_fields[key], ensure_ascii=False)}"
                              for key in ("address", "telephone", "email", "logo") if entry[key]}
            own.append(entry)
    emails, phones = [], []
    for page_url, page_soup in [(base_url, soup), *pages_soups]:
        for node in page_soup.select('a[href^="mailto:"]'):
            email = urllib.parse.unquote(str(node.get("href"))[7:].split("?")[0]).strip().casefold()
            if "@" in email and _registered_domain("https://" + email.split("@")[-1]) == domain and email not in emails:
                emails.append(email)
                sources[f"email:{email}"] = {"page_url": page_url, "content_sha256": page_sha256 if page_url == base_url else None,
                                             "raw": raw_attribute(html_text if page_url == base_url else None, "href", str(node.get("href")))}
        for node in page_soup.select('footer a[href^="tel:"], address a[href^="tel:"], [itemprop="telephone"]'):
            raw = str(node.get("href") or node.get_text(" ", strip=True)).removeprefix("tel:")
            digits = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
            if len(digits.lstrip("+")) >= 8 and digits not in phones:
                phones.append(digits)
                raw_text = raw_attribute(html_text if page_url == base_url else None, "href", str(node.get("href"))) if node.get("href") \
                    else collapse_text(node.get_text(" ", strip=True))
                sources[f"phone:{digits}"] = {"page_url": page_url, "content_sha256": page_sha256 if page_url == base_url else None, "raw": raw_text}
    return {"organisations": own[:5], "emails": emails[:5], "phones": phones[:3], "sources": sources}


def collapse_text(text: str) -> str:
    return " ".join(str(text or "").split())


def _jsonld_type(item: dict[str, Any]) -> str:
    kind = item.get("@type")
    return str(kind[0] if isinstance(kind, list) and kind else kind or "Organization")


_DATE_RE = __import__("re").compile(r"(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")


def page_dates(soup: BeautifulSoup, structured: dict[str, Any] | None = None, spans: dict[str, str] | None = None) -> list[str]:
    """Publication dates the page itself declares (meta, <time datetime>, JSON-LD). Never inferred.
    `spans`, when given, is filled with date -> the exact attribute or JSON-LD field it was read from."""
    found: list[str] = []
    labels: list[str] = []
    for node in soup.select('meta[property="article:published_time"], meta[property="article:modified_time"], meta[name="date"], meta[itemprop="datePublished"]'):
        found.append(str(node.get("content") or ""))
        selector = next((f'{key}="{node.get(key)}"' for key in ("property", "name", "itemprop") if node.get(key)), "meta")
        labels.append(f'<meta {selector} content="{node.get("content") or ""}">')
    for node in soup.select("time[datetime]"):
        found.append(str(node.get("datetime") or ""))
        labels.append(f'<time datetime="{node.get("datetime") or ""}">')

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("datePublished", "dateModified", "datePosted", "uploadDate"):
                if isinstance(value.get(key), str):
                    found.append(value[key])
                    labels.append(f"JSON-LD {key}={json.dumps(value[key])}")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk((structured or {}).get("json-ld", []))
    dates = sorted({"-".join(match.groups()) for item in found for match in [_DATE_RE.search(item)] if match}, reverse=True)
    if spans is not None:
        for item, label in zip(found, labels):
            match = _DATE_RE.search(item)
            if match:
                spans.setdefault("-".join(match.groups()), label)
    return dates[:20]


def job_postings(structured: dict[str, Any] | None) -> list[dict[str, Any]]:
    """schema.org JobPosting objects embedded by the company itself."""
    jobs: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            kind = value.get("@type")
            kinds = set(kind if isinstance(kind, list) else [kind])
            if "JobPosting" in kinds:
                jobs.append({
                    "claim_span": "JSON-LD JobPosting." + "; JobPosting.".join(
                        f"{key}={json.dumps(value.get(key), ensure_ascii=False)}" for key in ("title", "datePosted", "validThrough") if value.get(key)),
                    "title": str(value.get("title") or "")[:300],
                    "date_posted": str(value.get("datePosted") or "")[:40] or None,
                    "valid_through": str(value.get("validThrough") or "")[:40] or None,
                    "employment_type": value.get("employmentType"),
                    "hiring_organization": (value.get("hiringOrganization") or {}).get("name")
                    if isinstance(value.get("hiringOrganization"), dict) else value.get("hiringOrganization") if isinstance(value.get("hiringOrganization"), str) else None,
                })
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk((structured or {}).get("json-ld", []))
    return jobs[:50]


# v6: open positions listed on the verified site's careers page (own job pages, or the company's ATS listing).
ATS_HOSTS = ("webcruiter.no", "webcruiter.com", "jobbnorge.no", "recman.no", "recman.page", "teamtailor.com", "hrmanager.no",
             "easycruit.com", "reachmee.com", "jobylon.com", "workable.com", "varbi.com", "talentech.com", "cvpartner.com",
             "finn.no")
JOB_PATH = re.compile(r"(stilling|jobb|job|karriere|career|vacanc|ledig|rekrutt|position|utlysning|apply|soknad|søknad)", re.I)
NOT_JOB_TEXT = re.compile(r"^(?:les mer|read more|se alle|see all|søk her|søk nå|apply|tilbake|back|kontakt|meny|menu|mer|more|"
                          r"ledige stillinger|stillinger|karriere|careers?|jobs?|jobb hos oss|open positions|alle stillinger|"
                          r"åpen søknad|open application|generell søknad)$", re.I)


def careers_listing_items(base_url: str, soup: BeautifulSoup, html_text: str | None = None) -> list[dict[str, Any]]:
    """Job ads linked from a careers page: same-site pages one level below a job path, or an ATS posting URL. The anchor
    text is the ad title; navigation, header/footer and 'open application' links are not ads."""
    base = urllib.parse.urlparse(base_url)
    base_domain = _registered_domain(base_url)
    base_path = base.path.rstrip("/")
    found: dict[str, dict[str, Any]] = {}
    for anchor in soup.select("a[href]"):
        if anchor.find_parent(["header", "footer", "nav"]) or link_location(anchor) != "body":
            continue
        href = str(anchor.get("href") or "").strip()
        url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        host = (parsed.hostname or "").casefold().removeprefix("www.")
        title = " ".join(anchor.get_text(" ", strip=True).split())[:200]
        if len(title) < 6 or NOT_JOB_TEXT.match(title) or len(title.split()) > 14 \
                or re.fullmatch(r"(?:https?://)?(?:www\.)?[\w.-]+\.[a-z]{2,6}(?:/\S*)?", title, re.I) \
                or re.fullmatch(r"(?:se |les |søk |apply |view )?(?:annonse|stillingen|på finn|on finn|finn|webcruiter|jobbnorge|teamtailor)\.?", title, re.I):
            continue  # the anchor names the job board, not the job: the ad's title is unknown
        path = parsed.path.rstrip("/")
        ats = next((item for item in ATS_HOSTS if host == item or host.endswith("." + item)), None)
        if ats == "finn.no" and "/job/" not in parsed.path:
            continue
        if ats:
            kind = "ats_listing"
            if not re.search(r"\d{3,}|/jobs?/|/stilling|/position|/vacanc|/job/", parsed.path + "?" + parsed.query, re.I):
                continue  # a posting, not the ATS front page
        elif _registered_domain(url) == base_domain and path.startswith(base_path + "/") and path != base_path \
                and JOB_PATH.search(base_path + path) and len([part for part in path[len(base_path):].split("/") if part]) == 1:
            kind = "site_job_page"
        else:
            continue
        clean = urllib.parse.urlunparse(parsed._replace(fragment=""))
        found.setdefault(clean, {"title": title, "url": clean, "kind": kind,
                                 "claim_span": f'{raw_attribute(html_text, "href", href)}>{title}'})
    return list(found.values())[:15]


def _fetch_secondary_page(url: str, *, homepage_domain: str, timeout: float, max_bytes: int, probe: bool = False) -> tuple[dict[str, Any] | None, list[dict[str, str]], int, int, int, str | None]:
    robots_request = 0 if robots_cached(url) else 1
    if not _robots_allowed(url, timeout):
        return None, [], robots_request, 0, 0, "robots.txt disallows page"
    page_request_count = robots_request + 1
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with host_slot(url) as started, SAFE_OPENER.open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            final_url = response.geturl()
            if len(raw) > max_bytes or "html" not in response.headers.get("content-type", "").lower():
                return None, [], page_request_count, len(raw), elapsed, "unsupported or oversized page"
            if _registered_domain(final_url) != homepage_domain:
                return None, [], page_request_count, len(raw), elapsed, "redirected outside registered domain"
        page_html = raw.decode("utf-8", errors="replace")
        page_soup = BeautifulSoup(page_html, "lxml")
        page_text = trafilatura.extract(page_html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        try:
            page_structured = extruct.extract(page_html, base_url=final_url, syntaxes=["json-ld"])
        except Exception:
            page_structured = {}
        page_title = page_soup.title.get_text(" ", strip=True)[:500] if page_soup.title else ""
        page_sha256 = __import__("hashlib").sha256(raw).hexdigest()
        date_spans: dict[str, str] = {}
        page = {
            "url": final_url,
            "title": page_title,
            "main_text_excerpt": page_text[:5000],
            "block_hashes": text_block_hashes(page_soup),
            "content_sha256": page_sha256,
            "category": page_category(final_url, page_title),
            "identity_text_excerpt": identity_excerpt(page_soup),
            "declared_dates": page_dates(page_soup, page_structured, date_spans),
            "declared_date_spans": date_spans,
            "job_postings": job_postings(page_structured),
        }
        if page["category"] == "news":
            page["news_items"] = news_listing_items(final_url, page_soup)
        if page["category"] == "careers":
            page["listed_jobs"] = careers_listing_items(final_url, page_soup, page_html)
        if page["category"] == "identity" or probe:
            page["category"] = page["category"] or "identity"
            page["site_contact"] = site_contact(final_url, page_soup, [], html_text=page_html, page_sha256=page_sha256)
        return page, _social_links(final_url, page_soup, page_html, page_sha256), 2, len(raw), elapsed, None
    except Exception as exc:
        return None, [], page_request_count, 0, int((time.monotonic() - started) * 1000), f"{type(exc).__name__}: {str(exc)[:120]}"


def _merge_contact(base: dict[str, Any], others: list[dict[str, Any]]) -> dict[str, Any]:
    merged = {key: list(base.get(key) or []) for key in ("organisations", "emails", "phones")}
    sources = dict(base.get("sources") or {})
    for other in others:
        for key in ("emails", "phones"):
            merged[key] += [item for item in other.get(key) or [] if item not in merged[key]]
        for key, source in (other.get("sources") or {}).items():
            sources.setdefault(key, source)
    return {"organisations": merged["organisations"][:5], "emails": merged["emails"][:5], "phones": merged["phones"][:3],
            "sources": sources}


def _jsonld_organisations(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            kind = value.get("@type")
            kinds = set(kind if isinstance(kind, list) else [kind])
            if kinds & {"Organization", "Corporation", "LocalBusiness", "Store", "Restaurant"}:
                values.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(metadata.get("json-ld", []))
    return values[:20]


def _extraction_state(text: str, soup: BeautifulSoup) -> str:
    return "js_fallback_candidate" if len(text.strip()) < 100 and len(soup.select("script[src]")) >= 2 else "static_complete"


def fetch_website(url: str | None, *, timeout: float = 15.0, max_bytes: int = 2_000_000, source_type: str = "registry_linked_company_website",
                  probe: bool = False, probe_terms: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch a homepage and up to 4 category pages. probe=True (website candidates) instead checks at most 2
    identity pages (contact, about, legal), and only when the homepage mentions one of `probe_terms` (if given)."""
    supplied_url = str(url or "").strip()
    supplied_scheme = bool(re.match(r"^https?://", supplied_url, re.I))
    normalized = normalize_homepage(url)
    if not normalized:
        return evidence("website", "not_found", source_type, "https://data.brreg.no/enhetsregisteret/api/enheter", note="No valid registry website URL"), {"requests": 0, "bytes": 0, "latencies_ms": []}
    try:
        assert_public_url(normalized)
    except ValueError as exc:
        return evidence("website", "blocked", source_type, normalized, note=str(exc)), {"requests": 0, "bytes": 0, "latencies_ms": []}
    if not _robots_allowed(normalized, timeout):
        return evidence("website", "blocked", source_type, normalized, note="robots.txt disallows this user agent"), {"requests": 1, "bytes": 0, "latencies_ms": []}
    started = time.monotonic()
    request = urllib.request.Request(normalized, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with host_slot(normalized) as started, SAFE_OPENER.open(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            if len(raw) > max_bytes:
                return evidence("website", "blocked", source_type, normalized, note="Homepage exceeds byte limit"), {"requests": 2, "bytes": len(raw), "latencies_ms": [elapsed]}
            if "html" not in content_type.lower():
                return evidence("website", "source_error", source_type, normalized, note=f"Unsupported content type: {content_type}"), {"requests": 2, "bytes": len(raw), "latencies_ms": [elapsed]}
            final_url = response.geturl()
            assert_public_url(final_url)
        html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        structured = extruct.extract(html, base_url=final_url, syntaxes=["json-ld", "microdata", "opengraph"])
        text = trafilatura.extract(html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        description_tag = soup.select_one('meta[name="description"], meta[property="og:description"]')
        description = str(description_tag.get("content") or "").strip() if description_tag else ""
        homepage_sha256 = __import__("hashlib").sha256(raw).hexdigest()
        homepage_date_spans: dict[str, str] = {}
        value = {
            "requested_url": normalized,
            "final_url": final_url,
            "registered_domain": _registered_domain(final_url),
            "title": title[:500],
            "description": description[:2000],
            "main_text_excerpt": text[:5000],
            "block_hashes": text_block_hashes(soup),
            "social_links": _social_links(final_url, soup, html, homepage_sha256),
            "structured_organisations": _jsonld_organisations(structured),
            "content_sha256": homepage_sha256,
            "extraction_state": _extraction_state(text, soup),
            "declared_dates": page_dates(soup, structured, homepage_date_spans),
            "declared_date_spans": homepage_date_spans,
            "job_postings": job_postings(structured),
            "identity_text_excerpt": identity_excerpt(soup),
            "feed_links": feed_links(final_url, soup),
            "news_items": news_listing_items(final_url, soup, articles_only=True),
        }
        pages = [{"url": final_url, "title": title[:500], "main_text_excerpt": text[:5000], "content_sha256": value["content_sha256"],
                  "block_hashes": value["block_hashes"]}]
        social = value["social_links"]
        crawl_errors = []
        requests = 2
        bytes_received = len(raw)
        page_latencies = [elapsed]
        homepage_domain = value["registered_domain"]
        if probe:
            folded = " ".join([title, description, text, value["identity_text_excerpt"]]).casefold()
            mentions = probe_terms is None or any(term in folded for term in probe_terms)
            page_urls = probe_links(final_url, soup) if mentions else []
        else:
            page_urls = _priority_links(final_url, soup)
        for page_url in page_urls:
            page, page_social, page_requests, page_bytes, page_elapsed, page_error = _fetch_secondary_page(
                page_url,
                homepage_domain=homepage_domain,
                timeout=timeout,
                max_bytes=min(max_bytes, 1_000_000),
                probe=probe,
            )
            requests += page_requests
            bytes_received += page_bytes
            if page_elapsed:
                page_latencies.append(page_elapsed)
            if page:
                pages.append(page)
                social.extend(page_social)
            elif page_error:
                crawl_errors.append({"url": page_url, "error": page_error})
        value["pages"] = pages
        social.extend(structured_social_links(value["structured_organisations"], final_url, homepage_sha256))
        value["site_contact"] = _merge_contact(site_contact(final_url, soup, value["structured_organisations"], html_text=html,
                                                            page_sha256=homepage_sha256),
                                               [page.get("site_contact") or {} for page in pages[1:]])
        # first source of each profile wins (homepage before secondary pages), a header/footer/sameAs link before a body link
        merged_social: dict[tuple[str, str], dict[str, Any]] = {}
        for item in sorted(social, key=lambda item: item.get("location") not in {"footer", "header", "jsonld_same_as"}):
            merged_social.setdefault((item["platform"], item["url"]), item)
        value["social_links"] = list(merged_social.values())
        value["crawl_errors"] = crawl_errors
        return evidence("website", "available", source_type, final_url, value=value, note="Company-controlled claim layer; not an official registry fact", content_sha256=value["content_sha256"]), {"requests": requests, "bytes": bytes_received, "latencies_ms": page_latencies}
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        status = "not_found" if exc.code in {404, 410} else "source_error"
        return evidence("website", status, source_type, normalized, note=f"HTTP {exc.code}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
    except urllib.error.URLError as exc:
        if not supplied_scheme and normalized.startswith("https://"):
            first_elapsed = int((time.monotonic() - started) * 1000)
            record, metrics = fetch_website("http://" + supplied_url, timeout=timeout, max_bytes=max_bytes, source_type=source_type,
                                            probe=probe, probe_terms=probe_terms)
            metrics["requests"] += 2
            metrics["latencies_ms"].insert(0, first_elapsed)
            return record, metrics
        elapsed = int((time.monotonic() - started) * 1000)
        return evidence("website", "source_error", source_type, normalized, note=f"URLError: {str(exc.reason)[:180]}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return evidence("website", "source_error", source_type, normalized, note=f"{type(exc).__name__}: {str(exc)[:180]}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
