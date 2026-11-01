from __future__ import annotations

import re
import unicodedata
import urllib.parse
from typing import Any

import tldextract


LEGAL_AND_GENERIC = {
    "as", "asa", "ans", "da", "enk", "iks", "sa", "sam", "sti", "stiftelsen",
    "nuf", "ab", "b", "v", "limited", "ltd", "inc", "plc", "the", "og", "and",
}


def _tokens(value: Any) -> list[str]:
    text = str(value or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return [token for token in re.findall(r"[a-z0-9]+", text) if token not in LEGAL_AND_GENERIC and len(token) > 1]


def _structured_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"name", "legalName", "alternateName"} and isinstance(child, str):
                names.append(child)
            else:
                names.extend(_structured_names(child))
    elif isinstance(value, list):
        for child in value:
            names.extend(_structured_names(child))
    return names


def org_number_present(text: Any, org: str) -> bool:
    """Exact organisation number as a standalone token, allowing 'NO 923 609 016 MVA' style spacing."""
    if not org or len(org) != 9:
        return False
    pattern = r"(?<!\d)" + r"[\s.\u00a0-]?".join([org[0:3], org[3:6], org[6:9]]) + r"(?!\d)"
    return bool(re.search(pattern, str(text or "")))


_ORG_NUMBER_RE = re.compile(r"(?<!\d)(\d{3})[\s.\u00a0-]?(\d{3})[\s.\u00a0-]?(\d{3})(?!\d)")


def org_numbers_in(text: Any) -> set[str]:
    """Organisation-number-shaped tokens (9 digits, optionally 3-3-3 spaced) with a valid mod-11 check digit."""
    found = set()
    for match in _ORG_NUMBER_RE.finditer(str(text or "")):
        digits = "".join(match.groups())
        weights = (3, 2, 7, 6, 5, 4, 3, 2)
        remainder = sum(int(d) * w for d, w in zip(digits[:8], weights)) % 11
        check = 0 if remainder == 0 else 11 - remainder
        if check != 10 and check == int(digits[8]) and digits[0] in "89":
            found.add(digits)
    return found


def number_quote(value: dict[str, Any], numbers: Any) -> dict[str, Any] | None:
    """The exact page text around the first of `numbers` found: {claim_span, page_url, content_sha256}."""
    sources = [(value.get("final_url"), value.get("content_sha256"), value.get("identity_text_excerpt")),
               (value.get("final_url"), value.get("content_sha256"), value.get("main_text_excerpt"))]
    sources += [(page.get("url"), page.get("content_sha256"), page.get(key)) for page in value.get("pages") or []
                for key in ("identity_text_excerpt", "main_text_excerpt")]
    for url, sha, text in sources:
        text = " ".join(str(text or "").split())
        for number in numbers:
            match = re.search(r"(?<!\d)" + r"[\s.\u00a0-]?".join([number[0:3], number[3:6], number[6:9]]) + r"(?!\d)", text)
            if match:
                return {"claim_span": " ".join(text[max(0, match.start() - 80):match.end() + 40].split()), "page_url": url, "content_sha256": sha}
    return None


LEGAL_FORM_SUFFIX = r"(?:AS|ASA|ANS|DA|SA|BA|NUF|ENK|KS|IKS|SF|AB|ApS|GmbH|Ltd|BRL)"
_LEGAL_NAME_RE = re.compile(r"((?:[A-ZÆØÅ0-9][\w&'’.\-]*\s+(?:(?:og|&|i|for|av|de|and)\s+)?){1,6})" + LEGAL_FORM_SUFFIX + r"(?![\wÆØÅæøå])")


def other_legal_names(own_name: Any, texts: list[Any]) -> list[str]:
    """Legal-entity names ("Xxx Yyy AS") printed in identity/footer text whose name words differ from this company's."""
    own = _tokens(own_name)
    found: list[str] = []
    for text in texts:
        for match in _LEGAL_NAME_RE.finditer(" ".join(str(text or "").split())):
            name = re.sub(r"^(?:\d+\s+)+", "", " ".join(match.group(0).split()))  # "© 2026 Firma AS" -> "Firma AS"
            words = _tokens(name)
            if not words or words == own:
                continue
            # trailing words equal to our name ("Kontakt Perfekt Hage AS") are ours, with page words before them
            if len(words) > len(own) and own and words[-len(own):] == own:
                continue
            if name not in found:
                found.append(name)
    return found


