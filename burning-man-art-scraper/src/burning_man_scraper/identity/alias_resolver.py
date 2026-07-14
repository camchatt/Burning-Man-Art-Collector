from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from burning_man_scraper.enrichment.models import SearchResult
from burning_man_scraper.identity.classifier import clean_text, ordered_unique, _looks_like_legal_name
from burning_man_scraper.identity.models import ResolvedPerson


# Keep legal-name capture case-sensitive; only keywords are case-insensitive.
ALIAS_LINK_PATTERNS = [
    # "Jane Doe aka PlayaName" / "Jane Doe, also known as PlayaName"
    re.compile(
        r"(?P<legal>\b[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2}\b)"
        r"\s*(?:,)?\s*(?i:a\.?k\.?a\.?|also known as|known as|goes by|playa name)\s+"
        r"['\"]?(?P<alias>[A-Za-z0-9][A-Za-z0-9'\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'\-]*){0,3})['\"]?"
    ),
    # "PlayaName aka Jane Doe" / "PlayaName real name Jane Doe"
    re.compile(
        r"['\"]?(?P<alias>[A-Za-z0-9][A-Za-z0-9'\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'\-]*){0,3})['\"]?"
        r"\s*(?i:a\.?k\.?a\.?|also known as|real name(?:\s+is)?|legal name(?:\s+is)?)\s+"
        r"(?P<legal>\b[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2}\b)"
    ),
    # "Jane Doe (PlayaName)"
    re.compile(
        r"(?P<legal>\b[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2}\b)\s*"
        r"[\(\[]\s*(?P<alias>[A-Za-z0-9][A-Za-z0-9'\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'\-]*){0,3})\s*[\)\]]"
    ),
    # "PlayaName (Jane Doe)"
    re.compile(
        r"(?P<alias>[A-Za-z0-9][A-Za-z0-9'\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'\-]*){0,3})\s*"
        r"[\(\[]\s*(?P<legal>\b[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2}\b)\s*[\)\]]"
    ),
    # "fnnch (born John Hundt)"
    re.compile(
        r"(?P<alias>[A-Za-z0-9][A-Za-z0-9'\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'\-]*){0,3})\s*"
        r"[\(\[]\s*born\s+(?P<legal>\b[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,2}\b)\s*[\)\]]",
        re.I,
    ),
]


ABOUT_PATH_CANDIDATES = (
    "/about",
    "/about/",
    "/about-me",
    "/about-me/",
    "/bio",
    "/bio/",
    "/artist",
    "/artist/",
    "/contact",
    "/contact/",
)

BLOCKED_HOSTS = (
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "youtube.com",
    "genius.com",
    "lyricstories.com",
    "songfacts.com",
    "fandom.com",
    "comicvine.gamespot.com",
    "deviantart.com",
    "neonmusic.co.uk",
)

NON_PERSON_NAME_TOKENS = {
    "music",
    "songline",
    "lyrics",
    "lyric",
    "video",
    "official",
    "translation",
    "traduzione",
    "terjemahan",
    "tafsiri",
    "oversettelse",
    "amazon",
    "gallery",
    "contact",
    "home",
    "submit",
    "expense",
    "actions",
}


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


def build_alias_queries(*, alias: str, project_title: str, year: int | str | None) -> list[str]:
    alias_q = quote(alias)
    title_q = quote(project_title)
    year_text = str(year or "")
    queries = [
        f"{alias_q} Burning Man real name",
        f'{alias_q} artist "real name"',
        f'{alias_q} aka OR "also known as" Burning Man',
        f"{alias_q} {title_q} artist",
        f"{alias_q} Burning Man {year_text}".strip(),
        f'{title_q} "{alias}" artist name',
    ]
    return ordered_unique([query for query in queries if alias_q in query or title_q in query])


def extract_alias_linked_names(
    text: str,
    *,
    alias: str,
    source_url: str | None = None,
    require_burn_context: bool = False,
) -> list[ResolvedPerson]:
    """Only accept names that are explicitly linked to the alias in the same phrase."""
    if not text or not alias:
        return []
    if require_burn_context and not has_burn_context(text):
        return []
    alias_norm = normalize_alias(alias)
    people: list[ResolvedPerson] = []
    for pattern in ALIAS_LINK_PATTERNS:
        for match in pattern.finditer(text):
            linked_alias = clean_text(match.group("alias"))
            legal = clean_text(match.group("legal"))
            if not aliases_match(alias_norm, linked_alias):
                continue
            if not _looks_like_legal_name(legal):
                continue
            if not looks_like_person_name(legal):
                continue
            if normalize_alias(legal) == alias_norm:
                continue
            people.append(
                ResolvedPerson(
                    name=legal,
                    role="alias_linked",
                    confidence=0.92,
                    source_url=source_url,
                    source_snippet=clean_text(match.group(0))[:200],
                )
            )
    return dedupe_people(people)


