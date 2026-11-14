"""Which website domains are safe to attribute from a registry entry alone.

A registry `hjemmeside` that we could not read (or a sub-unit's `hjemmeside`) is published only from the official
record, without proof on the site itself. That is safe only when the domain belongs to one filer: a domain filed as
the website of more than one main unit in the bulk entity file (housing-cooperative managers, chains, franchises,
associations) or a known platform/franchise domain is marked ambiguous and not published.
"""
from __future__ import annotations

import csv
import gzip
import urllib.parse
from collections import Counter
from pathlib import Path

# Hosting/social/directory platforms: a domain here says nothing about which company it is.
PLATFORM_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "tiktok.com", "google.com",
    "business.site", "wixsite.com", "wix.com", "squarespace.com", "weebly.com", "jimdo.com", "jimdofree.com", "webnode.no",
    "webnode.com", "blogspot.com", "wordpress.com", "github.io", "netlify.app", "vercel.app", "godaddysites.com",
    "mystrikingly.com", "site123.me", "one.com", "finn.no", "proff.no", "gulesider.no", "1881.no", "mittanbud.no",
    "purehelp.no", "brreg.no", "speaker.no", "123hjemmeside.no", "hjemmeside.no", "linktr.ee", "carrd.co", "canva.site",
    "shopify.com", "myshopify.com", "bookingkit.de", "timma.no", "bestille.no", "vipps.no",
}
# Chains and franchises whose stores file the chain's domain as their own.
FRANCHISE_DOMAINS = {
    "7-eleven.no", "narvesen.no", "circlek.no", "esso.no", "shell.no", "uno-x.no", "yx.no", "rema.no", "kiwi.no", "meny.no",
    "spar.no", "joker.no", "coop.no", "bunnpris.no", "extra.no", "obs.no", "mcdonalds.no", "burgerking.no", "peppes.no",
    "dominos.no", "subway.no", "eurospar.no", "europris.no", "clasohlson.no", "jernia.no", "mekonomen.no", "byggmakker.no",
    "maxbo.no", "xl-bygg.no", "optimera.no", "montér.no", "monter.no", "intersport.no", "sport1.no", "gsport.no", "xxl.no",
    "apotek1.no", "vitusapotek.no", "boots.no", "dnbeiendom.no", "eiendomsmegler1.no", "privatmegleren.no", "krogsveen.no",
    "nordvikbolig.no", "aktiv.no", "em1.no", "remax.no", "proaktiv.no", "boligpartner.no", "obos.no", "usbl.no", "bate.no",
    "oddfellow.no", "kirken.no", "idrett.no", "norian.no", "frende.no", "sparebank1.no", "eika.no", "lions.no", "rotary.no",
}


def host_of(raw: str | None) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        host = urllib.parse.urlparse(text if "://" in text else "https://" + text).hostname or ""
    except ValueError:
        return ""
    return host.casefold().rstrip(".").removeprefix("www.")


def registered_domain(raw: str | None) -> str:
    host = host_of(raw)
    if not host:
        return ""
    from .website import _registered_domain
    return (_registered_domain("https://" + host + "/") or host).casefold()