def own_name_phrase_present(own_name: Any, texts: list[Any]) -> bool:
    """This company's full legal name, legal form included ("Perfekt Hage AS"), appears as one phrase on the site."""
    words = _words(own_name)
    if not words:
        return False
    for text in texts:
        seen = _words(text)
        if any(seen[index:index + len(words)] == words for index in range(len(seen) - len(words) + 1)):
            return True
    return False


def related_org_numbers(profile: dict[str, Any]) -> set[str]:
    """Other entities of the same corporate group (Brønnøysund konsernstruktur) and the registered parent unit."""
    own = str(profile.get("organisation_number") or "")
    numbers: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            number = str(node.get("organisasjonsnummer") or "")
            if len(number) == 9 and number != own:
                numbers.add(number)
            for child in node.get("children") or []:
                walk(child)

    group = (profile.get("evidence") or {}).get("group") or {}
    if group.get("status") == "available":
        walk(group.get("value") or {})
    row = ((profile.get("evidence") or {}).get("registry") or {}).get("value") or {}
    parent = re.sub(r"\D", "", str(row.get("overordnetEnhet") or ""))
    if len(parent) == 9 and parent != own:
        numbers.add(parent)
    return numbers


LEGAL_FORM_SUFFIXES = {"as", "asa", "ans", "da", "enk", "sa", "nuf", "ba", "bbl", "iks", "ks", "sf", "stiftelse"}
POST_BOX_PREFIXES = ("postboks", "boks", "pb", "postbox", "p o box")


def _fold(value: Any) -> str:
    text = str(value or "").translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()


def _words(value: Any) -> list[str]:
    """Every folded alphanumeric word, including single letters (unlike _tokens)."""
    return re.findall(r"[a-z0-9]+", _fold(value))


def _contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    return bool(needle) and any(haystack[i:i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1))


def full_legal_name_words(name: Any) -> list[str]:
    """The legal name as a word sequence with only the trailing legal form removed ('A-SEAFOOD AS' -> a seafood)."""
    words = _words(name)
    while words and words[-1] in LEGAL_FORM_SUFFIXES:
        words.pop()
    while words and words[0] in LEGAL_FORM_SUFFIXES:
        words.pop(0)
    return words


def registry_contact_details(profile: dict[str, Any]) -> dict[str, list[Any]]:
    """Postcode+city, street addresses and phone numbers from the registry row (business and postal address)."""
    row = (profile.get("evidence", {}).get("registry") or {}).get("value") or {}
    postcode_city, streets, phones = [], [], []
    for prefix in ("forretningsadresse", "postadresse"):
        postcode = re.sub(r"\D", "", str(row.get(f"{prefix}.postnummer") or ""))
        city = _words(row.get(f"{prefix}.poststed"))
        if len(postcode) == 4 and city:
            postcode_city.append((postcode, city))
        street = _words(row.get(f"{prefix}.adresse"))
        # A street counts only with a house number; post boxes are not a street address.
        if street and any(word.isdigit() for word in street) and not " ".join(street).startswith(POST_BOX_PREFIXES):
            streets.append(street)
    for key in ("telefon", "mobil"):
        digits = re.sub(r"\D", "", str(row.get(key) or ""))
        if digits.startswith("0047"):
            digits = digits[4:]
        elif digits.startswith("47") and len(digits) == 10:
            digits = digits[2:]
        if len(digits) == 8:
            phones.append(digits)
    roles = (profile.get("evidence", {}).get("roles") or {}).get("value") or {}
    people = [_words(item.get("name")) for item in roles.get("roles") or [] if item.get("name") and not item.get("organisation_number")]
    return {"postcode_city": postcode_city, "street_address": streets, "phone": phones,
            "company_words": _words(profile.get("name")), "person_names": [name for name in people if name]}