def looks_like_person_name(value: str) -> bool:
    text = clean_text(value)
    if not text:
        return False
    tokens = [re.sub(r"[^A-Za-z]", "", token).lower() for token in text.split()]
    tokens = [token for token in tokens if token]
    if not tokens or any(token in NON_PERSON_NAME_TOKENS for token in tokens):
        return False
    if len(tokens) > 4:
        return False
    return True


def has_burn_context(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "burning man",
            "black rock city",
            "playa",
            "brc",
            "burner",
        )
    )


def expand_artist_site_urls(seed_url: str) -> list[str]:
    parsed = urlsplit(seed_url)
    if not parsed.scheme or not parsed.netloc:
        return [seed_url]
    base = f"{parsed.scheme}://{parsed.netloc}"
    urls = [seed_url]
    for path in ABOUT_PATH_CANDIDATES:
        candidate = urljoin(base + "/", path.lstrip("/"))
        if candidate not in urls:
            urls.append(candidate)
    return urls


def gather_alias_evidence_pages(
    *,
    alias: str,
    artist_website: str | None,
    search_results: list[SearchResult],
    user_agent: str,
    fetch_search_pages: bool = True,
    max_pages: int = 6,
) -> list[tuple[str, str]]:
    pages: list[tuple[str, str]] = []
    seen: set[str] = set()

    seed_urls: list[str] = []
    if artist_website:
        seed_urls.extend(expand_artist_site_urls(artist_website))
    for item in search_results:
        if item.url and item.url not in seed_urls:
            seed_urls.append(item.url)

    for url in seed_urls:
        if len(pages) >= max_pages:
            break
        if url in seen or not _should_fetch(url):
            continue
        is_artist_site = bool(artist_website and urlsplit(url).netloc == urlsplit(artist_website).netloc)
        if not is_artist_site and not fetch_search_pages:
            continue
        seen.add(url)
        try:
            html = _fetch_text(url, user_agent=user_agent)
        except Exception:
            continue
        text = HtmlTextExtractor().extract(html)
        if is_artist_site or alias_mentioned(alias, text) or alias_mentioned(alias, url):
            pages.append((url, text))
    return pages


def alias_mentioned(alias: str, text: str) -> bool:
    if not alias or not text:
        return False
    return normalize_alias(alias) in normalize_alias(text)


def aliases_match(expected_norm: str, candidate: str) -> bool:
    candidate_norm = normalize_alias(candidate)
    if not expected_norm or not candidate_norm:
        return False
    if expected_norm == candidate_norm:
        return True
    compact_expected = re.sub(r"[^a-z0-9]", "", expected_norm)
    compact_candidate = re.sub(r"[^a-z0-9]", "", candidate_norm)
    return compact_expected == compact_candidate and len(compact_expected) >= 3


def normalize_alias(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def quote(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    if re.search(r"\s", text):
        return f'"{text}"'
    return text


def dedupe_people(people: list[ResolvedPerson]) -> list[ResolvedPerson]:
    best: dict[str, ResolvedPerson] = {}
    for person in people:
        key = person.name.lower()
        existing = best.get(key)
        if existing is None or person.confidence > existing.confidence:
            best[key] = person
    return sorted(best.values(), key=lambda item: (-item.confidence, item.name.lower()))


def _should_fetch(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    if any(host == domain or host.endswith("." + domain) for domain in BLOCKED_HOSTS):
        return False
    # Broad entertainment-wiki noise for common playa nicknames.
    if host.endswith(".fandom.com") or "gamespot.com" in host:
        return False
    return True


def _fetch_text(url: str, *, user_agent: str, timeout_seconds: float = 20.0) -> str:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout_seconds) as response:
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "html" not in content_type and "text" not in content_type:
            raise ValueError(f"Unsupported content type: {content_type or 'unknown'}")
        return response.read().decode("utf-8", errors="replace")