def domain_counts(bulk_path: str | Path) -> dict[str, int]:
    """Registered domain -> number of main units in the bulk entity file that file it as their website (~12 s pass)."""
    hosts: Counter[str] = Counter()
    with gzip.open(bulk_path, "rt", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        reader = csv.reader(handle, dialect=dialect)
        header = next(reader)
        column = header.index("hjemmeside") if "hjemmeside" in header else None
        if column is None:
            return {}
        for row in reader:
            if len(row) > column and row[column].strip():
                hosts[host_of(row[column])] += 1
    counts: Counter[str] = Counter()
    for host, count in hosts.items():
        if host:
            counts[registered_domain(host)] += count
    return dict(counts)


def ambiguity(raw: str | None, counts: dict[str, int] | None) -> str | None:
    """Why this domain cannot be attributed from a registry entry alone, or None when it can."""
    domain = registered_domain(raw)
    if not domain:
        return "no valid domain"
    if domain in PLATFORM_DOMAINS:
        return f"{domain} is a hosting, social or directory platform"
    if domain in FRANCHISE_DOMAINS:
        return f"{domain} is a chain or franchise domain"
    if counts is None:
        return "the bulk entity file was not available to check how many companies file this domain"
    filed = counts.get(domain, 0)
    if filed > 1:
        return f"{domain} is filed as the website of {filed} companies in the Brønnøysund bulk entity file"
    return None


# Website-record states meaning the registry-listed site could not be read at crawl time.
UNREADABLE_SITE_STATES = {"source_error", "blocked", "not_found"}


def domain_reasons(profile: dict, counts: dict[str, int] | None) -> dict[str, str | None]:
    """registered domain -> why it cannot be attributed from a registry entry alone (None: one filer), for the
    company's registry website and every sub-unit website."""
    raws = [profile.get("website")] + [item.get("website") for item in
                                       ((((profile.get("evidence") or {}).get("locations") or {}).get("value") or {}).get("locations") or [])]
    return {registered_domain(raw): ambiguity(raw, counts) for raw in raws if registered_domain(raw)}


def decide_registry_website(profile: dict, counts: dict[str, int] | None, resolver=None) -> None:
    """Keep the registry's hjemmeside (from the registry record only) when the site could not be read (blocked, timeout,
    HTTP error), no other site was proven, the domain is this company's alone and it still resolves in DNS; otherwise
    record why it is not published (ambiguous, or a dead domain). Sets profile keys in place."""
    profile["domain_filings"] = domain_reasons(profile, counts)
    for key in ("registry_website_kept", "registry_website_ambiguous", "registry_website_unresolved"):
        profile.pop(key, None)
    check = profile.get("registry_website_check") or {}
    current = (profile.get("evidence") or {}).get("website") or {}
    published = current.get("status") == "available" and \
        bool(((current.get("value") or {}).get("identity_assessment") or {}).get("publishable"))
    if published or not check.get("url") or check.get("fetch_status") not in UNREADABLE_SITE_STATES:
        return
    if "No valid registry website URL" in str(check.get("fetch_note") or ""):
        return
    reason = profile["domain_filings"].get(registered_domain(check["url"]), ambiguity(check["url"], counts))
    if reason:
        profile["registry_website_ambiguous"] = {"url": check["url"], "reason": reason}
        return
    resolves, how = (resolver or domain_resolves)(check["url"])
    if not resolves:
        profile["registry_website_unresolved"] = {"url": check["url"], "reason": how, "fetch_note": check.get("fetch_note")}
    else:
        profile["registry_website_kept"] = {"url": check["url"], "domain": host_of(check["url"]),
                                            "fetch_status": check.get("fetch_status"), "fetch_note": check.get("fetch_note"), "dns": how}


def domain_resolves(raw: str | None, timeout: float = 5.0) -> tuple[bool, str]:
    """(True, how) when the filed host or its registered domain has a DNS address; (False, why) for NXDOMAIN, no
    address, or no DNS answer in time (one retry): a domain that does not resolve may have lapsed, so it is not published."""
    import socket
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
    host = host_of(raw)
    names = [name for name in dict.fromkeys([host, "www." + host if not host.startswith("www.") else host, registered_domain(raw)]) if name]
    if not names:
        return False, "no valid domain"
    errors = []
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        for name in names:
            for _attempt in range(2):
                future = pool.submit(socket.getaddrinfo, name, 443, 0, socket.SOCK_STREAM)
                try:
                    if future.result(timeout=timeout):
                        return True, f"{name} resolves in DNS"
                except FutureTimeout:
                    errors.append(f"{name}: no DNS answer within {timeout:.0f} s")
                    continue
                except socket.gaierror as exc:
                    temporary = exc.errno == getattr(socket, "EAI_AGAIN", -3)
                    errors.append(f"{name}: {'temporary DNS failure' if temporary else 'no such domain (NXDOMAIN) or no address'}")
                    if temporary:
                        continue
                except OSError as exc:
                    errors.append(f"{name}: {type(exc).__name__}")
                break
    finally:
        pool.shutdown(wait=False)
    return False, "domain does not resolve in DNS (" + "; ".join(errors[:3]) + ")"