# Words that label a phone number rather than name its owner ("Ring oss", "Kontakt oss", "Tlf").
PHONE_LABEL_WORDS = {"tlf", "tel", "telefon", "telephone", "phone", "mobil", "mob", "mobile", "ring", "oss", "kontakt",
                     "contact", "us", "call", "send", "epost", "e", "post", "sentralbord", "kundeservice", "vakttelefon",
                     "hovednummer", "faks", "fax", "info", "telefonnummer"}
PERSON_NAME_TAIL = re.compile(r"((?:[A-ZÆØÅ][a-zæøåéü]+(?:-[A-ZÆØÅ][a-zæøåéü]+)?\s+){1,3}[A-ZÆØÅ][a-zæøåéü]+(?:-[A-ZÆØÅ][a-zæøåéü]+)?)$")


def phone_belongs_to_person(before: str, details: dict[str, list[Any]]) -> bool:
    """A phone printed right after a person's name (a contact person's direct line) is not company identity."""
    tail = re.sub(r"(?:[\s,:;/|()·-]|(?i:tlf|tel|telefon|mobil|mob|phone)\.?)+$", "", before[-80:])
    tail_words = _words(tail)
    for person in details.get("person_names", []):
        if len(person) >= 2 and tail_words[-len(person):] == person:
            return True
    match = PERSON_NAME_TAIL.search(tail)
    if not match:
        return False
    words = _words(match.group(1))
    company = set(details.get("company_words", []))
    return len(words) >= 2 and not any(word in PHONE_LABEL_WORDS or word in company for word in words)


def _street_word(word: str) -> str:
    """Nynorsk/bokmål spellings of 'road' are the same street: Håndverkervegen = Håndverkerveien, Storveg = Storvei."""
    return re.sub(r"ve(?:g|i)(en)?$", r"vei\1", word)


def registry_detail_matches(text: Any, details: dict[str, list[Any]]) -> list[str]:
    """Which registry details (postcode+city, street address, phone) appear in the text."""
    words = _words(text)
    matched = []
    if any(_contains_sequence(words, [postcode, *city]) for postcode, city in details.get("postcode_city", [])):
        matched.append("postcode_city")
    street_words = [_street_word(word) for word in words]
    if any(_contains_sequence(street_words, [_street_word(word) for word in street]) for street in details.get("street_address", [])):
        matched.append("street_address")
    raw = str(text or "")
    for phone in details.get("phone", []):
        pattern = r"(?<!\d)(?:(?:\+|00)\s?47[\s. -]*)?" + r"[\s. -]?".join(phone) + r"(?!\d)"
        if any(not phone_belongs_to_person(raw[:hit.start()], details) for hit in re.finditer(pattern, raw)):
            matched.append("phone")
            break
    return matched


def own_contact_blocks(value: dict[str, Any]) -> list[tuple[str, str]]:
    """The homepage footer/legal block plus the footer/legal block of the site's own contact/about page."""
    blocks = [(value.get("final_url") or "", str(value.get("identity_text_excerpt") or ""))]
    for page in value.get("pages", []):
        if page.get("category") == "identity" and page.get("identity_text_excerpt"):
            blocks.append((page.get("url") or "", str(page["identity_text_excerpt"])))
    return [(url, text) for url, text in blocks if text.strip()]


