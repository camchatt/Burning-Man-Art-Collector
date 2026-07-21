"""Collection-card discovery and detail URL scoring."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

from burning_man_scraper.sources.artist_website.images import extract_image_from_tag
from burning_man_scraper.sources.artist_website.models import (
    ArtworkCandidate,
    ArtworkEvidence,
    ImageEvidence,
)

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
PRICE_RE = re.compile(r"\$\s*\d[\d,]*(?:\.\d{2})?")
DIMENSION_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ft\.?|feet|in\.?|inches|cm|mm|[”\"']|â€)?\s*"
    r"(?:x|×|Ã—|by)\s*\d+(?:\.\d+)?(?:\s*(?:ft\.?|feet|in\.?|inches|cm|mm|[”\"']|â€))?"
    r"|"
    r"\b\d+(?:\.\d+)?\s*(?:x|×|Ã—)\s*\d+(?:\.\d+)?\s*(?:ft\.?|feet|in\.?|inches|cm|mm|[”\"']|â€)?",
    re.I,
)
DETAIL_PATH_RE = re.compile(
    r"/(?:artworks?|products?|p|store(?:-\d+)?/p|shop(?:/[^/]+)?/products?)/[^/?#]+",
    re.I,
)
NEGATIVE_PATH_RE = re.compile(
    r"/(?:cart|checkout|account|login|sign-in|privacy|contact|press|biography|bio|cv|"
    r"about|newsletter|mailing-list|terms|cookie|search|tag|tags|filter|filters)(?:/|$)",
    re.I,
)
CATEGORY_PATH_RE = re.compile(r"/(?:categor(?:y|ies)|collections?|tags?|filters?)/", re.I)
NAV_TITLE_RE = re.compile(
    r"^(?:gallery|shop|artwork|artworks|all|menu|contact|cart|enquire|inquire|"
    r"view details|view more details|quick view|sold|home|about|news|press|work|"
    r"works|projects|portfolio|commissions|viewing room|mailing list|cookie policy|"
    r"skip to main content)$",
    re.I,
)
VIEW_DETAILS_RE = re.compile(r"\b(?:view(?:\s+more)?\s+details|quick view|enquire|inquire)\b", re.I)
PRINT_RE = re.compile(r"\b(?:signed\s+print|limited\s+edition|edition|print)\b", re.I)
FILENAME_TITLE_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|tiff?|svg)(?:\b|$)", re.I)
CARD_CLASS_RE = re.compile(
    r"(?:gallery|artwork|product|portfolio|grid-item|card|slide|item|index-item|"
    r"summary-item|project)",
    re.I,
)
STRUCTURAL_CARD_SELECTORS = (
    "article.index-item",
    ".index-item",
    ".portfolio-grid-item",
    ".summary-item",
    "li.artwork-item",
    "li.grid-item",
    ".sqs-gallery-design-grid-slide",
)
TRACKING_QUERY = frozenset()  # deprecated: use text_normalize.ARTIST_SITE_TRACKING_PARAMETERS


def clean_text(value: str | None) -> str:
    from burning_man_scraper.sources.artist_website.text_normalize import normalize_display_text

    return normalize_display_text(value)


def normalize_detail_url(url: str, base_url: str | None = None) -> str:
    """Unified artist-website URL identity (same policy as ingest.normalize_url)."""
    from burning_man_scraper.sources.artist_website.text_normalize import normalize_identity_url

    return normalize_identity_url(url, base_url)


def score_detail_url(url: str, *, anchor_text: str = "", card_text: str = "") -> int:
    parsed = urlparse(url)
    if parsed.scheme in {"mailto", "tel", "javascript"} or not parsed.scheme.startswith("http"):
        if parsed.scheme or url.startswith(("#", "mailto:", "tel:", "javascript:")):
            return -100
    path = parsed.path or "/"
    score = 0
    if NEGATIVE_PATH_RE.search(path):
        return -50
    if CATEGORY_PATH_RE.search(path) and not DETAIL_PATH_RE.search(path):
        score -= 4
    if DETAIL_PATH_RE.search(path):
        score += 5
    if re.search(r"/(?:artworks?|products?)/[^/]+-\d", path, re.I) or re.search(
        r"/\d+-[a-z0-9-]+", path, re.I
    ):
        score += 2
    if re.search(r"/p/[a-z0-9-]+", path, re.I):
        score += 4
    if VIEW_DETAILS_RE.search(anchor_text):
        score += 2
    if YEAR_RE.search(card_text) or DIMENSION_RE.search(card_text) or PRICE_RE.search(card_text):
        score += 2
    if PRINT_RE.search(card_text):
        score += 1
    if re.search(r"\b(store|shop|product)\b", path, re.I):
        score += 0  # commerce OK for product records
    return score


def is_nav_title(title: str, artist_name: str = "") -> bool:
    cleaned = clean_text(title)
    if not cleaned:
        return True
    if NAV_TITLE_RE.match(cleaned):
        return True
    if artist_name and cleaned.casefold() == artist_name.casefold():
        return True
    return False


def strip_site_title_suffix(title: str, artist_name: str = "") -> str:
    """Strip trailing '— Artist Name' / '| Artist' branding from document/OG titles."""
    title = clean_text(title)
    if not title or not artist_name:
        return title
    pattern = rf"^(.*?)\s*[—–|]\s*{re.escape(artist_name.strip())}\s*$"
    match = re.match(pattern, title, re.I)
    if match:
        return clean_text(match.group(1))
    return title


def title_from_card_text(text: str, artist_name: str = "") -> tuple[str, list[str]]:
    """Return title and review flags from card/alt/caption text."""
    flags: list[str] = []
    text = strip_site_title_suffix(clean_text(text), artist_name)
    text = re.sub(r"^\s*view\s+fullsize\s*", "", text, flags=re.I)
    text = PRICE_RE.sub("", text)
    text = re.sub(r"\bSold\b", "", text, flags=re.I)
    if FILENAME_TITLE_RE.search(text):
        return "", flags

    quoted = re.match(r'^[“"\'â€œ]([^”"\'â€]{2,120})[”"\'â€]', text)
    if quoted:
        title = clean_text(quoted.group(1).rstrip(".,"))
        if not is_nav_title(title, artist_name) and not FILENAME_TITLE_RE.search(title):
            return title, flags

    # Artist, Title, Year
    if artist_name:
        artist_prefix = re.escape(artist_name)
        match = re.match(
            rf"^{artist_prefix}\s*[,:\-—–â€”]\s*(.+)$",
            text,
            re.I,
        )
        if match:
            text = clean_text(match.group(1))

    before_year = YEAR_RE.split(text, maxsplit=1)[0].strip(" ,.-")
    before_meta = re.split(
        r"\b(?:signed\s+print|limited\s+edition|collaboration with|acrylic|spray paint|"
        r"oil on|on canvas|enquire|inquire|view more details|"
        r"\d+(?:\.\d+)?\s*(?:x|×)\s*\d+|"
        r"\d+(?:\.\d+)?\s*(?:ft|feet|in|inches|cm))\b",
        before_year,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" ,.-\"'“”")
    if 2 <= len(before_meta) <= 120 and len(before_meta.split()) <= 14:
        if not is_nav_title(before_meta, artist_name) and not FILENAME_TITLE_RE.search(before_meta):
            return before_meta, flags
    return "", flags


def _class_blob(tag: Tag) -> str:
    classes = tag.get("class") or []
    if isinstance(classes, str):
        return classes
    return " ".join(classes)


def _is_card_container(tag: Tag) -> bool:
    if tag.name in {"figure", "article", "li"}:
        return True
    return bool(CARD_CLASS_RE.search(_class_blob(tag)))


def _local_text(tag: Tag) -> str:
    parts: list[str] = []
    for child in tag.descendants:
        if isinstance(child, NavigableString) and child.parent and child.parent.name not in {
            "script",
            "style",
            "noscript",
        }:
            text = clean_text(str(child))
            if text:
                parts.append(text)
    return clean_text(" ".join(parts))


def _smallest_card_ancestor(anchor: Tag) -> Tag:
    current: Tag | None = anchor
    best = anchor
    for _ in range(8):
        if current is None:
            break
        if _is_card_container(current):
            best = current
            # Prefer the nearest card-like ancestor
            return current
        parent = current.parent
        if not isinstance(parent, Tag):
            break
        # Stop at major landmarks
        if parent.name in {"nav", "header", "footer", "body", "html"}:
            break
        current = parent
        best = current
    return best


def _card_heading_text(card: Tag, artist_name: str = "") -> str:
    for tag in card.find_all(["h1", "h2", "h3", "h4"]):
        text = clean_text(tag.get_text(" ", strip=True))
        text = strip_site_title_suffix(text, artist_name)
        if text and not is_nav_title(text, artist_name) and not FILENAME_TITLE_RE.search(text):
            return text
    for selector in (
        ".index-item-title",
        ".summary-title",
        ".portfolio-title",
        ".product-title",
        ".artwork-title",
    ):
        tag = card.select_one(selector)
        if not tag:
            continue
        text = clean_text(tag.get_text(" ", strip=True))
        text = strip_site_title_suffix(text, artist_name)
        if text and not is_nav_title(text, artist_name) and not FILENAME_TITLE_RE.search(text):
            return text
    return ""


def score_structural_card(
    card: Tag,
    detail: str,
    *,
    page_url: str,
    sibling_structural_count: int = 0,
) -> int:
    """Score repeated portfolio/grid card structure independent of lexical URL tokens."""
    from burning_man_scraper.sources.artist_website.ingest import is_internal_url, normalize_url

    if card.name in {"nav", "header", "footer"}:
        return -50
    score = 0
    class_blob = _class_blob(card)
    if card.name == "article" or re.search(
        r"index-item|portfolio|summary-item|grid-item|artwork-item",
        class_blob,
        re.I,
    ):
        score += 2

    image_link = False
    title_link = False
    matching_links = 0
    for anchor in card.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = normalize_url(href, page_url)
        if not is_internal_url(absolute, page_url):
            continue
        if normalize_detail_url(absolute, page_url) != detail:
            continue
        matching_links += 1
        if anchor.find("img"):
            image_link = True
        anchor_text = clean_text(anchor.get_text(" ", strip=True))
        if anchor_text and not is_nav_title(anchor_text):
            title_link = True
        if anchor.find_parent(["h1", "h2", "h3", "h4"]):
            title_link = True

    if image_link and title_link:
        score += 3
    elif matching_links >= 1 and (image_link or title_link):
        score += 1

    has_heading = bool(_card_heading_text(card))
    has_image = bool(card.find("img"))
    if has_heading and has_image:
        score += 2

    if sibling_structural_count >= 3:
        score += 2
    elif sibling_structural_count >= 2:
        score += 1

    collection = normalize_url(page_url)
    if detail and detail != collection:
        score += 1
    return score


def _structural_detail_from_card(card: Tag, page_url: str) -> str:
    """Pick the shared internal destination for an image+title portfolio card."""
    from burning_man_scraper.sources.artist_website.ingest import is_internal_url, normalize_url

    counts: dict[str, int] = {}
    image_urls: set[str] = set()
    title_urls: set[str] = set()
    collection = normalize_url(page_url)

    for anchor in card.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = normalize_url(href, page_url)
        if not is_internal_url(absolute, page_url):
            continue
        detail = normalize_detail_url(absolute, page_url)
        if not detail or detail == collection:
            continue
        if score_detail_url(detail) < 0:
            continue
        path = urlparse(detail).path or "/"
        if NEGATIVE_PATH_RE.search(path):
            continue
        counts[detail] = counts.get(detail, 0) + 1
        if anchor.find("img"):
            image_urls.add(detail)
        text = clean_text(anchor.get_text(" ", strip=True))
        if text and not is_nav_title(text):
            title_urls.add(detail)
        if anchor.find_parent(["h1", "h2", "h3", "h4"]):
            title_urls.add(detail)

    shared = image_urls & title_urls
    if shared:
        return max(shared, key=lambda url: (counts.get(url, 0), -len(url)))
    if counts:
        return max(counts, key=lambda url: (counts[url], -len(url)))
    return ""


def _iter_structural_cards(soup: BeautifulSoup) -> list[Tag]:
    seen: set[int] = set()
    cards: list[Tag] = []
    for selector in STRUCTURAL_CARD_SELECTORS:
        for card in soup.select(selector):
            ident = id(card)
            if ident in seen:
                continue
            if card.find_parent(["nav", "header", "footer"]):
                continue
            seen.add(ident)
            cards.append(card)
    return cards


def _merge_candidate_into(
    grouped: dict[str, ArtworkCandidate],
    candidate: ArtworkCandidate,
) -> None:
    detail = candidate.detail_url
    if not detail:
        return
    existing = grouped.get(detail)
    if not existing:
        grouped[detail] = candidate
        return
    if len(candidate.excerpt) > len(existing.excerpt):
        existing.excerpt = candidate.excerpt
    if candidate.images:
        from burning_man_scraper.sources.artist_website.images import prefer_artwork_images

        existing.images = prefer_artwork_images(
            [*existing.images, *candidate.images],
            artist_name="",
        )
    # Prefer explicit heading titles over alt/slug inferences
    existing_inferred = any(
        flag.startswith("title_inferred") for flag in existing.review_flags
    )
    candidate_inferred = any(
        flag.startswith("title_inferred") for flag in candidate.review_flags
    )
    if candidate.title and (not existing.title or (existing_inferred and not candidate_inferred)):
        existing.title = candidate.title
        existing.review_flags = [
            flag for flag in existing.review_flags if not flag.startswith("title_inferred")
        ]
        for flag in candidate.review_flags:
            if flag not in existing.review_flags:
                existing.review_flags.append(flag)
    for key, value in candidate.metadata.items():
        existing.metadata.setdefault(key, value)
    for flag in candidate.review_flags:
        if flag not in existing.review_flags:
            existing.review_flags.append(flag)


def discover_collection_candidates(page, artist_name: str = "") -> list[ArtworkCandidate]:
    from burning_man_scraper.sources.artist_website.ingest import is_internal_url, normalize_url
    from burning_man_scraper.sources.artist_website.images import prefer_artwork_images

    soup = BeautifulSoup(page.html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    grouped: dict[str, ArtworkCandidate] = {}
    bare_candidates: list[ArtworkCandidate] = []
    collection_url = normalize_url(page.url)

    structural_cards = _iter_structural_cards(soup)
    structural_details: list[str] = []
    for card in structural_cards:
        detail = _structural_detail_from_card(card, page.url)
        if detail:
            structural_details.append(detail)
    sibling_count = len(set(structural_details))

    # Pass 0: repeated structural portfolio/index cards (arbitrary slug URLs OK)
    for card in structural_cards:
        detail = _structural_detail_from_card(card, page.url)
        if not detail:
            continue
        structural = score_structural_card(
            card,
            detail,
            page_url=page.url,
            sibling_structural_count=sibling_count,
        )
        if structural < 4:
            continue

        images = prefer_artwork_images(
            [
                evidence
                for image in card.find_all("img")
                if (evidence := extract_image_from_tag(image, page.url))
            ],
            artist_name=artist_name,
        )
        heading = _card_heading_text(card, artist_name)
        card_text = _local_text(card)
        flags: list[str] = []
        title = ""
        if heading:
            title = heading
        else:
            alt_text = next((img.alt for img in images if img.alt), "")
            title, flags = title_from_card_text(card_text or alt_text, artist_name)
            if not title and alt_text:
                title, flags = title_from_card_text(alt_text, artist_name)
                if title:
                    flags.append("title_inferred_from_alt")
        if not title or is_nav_title(title, artist_name):
            continue

        year = ""
        year_match = YEAR_RE.search(card_text)
        if year_match:
            year = year_match.group(0)
        metadata = {}
        dims = DIMENSION_RE.search(card_text)
        if dims:
            metadata["dimensions"] = clean_text(dims.group(0))
        price = PRICE_RE.search(card_text)
        if price:
            metadata["price"] = price.group(0)

        _merge_candidate_into(
            grouped,
            ArtworkCandidate(
                title=title,
                year=year,
                detail_url=detail,
                collection_url=collection_url,
                images=images,
                metadata=metadata,
                evidence=[
                    ArtworkEvidence(
                        field="title",
                        value=title,
                        source_url=page.url,
                        source_kind="collection_card",
                        confidence=0.75,
                        selector_or_signal="structural_card",
                    )
                ],
                confidence=0.6,
                review_flags=flags,
                excerpt=clean_text(card_text)[:700] or title,
                source_granularity="Gallery caption",
                page_text=page.text,
                page_url=collection_url,
            ),
        )

    # Pass 1: group by internal detail URL using lexical + structural gates
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = normalize_url(href, page.url)
        if not is_internal_url(absolute, page.url):
            continue
        if absolute == collection_url:
            continue
        detail = normalize_detail_url(absolute, page.url)
        if detail in grouped:
            continue
        card = _smallest_card_ancestor(anchor)
        if card.name in {"nav", "header", "footer"}:
            continue
        card_text = _local_text(card)
        anchor_text = clean_text(anchor.get_text(" ", strip=True))
        if is_nav_title(anchor_text, artist_name) and not card.find("img"):
            continue
        url_score = score_detail_url(detail, anchor_text=anchor_text, card_text=card_text)
        structural = score_structural_card(
            card,
            detail,
            page_url=page.url,
            sibling_structural_count=sibling_count,
        )
        has_meta = bool(
            YEAR_RE.search(card_text)
            or DIMENSION_RE.search(card_text)
            or PRICE_RE.search(card_text)
        )
        if url_score < 2 and structural < 4 and not has_meta:
            if not DETAIL_PATH_RE.search(urlparse(detail).path or "/"):
                continue

        images = prefer_artwork_images(
            [
                evidence
                for image in card.find_all("img")
                if (evidence := extract_image_from_tag(image, page.url))
            ],
            artist_name=artist_name,
        )

        heading = _card_heading_text(card, artist_name)
        alt_text = next((img.alt for img in images if img.alt), "")
        flags = []
        if heading:
            title = heading
        else:
            title_source = card_text or anchor_text or alt_text
            title, flags = title_from_card_text(title_source, artist_name)
            if not title and alt_text:
                title, flags = title_from_card_text(alt_text, artist_name)
                if title:
                    flags.append("title_inferred_from_alt")
        if not title:
            slug = (urlparse(detail).path or "/").rstrip("/").split("/")[-1]
            slug_title = clean_text(re.sub(r"^\d+-", "", slug).replace("-", " "))
            if artist_name:
                artist_slug = re.sub(r"[^a-z0-9]+", " ", artist_name.casefold()).strip()
                slug_fold = slug_title.casefold()
                if slug_fold.startswith(artist_slug):
                    slug_title = clean_text(slug_title[len(artist_name) :])
            slug_title = YEAR_RE.sub("", slug_title).strip(" -")
            if slug_title and not is_nav_title(slug_title, artist_name):
                from burning_man_scraper.sources.artist_website.text_normalize import (
                    title_from_slug_words,
                )

                # Do not invent Title Case from slugs; prefer visible titles when present.
                title = title_from_slug_words(slug_title)
                flags.append("title_inferred_from_slug")

        if is_nav_title(title, artist_name):
            continue
        if VIEW_DETAILS_RE.fullmatch(title or ""):
            continue

        year = ""
        year_match = YEAR_RE.search(heading or card_text or alt_text)
        if year_match:
            year = year_match.group(0)
        dims = ""
        from burning_man_scraper.sources.artist_website.text_normalize import (
            normalize_dimension_text,
        )

        dim_match = DIMENSION_RE.search(normalize_dimension_text(card_text)) or DIMENSION_RE.search(
            card_text
        )
        if dim_match:
            dims = clean_text(dim_match.group(0))
        price = ""
        price_match = PRICE_RE.search(card_text)
        if price_match:
            price = price_match.group(0)
        availability = "sold" if re.search(r"\bSold\b", card_text) else ""

        metadata = {
            k: v
            for k, v in {
                "dimensions": dims,
                "price": price,
                "availability": availability,
            }.items()
            if v
        }
        if PRINT_RE.search(card_text):
            metadata["edition_kind"] = "print"
            flags.append("commerce_edition")

        _merge_candidate_into(
            grouped,
            ArtworkCandidate(
                title=title,
                year=year,
                detail_url=detail,
                collection_url=collection_url,
                images=images,
                metadata=metadata,
                evidence=[
                    ArtworkEvidence(
                        field="title",
                        value=title,
                        source_url=page.url,
                        source_kind="collection_card"
                        if "title_inferred_from_slug" not in flags
                        else "url_slug",
                        confidence=0.6 if "title_inferred_from_alt" in flags else (
                            0.35 if "title_inferred_from_slug" in flags else 0.7
                        ),
                        selector_or_signal="card",
                    )
                ],
                confidence=0.55,
                review_flags=flags,
                excerpt=clean_text(card_text)[:700] or title,
                source_granularity="Gallery caption",
                page_text=page.text,
                page_url=collection_url,
            ),
        )

    # Pass 2: caption-only figures without detail links (murals)
    for block in soup.select(
        "figure, .gallery-item, .image-slide, .sqs-gallery-design-grid-slide, .portfolio-grid-item"
    ):
        anchors = [
            a
            for a in block.find_all("a", href=True)
            if is_internal_url(normalize_url(a.get("href", ""), page.url), page.url)
            and normalize_url(a.get("href", ""), page.url) != collection_url
        ]
        if anchors:
            continue
        text = _local_text(block)
        images = prefer_artwork_images(
            [
                evidence
                for image in block.find_all("img")
                if (evidence := extract_image_from_tag(image, page.url))
            ],
            artist_name=artist_name,
        )
        alt = next((img.alt for img in images if img.alt), "")
        title, flags = title_from_card_text(text or alt, artist_name)
        if not title:
            continue
        year_match = YEAR_RE.search(text or alt)
        dims_match = DIMENSION_RE.search(text)
        bare_candidates.append(
            ArtworkCandidate(
                title=title,
                year=year_match.group(0) if year_match else "",
                detail_url="",
                collection_url=collection_url,
                images=images,
                metadata={"dimensions": clean_text(dims_match.group(0))} if dims_match else {},
                evidence=[
                    ArtworkEvidence(
                        field="title",
                        value=title,
                        source_url=page.url,
                        source_kind="figure_caption",
                        confidence=0.7,
                    )
                ],
                confidence=0.5,
                review_flags=[*flags, "collection_only", "missing_detail_page"],
                excerpt=clean_text(text or alt)[:700],
                source_granularity="Gallery caption",
                page_text=page.text,
                page_url=collection_url,
            )
        )

    # Standalone strong alts without cards already counted
    seen_titles = {c.title.casefold() for c in grouped.values()} | {
        c.title.casefold() for c in bare_candidates
    }
    for image in soup.find_all("img"):
        evidence = extract_image_from_tag(image, page.url)
        if not evidence or not evidence.alt:
            continue
        preferred = prefer_artwork_images([evidence], artist_name=artist_name)
        if not preferred:
            continue
        evidence = preferred[0]
        if not (YEAR_RE.search(evidence.alt) and len(evidence.alt.replace(",", " ").split()) >= 3):
            continue
        title, flags = title_from_card_text(evidence.alt, artist_name)
        if not title or title.casefold() in seen_titles:
            continue
        if any(evidence.url == img.url for c in grouped.values() for img in c.images):
            continue
        year_match = YEAR_RE.search(evidence.alt)
        bare_candidates.append(
            ArtworkCandidate(
                title=title,
                year=year_match.group(0) if year_match else "",
                detail_url="",
                collection_url=collection_url,
                images=[evidence],
                metadata={},
                evidence=[
                    ArtworkEvidence(
                        field="title",
                        value=title,
                        source_url=page.url,
                        source_kind="image_alt",
                        confidence=0.55,
                    )
                ],
                confidence=0.45,
                review_flags=[*flags, "title_inferred_from_alt", "collection_only"],
                excerpt=evidence.alt[:700],
                source_granularity="Gallery caption",
                page_text=page.text,
                page_url=collection_url,
            )
        )
        seen_titles.add(title.casefold())

    return [*grouped.values(), *bare_candidates]
