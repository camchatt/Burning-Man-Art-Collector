from __future__ import annotations

from html.parser import HTMLParser
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from burning_man_scraper.enrichment.models import SearchResult
from burning_man_scraper.enrichment.providers import SearchProvider
from burning_man_scraper.identity.alias_resolver import (
    aliases_match,
    build_alias_queries,
    extract_alias_linked_names,
    gather_alias_evidence_pages,
    normalize_alias,
)
from burning_man_scraper.identity.classifier import (
    CreditClassification,
    classify_archive_credit,
    clean_text,
    ordered_unique,
    _looks_like_legal_name,
)
from burning_man_scraper.identity.models import IdentityResult, ResolvedPerson


PERSON_LINE_PATTERN = re.compile(
    r"(?i)\b(?:(?:lead\s+)?artist|founded by|founder[s]?|created by|co-?founder[s]?|directed by|led by)\b[:\s]+"
    r"([A-Z][a-z]+(?:\s+[A-Z]\.?\s*)?(?:\s+[A-Z][a-z]+){1,3})"
)
NAME_CANDIDATE_PATTERN = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2})\b"
)
SKIP_NAME_PHRASES = {
    "Burning Man",
    "Black Rock",
    "Black Rock City",
    "Art Installation",
    "Open Playa",
    "United States",
    "Public Art",
    "All Rights",
    "Privacy Policy",
    "Terms Of",
    "Home Page",
    "About Us",
    "Contact Us",
    "About Submit",
    "Submit Expense",
    "Expense Actions",
    "Lightwave Laser",
}
SKIP_NAME_TOKENS = {
    "the",
    "and",
    "with",
    "from",
    "for",
    "this",
    "that",
    "project",
    "website",
    "known",
    "burning",
    "man",
    "collective",
    "archive",
    "facebook",
    "instagram",
    "year",
    "born",
    "night",
}
BM_OFFICIAL_NAMES = {"larry harvey", "marian goodell", "crimson rose", "will roger"}


def build_identity_queries(
    *,
    project_title: str,
    year: int | str | None,
    classification: CreditClassification,
) -> list[str]:
    title = quote(project_title)
    year_text = str(year or "")
    credit = quote(classification.archive_credit)
    queries: list[str] = []
    if classification.credit_type in {"alias_or_unknown", "alias_pair"}:
        return build_alias_queries(
            alias=classification.archive_credit,
            project_title=project_title,
            year=year,
        )
    if classification.collective_name:
        collective = quote(classification.collective_name)
        queries.extend(
            [
                f"{collective} Burning Man members",
                f"{collective} Burning Man founders",
                f"{collective} lead artist",
                f"{title} {collective}",
            ]
        )
    if classification.playa_name and classification.playa_name_confidence == "high":
        queries.append(f"{quote(classification.playa_name)} Burning Man real name")
    if classification.legal_name and classification.needs_identity_search:
        queries.append(f"{quote(classification.legal_name)} Burning Man")
    queries.append(f"{title} Burning Man {year_text} artist".strip())
    return ordered_unique([query for query in queries if query.strip()])