def footer_registry_proof(profile: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    """Registry details found in the site's own footer/contact blocks, and whether the full legal name is there too."""
    details = registry_contact_details(profile)
    name_words = full_legal_name_words(profile.get("name"))
    any_detail: list[str] = []
    for url, text in own_contact_blocks(value):
        matched = registry_detail_matches(text, details)
        if not matched:
            continue
        any_detail = any_detail or matched
        if _contains_sequence(_words(text), name_words):
            return {"name_with_detail": True, "details": matched, "page": url}
    return {"name_with_detail": False, "details": any_detail, "page": None}


# Paths that still count as the site root (index files and language roots).
ROOT_PATHS = {"", "/index.html", "/index.htm", "/index.php", "/index.asp", "/index.aspx", "/no", "/nb", "/nn", "/en", "/hjem", "/home"}


def landing_checks(value: dict[str, Any]) -> dict[str, bool]:
    """Where the crawl landed: on another brand's domain (TLD ignored), or below the site root."""
    requested = urllib.parse.urlparse(value.get("requested_url") or "")
    final = urllib.parse.urlparse(value.get("final_url") or value.get("requested_url") or "")
    requested_label = tldextract.extract(requested.hostname or "").domain.casefold()
    final_label = tldextract.extract(final.hostname or "").domain.casefold()
    return {
        "cross_domain_redirect": bool(requested_label and final_label and requested_label != final_label),
        "deep_path_landing": final.path.rstrip("/").casefold() not in ROOT_PATHS,
    }


def assess_website_identity(profile: dict[str, Any]) -> dict[str, Any]:
    website = profile.get("evidence", {}).get("website", {})
    value = website.get("value") or {}
    core = _tokens(profile.get("name"))
    hostname = urllib.parse.urlparse(value.get("final_url") or website.get("source_url") or "").hostname or ""
    structured_names = _structured_names(value.get("structured_organisations") or [])
    rendered = value.get("js_fallback") or {}
    homepage_identity_parts = [
        value.get("title"), value.get("description"), value.get("identity_text_excerpt"), hostname, *structured_names,
        rendered.get("title"),
    ]
    candidate_parts = [
        *homepage_identity_parts, value.get("main_text_excerpt"),
        *[page.get("title") for page in value.get("pages", [])],
        *[page.get("main_text_excerpt") for page in value.get("pages", [])],
        *[page.get("identity_text_excerpt") for page in value.get("pages", [])],
    ]
    candidate_parts.append(rendered.get("main_text_excerpt"))
    candidate_text = " ".join(str(part or "") for part in candidate_parts)
    homepage_candidate_text = " ".join(str(part or "") for part in [*homepage_identity_parts, value.get("main_text_excerpt"), rendered.get("main_text_excerpt")])
    normalized_candidate_text = " ".join(_tokens(candidate_text))
    candidate_tokens = set(_tokens(candidate_text))
    org_digits = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    compact_candidate = re.sub(r"\D", "", candidate_text)
    compact_homepage_candidate = re.sub(r"\D", "", homepage_candidate_text)
    overlap = sorted(set(core) & candidate_tokens)
    ratio = len(overlap) / len(set(core)) if core else 0.0
    reasons = []
    parked_markers = (
        "domain is for sale", "domain for sale", "hugedomains", "parked at", "miss hosting",
        "her flytter snart en ny gjest", "has been informing visitors",
        "find the best information and most relevant links on all topics related to",
    )
    normalized_raw = unicodedata.normalize("NFKD", candidate_text).encode("ascii", "ignore").decode().casefold()
    homepage_token_sets = [set(_tokens(part)) for part in homepage_identity_parts if part]
    exact_homepage_name = bool(core and any(set(core).issubset(tokens) for tokens in homepage_token_sets))
    substantive_homepage = len(str(value.get("main_text_excerpt") or "").strip()) >= 100
    proof = footer_registry_proof(profile, value)
    homepage_numbers = org_numbers_in(homepage_candidate_text)
    is_business_sports_club = bool(re.search(r"(?:^|\s)B\.?\s*I\.?\s*L\.?(?:\s|$)", str(profile.get("name") or ""), re.I))
    if any(marker in normalized_raw for marker in parked_markers):
        score = 0.1
        reasons.append("captured page is a parked, for-sale, or generic hosting placeholder")
    elif is_business_sports_club and "bedriftsidrett" not in normalized_candidate_text and "b i l" not in normalized_candidate_text:
        score = 0.3
        reasons.append("business sports-club entity points to the operating company's site without club evidence")
    elif org_digits and org_number_present(homepage_candidate_text, org_digits) and len(homepage_numbers - {org_digits}) == 0:
        # v3 (stricter): a standalone token, and no other organisation number on the homepage (group/portfolio pages).
        score = 1.0
        reasons.append("exact organisation number appears in homepage identity evidence")
    elif len(core) >= 2 and exact_homepage_name:
        score = 0.95
        reasons.append("all normalized legal-name tokens appear together in homepage identity evidence")
    elif len(core) == 1 and exact_homepage_name and substantive_homepage and proof["details"]:
        score = 0.95
        reasons.append("single distinctive legal-name token appears in homepage identity evidence with substantive content, "
                       f"and the registry {'/'.join(proof['details'])} appears in the site's own footer/contact block")
    elif len(core) == 1 and exact_homepage_name and substantive_homepage:
        # One word (often also the domain itself) is too weak alone: require a registry detail or the org number.
        score = 0.85
        reasons.append("single legal-name token matches, but no registry postcode, address or phone appears in the site's own footer/contact block")
    elif ratio >= 0.75 and len(overlap) >= 2:
        score = 0.85
        reasons.append("most legal-name tokens appear, but exact identity is incomplete")
    elif ratio >= 0.5 and len(overlap) >= 2:
        score = 0.65
        reasons.append("partial legal-name overlap only")
    else:
        score = 0.3
        reasons.append("registry-linked URL lacks strong exact-entity identity evidence")
    if 0.3 <= score < 1.0 and not any(marker in normalized_raw for marker in parked_markers):
        for page in value.get("pages", []):
            # Only the page's own legal/footer block counts, and only when it names a single organisation
            # number: group pages listing several subsidiaries are related, not exact.
            page_text = str(page.get("identity_text_excerpt") or "")
            numbers = set(re.findall(r"(?<!\d)(\d{3})[\s.\u00a0-]?(\d{3})[\s.\u00a0-]?(\d{3})(?!\d)", page_text))
            if org_number_present(page_text, org_digits) and len(numbers) == 1:
                score = 1.0
                reasons.append(f"exact organisation number appears on the company's own page {page.get('url')}")
                break
    other_numbers = sorted(homepage_numbers - {org_digits})
    if other_numbers and org_number_present(homepage_candidate_text, org_digits):
        # v3 (stricter): a homepage listing this and other organisation numbers is a group/portfolio/directory page.
        score = min(score, 0.6)
        reasons.append(f"homepage lists other organisation number(s) ({', '.join(other_numbers[:3])}) next to this one; "
                       "multi-entity page, not published")
    group_numbers = sorted(related_org_numbers(profile) & org_numbers_in(candidate_text))
    group_site = None
    if group_numbers:
        # v3: a page showing the parent's or a group member's organisation number is the group's site, never this company's.
        group_site = {"organisation_numbers": group_numbers, "relationship": "site shows organisation number(s) of the corporate group / parent"}
        score = min(score, 0.6)
        reasons.append(f"site shows group/parent organisation number(s) {', '.join(group_numbers)}: labelled as group site, not published")
        group_site.update(number_quote(value, group_numbers) or {})
    # v4 (stricter): the site's own identity blocks print organisation number(s), none of them this company's: it is
    # another entity's site (e.g. "Perfekt Hus og Hage AS, org.nr 925 353 191" for PERFEKT HAGE AS), never published.
    identity_numbers = set()
    for text in [value.get("identity_text_excerpt"), *[page.get("identity_text_excerpt") for page in value.get("pages", [])]]:
        identity_numbers |= org_numbers_in(text)
    other_entity_site = None
    if identity_numbers and org_digits not in identity_numbers and not group_numbers:
        foreign = sorted(identity_numbers)
        other_entity_site = {"organisation_numbers": foreign, "relationship": "site prints another entity's organisation number, not this company's",
                             **(number_quote(value, foreign) or {})}
        score = min(score, 0.6)
        reasons.append(f"site's own footer/legal text prints organisation number(s) {', '.join(foreign[:3])} and not this one: "
                       "another entity's site, not published")
    # v4 (stricter): proven by name only, but the site's own footer/legal text names a different legal entity
    # ("Perfekt Hus og Hage AS" for PERFEKT HAGE AS) and this company's full name never appears as one phrase: the site
    # is the other company's, never published, even when no organisation number is printed.
    identity_texts = [value.get("identity_text_excerpt"), *[page.get("identity_text_excerpt") for page in value.get("pages", [])]]
    if score < 1.0 and not other_entity_site and not group_numbers:
        foreign_names = other_legal_names(profile.get("name"), identity_texts)
        if foreign_names and not own_name_phrase_present(profile.get("name"), [*homepage_identity_parts, *identity_texts,
                                                                                value.get("main_text_excerpt")]):
            other_entity_site = {"legal_names": foreign_names[:3], "relationship": "site's footer names another legal entity, not this company"}
            score = min(score, 0.6)
            reasons.append(f"site's own footer/legal text names {', '.join(foreign_names[:2])} and never this company's full name: "
                           "another company's site, not published")
    landing = landing_checks(value)
    requires_org_number = landing["cross_domain_redirect"] or landing["deep_path_landing"]
    if requires_org_number and 0.3 < score < 1.0:
        # Redirects to another brand and directory/chain/parent-group subpages never publish on name or address.
        score = 0.6
        reasons.append("landed on " + ("another domain" if landing["cross_domain_redirect"] else "a page below the site root")
                       + f" ({value.get('final_url')}); only the exact organisation number can prove identity there")
    if proof["name_with_detail"] and score >= 0.9:
        reasons.append(f"full legal name and registry {'/'.join(proof['details'])} appear together in the site's own footer/contact block {proof['page']}")
    status = "exact" if score >= 0.9 else "review" if score >= 0.8 else "related_or_uncertain"
    return {
        "status": status,
        "score": score,
        "publishable": status == "exact",
        "legal_name_tokens": core,
        "matched_tokens": overlap,
        "reasons": reasons,
        "registry_detail_proof": proof,
        "landing": landing,
        "requires_org_number": requires_org_number,
        "group_site": group_site,
        "other_entity_site": other_entity_site,
        "method": "deterministic_name_org_evidence_v5",
    }


# v5: where on the company's own page a profile link may sit to count as the company's own profile.
OWN_PROFILE_LOCATIONS = {"footer", "header", "jsonld_same_as"}
# Words a handle may add to the company or brand name ("jaeder.as", "kontorlev.no", "fiskeeksportnorge").
HANDLE_SUFFIXES = {"as", "asa", "no", "norge", "norway", "official", "offisiell", "the"}
# Path segments that are a platform's own words, not the company's handle.
HANDLE_PREFIXES = {"company", "channel", "user", "c", "pages", "pg", "people"}


def handle_slug(url: str) -> str:
    """The company-chosen part of a profile URL ('linkedin.com/company/jaeder-as' -> 'jaeder-as'); '' for numeric ids."""
    parsed = urllib.parse.urlparse(url or "")
    parts = [part for part in urllib.parse.unquote(parsed.path).split("/") if part]
    if "facebook." in parsed.netloc.casefold() and len(parts) > 1 and parts[0] == "p":
        parts = parts[1:]  # Facebook's newer page URLs: facebook.com/p/Page-Name-100034744493193 (on Instagram /p/ is a post)
    while parts and parts[0].casefold() in HANDLE_PREFIXES and len(parts) > 1:
        parts = parts[1:]
    slug = (parts[0] if parts else "").lstrip("@")
    slug = re.sub(r"(?<=[^\W\d_])-\d{8,}$", "", slug)  # Facebook's 'Page-Name-1159100470769905': the name, then the page id
    if not slug or re.fullmatch(r"[\d_\-]+", slug) or re.fullmatch(r"UC[\w-]{20,}", slug):
        return ""  # numeric page id or YouTube channel id: nothing the company named
    return slug


def _compact_variants(text: str) -> set[str]:
    """Folded alphanumerics (ø -> o, æ -> ae), plus the variant where non-ASCII letters were dropped, as LinkedIn
    does when it builds a slug ('høgskole' -> 'h-gskole')."""
    folded = "".join(re.findall(r"[a-z0-9]+", _fold(text)))
    dropped = "".join(re.findall(r"[a-z0-9]+", str(text or "").casefold().encode("ascii", "ignore").decode()))
    return {variant for variant in (folded, dropped) if variant}


def _segments(text: str, words: set[str]) -> list[str] | None:
    """Split `text` fully into `words` (longest first); None when some part is not one of them."""
    best: dict[int, list[str] | None] = {len(text): []}
    for start in range(len(text) - 1, -1, -1):
        best[start] = None
        for word in sorted(words, key=len, reverse=True):
            if word and text.startswith(word, start) and best.get(start + len(word)) is not None:
                best[start] = [word, *best[start + len(word)]]
                break
    return best[0]


def site_brands(value: dict[str, Any] | None) -> list[str]:
    """The brand the verified site itself shows: its registered-domain label ('kontorlev' for kontorlev.no)."""
    domain = str((value or {}).get("registered_domain") or "")
    label = domain.split(".")[0] if domain else ""
    compact = "".join(re.findall(r"[a-z0-9]+", _fold(label)))
    return [compact] if len(compact) >= 3 and not compact.isdigit() else []


def assess_social_identity(profile: dict[str, Any], link: dict[str, str], brands: list[str] | None = None) -> dict[str, Any]:
    """A profile linked from the company's own site is the company's when its handle is the legal name or the site's
    brand (high), or is made only of legal-name words and the brand with at least one distinctive word (medium).
    Anything else, a numeric id, or a link outside the page header/footer/JSON-LD sameAs is not published."""
    from .domain_guess import GENERIC_LABEL_TOKENS  # domain_guess imports this module

    core = _tokens(profile.get("name"))
    core_compact = "".join(core)
    slug = handle_slug(link.get("url") or "")
    location = link.get("location")
    brands = [brand for brand in brands or [] if brand]
    confidence, reason, parts = None, "", None
    if location is not None and location not in OWN_PROFILE_LOCATIONS:
        reason = f"linked only from the page {location.replace('_', ' ')}, not from the site's header, footer or JSON-LD sameAs"
    elif not slug:
        reason = "profile URL carries only a numeric or channel id, so the handle cannot be matched to the company"
    else:
        dropped_name = "".join(token for token in re.findall(r"[a-z0-9]+", str(profile.get("name") or "").casefold().encode("ascii", "ignore").decode())
                               if token not in LEGAL_AND_GENERIC and len(token) > 1)
        legal_names = {core_compact, dropped_name} - {""}
        for handle in _compact_variants(slug):
            exact = legal_names | set(brands)
            for name in sorted(exact, key=len, reverse=True):
                if handle == name or (handle.startswith(name) and _segments(handle[len(name):], HANDLE_SUFFIXES) is not None):
                    which = "legal name" if name in legal_names else "verified site's brand (domain)"
                    confidence, reason = "high", f"handle '{slug}' is the {which}"
                    break
            if confidence:
                break
            words = {*core, *brands}
            parts = _segments(handle, words | HANDLE_SUFFIXES)
            distinctive = [part for part in parts or [] if part in words and part not in GENERIC_LABEL_TOKENS and len(part) >= 4]
            if parts and distinctive:
                confidence, reason = "medium", f"handle '{slug}' is made only of legal-name words and the site's brand ({', '.join(parts)})"
                break
        if not confidence:
            reason = f"handle '{slug}' does not match the legal name ({' '.join(core) or 'none'}) or the verified site's brand ({', '.join(brands) or 'none'})"
    score = {"high": 0.98, "medium": 0.9}.get(confidence or "", 0.3)
    return {
        **link,
        "identity_score": score,
        "publishable": confidence is not None,
        "confidence": confidence,
        "matched_tokens": [part for part in parts or [] if part in core] if parts else [token for token in core if token in "".join(_compact_variants(slug))],
        "reason": reason,
        "method": "social_handle_matches_name_or_site_brand_v2",
    }


LINK_KEYS = ("platform", "url", "page_url", "content_sha256", "raw", "location", "confidence", "reason")


def link_with_provenance(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in LINK_KEYS if key in item}


def apply_website_identity_gate(profile: dict[str, Any], website: dict[str, Any]) -> dict[str, Any]:
    if website.get("status") != "available":
        return {"website": website, "assessment": None, "quarantined_social_links": 0}
    temporary_profile = {**profile, "evidence": {**profile.get("evidence", {}), "website": website}}
    value = website.get("value") or {}
    assessment = assess_website_identity(temporary_profile)
    value["identity_assessment"] = assessment
    original = list(value.get("discovered_social_links") or value.get("social_links") or [])
    value["discovered_social_links"] = original
    social_assessments = [assess_social_identity(profile, link, site_brands(value)) for link in original]
    value["social_link_assessments"] = social_assessments
    value["social_links"] = [
        link_with_provenance(item)
        for item in social_assessments
        if assessment["publishable"] and item["publishable"]
    ]
    website["value"] = value
    return {
        "website": website,
        "assessment": assessment,
        "quarantined_social_links": len(original) - len(value["social_links"]),
    }