def resolve_identity(
    *,
    year: int,
    project_title: str,
    archive_credit: str | None,
    archive_uid: str | None = None,
    archive_url: str | None = None,
    artist_website: str | None = None,
    search_client: SearchProvider | None = None,
    enable_search: bool = True,
    enable_page_fetch: bool = True,
    fetch_search_pages: bool = False,
    user_agent: str = "BurningManArtArchiveScraper/0.4 identity",
    search_limit: int = 5,
    max_queries: int = 2,
) -> IdentityResult:
    classification = classify_archive_credit(archive_credit)
    is_alias = classification.credit_type in {"alias_or_unknown", "alias_pair"}
    # For aliases, fetch search pages and use more queries by default.
    if is_alias:
        fetch_search_pages = True
        max_queries = max(max_queries, 4)
        search_limit = max(search_limit, 6)

    result = IdentityResult(
        year=year,
        project_title=project_title,
        archive_uid=archive_uid,
        archive_url=archive_url,
        archive_credit=classification.archive_credit,
        credit_type=classification.credit_type,
        legal_name=classification.legal_name,
        playa_name=classification.playa_name if classification.playa_name_confidence == "high" else None,
        playa_name_confidence=classification.playa_name_confidence
        if classification.playa_name_confidence == "high"
        else "none",
        collective_name=classification.collective_name,
        named_people=list(classification.named_people),
        artist_website=artist_website,
        notes=list(classification.notes),
    )

    # If the archive credit itself is an alias, keep it as playa_name when reliable single-token/stylized.
    if is_alias and not result.playa_name and classification.archive_credit:
        result.playa_name = classification.archive_credit
        result.playa_name_confidence = "medium"
        result.notes.append("Treating archive credit as playa/alias pending real-name confirmation.")

    # Seed resolved people from clearly named archive people.
    for person_name in classification.named_people:
        if classification.credit_type in {"person", "person_with_playa_name", "multi_person", "hybrid"}:
            result.resolved_people.append(
                ResolvedPerson(
                    name=person_name,
                    role="archive_credit",
                    confidence=0.9 if classification.credit_type != "hybrid" else 0.7,
                    source_url=archive_url,
                    source_snippet="Named in archive credit.",
                )
            )
        elif classification.credit_type == "alias_pair":
            # Prefer legal-looking sides from ambiguous aka pairs.
            if _looks_like_legal_name(person_name):
                result.resolved_people.append(
                    ResolvedPerson(
                        name=person_name,
                        role="aka_pair_candidate",
                        confidence=0.8,
                        source_url=archive_url,
                        source_snippet="Named in aka pair.",
                    )
                )

    evidence: list[SearchResult] = []
    if artist_website:
        evidence.append(
            SearchResult(
                title="Artist website",
                url=artist_website,
                snippet="Known project/artist website.",
                provider="known_url",
            )
        )

    if enable_search and search_client is not None and classification.needs_identity_search:
        queries = build_identity_queries(
            project_title=project_title,
            year=year,
            classification=classification,
        )
        result.search_queries = queries
        for query in queries[:max_queries]:
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(search_client.search, query, search_limit)
                    evidence.extend(future.result(timeout=25))
            except FuturesTimeout:
                result.notes.append(f"Search timed out for `{query}`")
            except Exception as exc:
                result.notes.append(f"Search failed for `{query}`: {exc}")

    # Deduplicate evidence URLs.
    seen_urls: set[str] = set()
    unique_evidence: list[SearchResult] = []
    for item in evidence:
        if not item.url or item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        unique_evidence.append(item)
    evidence = unique_evidence[:12]
    result.evidence_urls = [item.url for item in evidence]

    discovered: list[ResolvedPerson] = []

    if is_alias and enable_page_fetch:
        alias_value = classification.archive_credit
        # Do not trust search snippets alone for alias→legal links (too much comic/lyric noise).
        # Prefer full pages with burn context, artist sites, or alias-subject wiki pages.
        pages = gather_alias_evidence_pages(
            alias=alias_value,
            artist_website=artist_website,
            search_results=evidence,
            user_agent=user_agent,
            fetch_search_pages=fetch_search_pages,
            max_pages=6,
        )
        for page_url, page_text in pages:
            is_artist_site = bool(
                artist_website and urlsplit(page_url).netloc == urlsplit(artist_website).netloc
            )
            alias_is_page_subject = _alias_is_url_subject(alias_value, page_url)
            if _looks_like_entertainment_noise_url(page_url):
                continue
            linked = extract_alias_linked_names(
                page_text,
                alias=alias_value,
                source_url=page_url,
                require_burn_context=not (is_artist_site or alias_is_page_subject),
            )
            discovered.extend(linked)
            if not linked and is_artist_site:
                # Fall back to cautious extraction only on artist-owned pages.
                discovered.extend(
                    extract_people_from_text(page_text, source_url=page_url, base_confidence=0.55)
                )
        if discovered:
            result.notes.append("Alias real-name lookup used explicit aka/real-name evidence where possible.")
    else:
        for item in evidence:
            discovered.extend(
                extract_people_from_text(
                    item.title + " " + item.snippet,
                    source_url=item.url,
                    base_confidence=0.45,
                )
            )
            should_fetch = enable_page_fetch and _should_fetch(item.url)
            if should_fetch and item.provider != "known_url" and not fetch_search_pages:
                should_fetch = False
            if should_fetch:
                try:
                    html = fetch_text(item.url, user_agent=user_agent)
                    discovered.extend(extract_people_from_html(html, source_url=item.url))
                except Exception as exc:
                    result.notes.append(f"Fetch failed for {item.url}: {exc}")

    merged = _merge_people(
        result.resolved_people + discovered,
        exclude={classification.playa_name, classification.archive_credit if is_alias else None},
    )
    result.resolved_people = merged

    if not result.legal_name and merged:
        for top in merged:
            if top.confidence < 0.75:
                break
            if not _looks_like_legal_name(top.name):
                continue
            if top.name.lower() in BM_OFFICIAL_NAMES:
                continue
            if is_alias and top.role not in {"alias_linked", "aka_pair_candidate"} and top.confidence < 0.9:
                # Pure aliases: only accept strongly linked evidence, not random page names.
                continue
            if classification.credit_type in {"alias_or_unknown", "alias_pair", "collective", "hybrid"}:
                result.legal_name = top.name
                if is_alias and not result.playa_name:
                    result.playa_name = classification.archive_credit
                    result.playa_name_confidence = "medium"
                break
            break

    result.identity_status = _status_for(result, classification)
    return result


def extract_people_from_text(
    text: str,
    *,
    source_url: str | None = None,
    base_confidence: float = 0.5,
) -> list[ResolvedPerson]:
    people: list[ResolvedPerson] = []
    for match in PERSON_LINE_PATTERN.finditer(text or ""):
        name = clean_text(match.group(1))
        if not _usable_person_name(name):
            continue
        role = clean_text(match.group(0).split(":")[0] if ":" in match.group(0) else "mentioned")
        people.append(
            ResolvedPerson(
                name=name,
                role=role[:40],
                confidence=min(0.85, base_confidence + 0.25),
                source_url=source_url,
                source_snippet=clean_text(match.group(0))[:180],
            )
        )
    if people:
        return _merge_people(people)
    for match in NAME_CANDIDATE_PATTERN.finditer(text or ""):
        name = clean_text(match.group(1))
        if not _usable_person_name(name):
            continue
        people.append(
            ResolvedPerson(
                name=name,
                role="candidate",
                confidence=base_confidence,
                source_url=source_url,
                source_snippet=name,
            )
        )
    return _merge_people(people)[:5]


def extract_people_from_html(html: str, *, source_url: str | None = None) -> list[ResolvedPerson]:
    text = HtmlTextExtractor().extract(html)
    return extract_people_from_text(text, source_url=source_url, base_confidence=0.55)


class HtmlTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = clean_text(data)
        if text:
            self.parts.append(text)

    def extract(self, html: str) -> str:
        self.feed(html)
        return " ".join(self.parts)


def fetch_text(url: str, *, user_agent: str, timeout_seconds: float = 20.0) -> str:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout_seconds) as response:
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "html" not in content_type and "text" not in content_type:
            raise ValueError(f"Unsupported content type: {content_type or 'unknown'}")
        return response.read().decode("utf-8", errors="replace")


def _should_fetch(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    blocked = ("instagram.com", "facebook.com", "twitter.com", "x.com", "tiktok.com", "youtube.com")
    return not any(host == domain or host.endswith("." + domain) for domain in blocked)


def _usable_person_name(name: str) -> bool:
    if not name or name in SKIP_NAME_PHRASES:
        return False
    if any(phrase.lower() in name.lower() for phrase in SKIP_NAME_PHRASES):
        return False
    tokens = name.split()
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    if any(token.lower() in SKIP_NAME_TOKENS for token in tokens):
        return False
    if any(token.lower() in {"inc", "llc", "camp", "collective", "crew", "team", "studio"} for token in tokens):
        return False
    if "." in name and not re.search(r"\b[A-Z]\.", name):
        return False
    if re.search(r"\d|www\.|http|@", name):
        return False
    # Require mostly Title Case tokens.
    if sum(1 for token in tokens if token[:1].isupper()) < len(tokens):
        return False
    return True


def _merge_people(people: list[ResolvedPerson], exclude: set[str | None] | None = None) -> list[ResolvedPerson]:
    exclude_names = {clean_text(value).lower() for value in (exclude or set()) if value}
    best: dict[str, ResolvedPerson] = {}
    for person in people:
        key = person.name.lower()
        if key in exclude_names:
            continue
        existing = best.get(key)
        if existing is None or person.confidence > existing.confidence:
            best[key] = person
    return sorted(best.values(), key=lambda item: (-item.confidence, item.name.lower()))


def _alias_is_url_subject(alias: str, url: str) -> bool:
    """True only when the URL slug is essentially the alias itself (not Firefly_(DC_Comics))."""
    path = urlsplit(url).path or ""
    slug = path.rstrip("/").split("/")[-1]
    if not slug or "(" in slug or ")" in slug:
        return False
    slug_norm = re.sub(r"[_\-]+", " ", slug).strip().lower()
    alias_norm = normalize_alias(alias)
    if not aliases_match(alias_norm, slug_norm):
        return False
    # Reject longer titles where alias is only a prefix word ("Firefly Arts Collective").
    return slug_norm == alias_norm or re.sub(r"[^a-z0-9]", "", slug_norm) == re.sub(
        r"[^a-z0-9]", "", alias_norm
    )


def _looks_like_entertainment_noise_url(url: str) -> bool:
    lowered = (url or "").lower()
    noise_markers = (
        "comics",
        "fandom.com",
        "comicvine",
        "deviantart.com",
        "gamespot.com",
        "honkai",
        "batman",
        "characters/firefly",
        "tv_series",
        "lyrics",
        "genius.com",
        "songfacts",
    )
    return any(marker in lowered for marker in noise_markers)


def _status_for(result: IdentityResult, classification: CreditClassification) -> str:
    if result.playa_name and result.legal_name and classification.credit_type == "person_with_playa_name":
        return "resolved"
    if (
        classification.credit_type in {"alias_or_unknown", "alias_pair"}
        and result.playa_name
        and result.legal_name
        and any(person.role == "alias_linked" and person.confidence >= 0.9 for person in result.resolved_people)
    ):
        return "resolved"
    if classification.credit_type in {"person", "multi_person"} and result.named_people:
        return "resolved"
    high = [
        person
        for person in result.resolved_people
        if person.confidence >= 0.75 and _looks_like_legal_name(person.name)
    ]
    if classification.needs_identity_search and high:
        return "partial"
    if classification.needs_identity_search:
        return "needs_review"
    if result.legal_name:
        return "resolved"
    return "unresolved"


def quote(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    if " " in text:
        return f'"{text}"'
    return text
