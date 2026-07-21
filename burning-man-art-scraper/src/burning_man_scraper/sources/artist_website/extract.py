"""Field extraction with precedence for artwork detail and collection pages."""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from burning_man_scraper.sources.artist_website.discover import (
    DIMENSION_RE,
    PRICE_RE,
    YEAR_RE,
    clean_text,
    is_nav_title,
    normalize_detail_url,
    strip_site_title_suffix,
    title_from_card_text,
)
from burning_man_scraper.sources.artist_website.images import (
    extract_images_from_soup,
    extract_meta_images,
    prefer_artwork_images,
)
from burning_man_scraper.sources.artist_website.models import (
    ArtworkCandidate,
    ArtworkEvidence,
    ImageEvidence,
)


def _add_evidence(
    candidate: ArtworkCandidate,
    field: str,
    value: str,
    *,
    source_url: str,
    source_kind: str,
    confidence: float,
    signal: str = "",
) -> None:
    value = clean_text(value)
    if not value:
        return
    candidate.evidence.append(
        ArtworkEvidence(
            field=field,
            value=value,
            source_url=source_url,
            source_kind=source_kind,
            confidence=confidence,
            selector_or_signal=signal,
        )
    )


def _set_if_better(
    candidate: ArtworkCandidate,
    field: str,
    value: str,
    *,
    source_url: str,
    source_kind: str,
    confidence: float,
    signal: str = "",
    into_metadata: bool = False,
) -> None:
    value = clean_text(value)
    if not value:
        return
    current_conf = 0.0
    for item in candidate.evidence:
        if item.field == field:
            current_conf = max(current_conf, item.confidence)
    if confidence < current_conf:
        if into_metadata:
            return
        # Conflict when values disagree
        existing = getattr(candidate, field, "") if hasattr(candidate, field) else candidate.metadata.get(field, "")
        if existing and existing.casefold() != value.casefold():
            flag = f"conflicting_{field}"
            if flag not in candidate.review_flags:
                candidate.review_flags.append(flag)
        return
    if into_metadata:
        previous = candidate.metadata.get(field, "")
        if previous and previous.casefold() != value.casefold() and confidence == current_conf:
            flag = f"conflicting_{field}"
            if flag not in candidate.review_flags:
                candidate.review_flags.append(flag)
        candidate.metadata[field] = value
    else:
        previous = getattr(candidate, field, "")
        if previous and previous.casefold() != value.casefold() and confidence == current_conf:
            flag = f"conflicting_{field}"
            if flag not in candidate.review_flags:
                candidate.review_flags.append(flag)
        setattr(candidate, field, value)
    _add_evidence(
        candidate,
        field,
        value,
        source_url=source_url,
        source_kind=source_kind,
        confidence=confidence,
        signal=signal,
    )


def _parse_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            blocks.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                blocks.extend(item for item in data["@graph"] if isinstance(item, dict))
            else:
                blocks.append(data)
    return blocks


def _json_ld_artwork(blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for block in blocks:
        types = block.get("@type", "")
        type_list = types if isinstance(types, list) else [types]
        type_fold = {str(item).casefold() for item in type_list}
        if type_fold & {"product", "visualartwork", "painting", "sculpture", "creativework"}:
            return block
    return blocks[0] if len(blocks) == 1 else None


def extract_detail_candidate(page, artist_name: str = "") -> ArtworkCandidate:
    """Extract an authoritative artwork candidate from a detail page."""
    from burning_man_scraper.sources.artist_website.ingest import normalize_url

    soup = BeautifulSoup(page.html, "html.parser")
    detail_url = normalize_detail_url(page.url)
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        canonical_url = normalize_detail_url(canonical.get("href"), page.url)
        from burning_man_scraper.sources.artist_website.ingest import is_internal_url

        if is_internal_url(canonical_url, page.url):
            detail_url = canonical_url

    candidate = ArtworkCandidate(
        detail_url=detail_url,
        page_url=detail_url,
        collection_url="",
        source_granularity="Individual project page",
        page_text=page.text,
        confidence=0.8,
        excerpt="",
    )

    # 1) Explicit detail container fields
    title_el = soup.select_one(
        ".subtitle .title, .title_and_year .title, .title_and_year_title, "
        "[itemprop='name'], .artwork-title, .product-title, h1.title"
    )
    year_el = soup.select_one(".subtitle .year, .title_and_year .year, .title_and_year_year, [itemprop='dateCreated']")
    medium_el = soup.select_one(".medium, [class*='medium'], [itemprop='material'], .product-materials")
    dims_el = soup.select_one(".dimensions, [class*='dimension'], [itemprop='size']")
    series_el = soup.select_one(".series, [class*='series']")
    signed_el = soup.select_one(".signed_and_dated, [class*='signed']")
    stock_el = soup.select_one(".stock_number, [class*='stock'], [class*='inventory'], .sku")
    price_el = soup.select_one(".price, [itemprop='price'], .product-price")

    if title_el:
        title = clean_text(title_el.get_text(" ", strip=True))
        if not is_nav_title(title, artist_name):
            _set_if_better(
                candidate,
                "title",
                title,
                source_url=page.url,
                source_kind="detail_container",
                confidence=0.95,
                signal="subtitle.title",
            )
    if year_el:
        year = clean_text(year_el.get_text(" ", strip=True))
        year_match = YEAR_RE.search(year)
        if year_match:
            _set_if_better(
                candidate,
                "year",
                year_match.group(0),
                source_url=page.url,
                source_kind="detail_container",
                confidence=0.95,
                signal="subtitle.year",
            )
    if medium_el:
        _set_if_better(
            candidate,
            "medium",
            medium_el.get_text(" ", strip=True),
            source_url=page.url,
            source_kind="detail_container",
            confidence=0.95,
            signal="medium",
            into_metadata=True,
        )
    if dims_el:
        _set_if_better(
            candidate,
            "dimensions",
            dims_el.get_text(" ", strip=True),
            source_url=page.url,
            source_kind="detail_container",
            confidence=0.95,
            signal="dimensions",
            into_metadata=True,
        )
    if series_el:
        series_text = clean_text(series_el.get_text(" ", strip=True))
        series_text = re.sub(r"^series:\s*", "", series_text, flags=re.I)
        _set_if_better(
            candidate,
            "series",
            series_text,
            source_url=page.url,
            source_kind="detail_container",
            confidence=0.9,
            signal="series",
            into_metadata=True,
        )
    if signed_el:
        _set_if_better(
            candidate,
            "signature",
            signed_el.get_text(" ", strip=True),
            source_url=page.url,
            source_kind="detail_container",
            confidence=0.9,
            signal="signed",
            into_metadata=True,
        )
    if stock_el:
        _set_if_better(
            candidate,
            "inventory",
            stock_el.get_text(" ", strip=True),
            source_url=page.url,
            source_kind="detail_container",
            confidence=0.9,
            signal="stock",
            into_metadata=True,
        )
    if price_el:
        price_text = clean_text(price_el.get_text(" ", strip=True))
        if re.search(r"\bSold\b", price_text, re.I):
            _set_if_better(
                candidate,
                "availability",
                "sold",
                source_url=page.url,
                source_kind="detail_container",
                confidence=0.9,
                signal="sold",
                into_metadata=True,
            )
        else:
            price_match = PRICE_RE.search(price_text)
            if price_match:
                _set_if_better(
                    candidate,
                    "price",
                    price_match.group(0),
                    source_url=page.url,
                    source_kind="detail_container",
                    confidence=0.9,
                    signal="price",
                    into_metadata=True,
                )

    # 2) JSON-LD
    ld = _json_ld_artwork(_parse_json_ld(soup))
    if ld:
        name = ld.get("name") or ld.get("title")
        if isinstance(name, str):
            title, _flags = title_from_card_text(name, artist_name)
            if title:
                _set_if_better(
                    candidate,
                    "title",
                    title,
                    source_url=page.url,
                    source_kind="json_ld",
                    confidence=0.9,
                    signal="@type",
                )
        if isinstance(ld.get("dateCreated"), str):
            year_match = YEAR_RE.search(ld["dateCreated"])
            if year_match:
                _set_if_better(
                    candidate,
                    "year",
                    year_match.group(0),
                    source_url=page.url,
                    source_kind="json_ld",
                    confidence=0.85,
                )
        image = ld.get("image")
        image_url = ""
        if isinstance(image, str):
            image_url = image
        elif isinstance(image, dict):
            image_url = str(image.get("url") or "")
        elif isinstance(image, list) and image:
            first = image[0]
            image_url = first if isinstance(first, str) else str(first.get("url") or "")
        if image_url:
            candidate.images.append(
                ImageEvidence(url=normalize_url(image_url, page.url), alt="", source_kind="json_ld")
            )
        offers = ld.get("offers")
        if isinstance(offers, dict):
            if offers.get("price"):
                _set_if_better(
                    candidate,
                    "price",
                    f"${offers['price']}" if not str(offers["price"]).startswith("$") else str(offers["price"]),
                    source_url=page.url,
                    source_kind="json_ld",
                    confidence=0.85,
                    into_metadata=True,
                )
            availability = str(offers.get("availability") or "")
            if "sold" in availability.casefold() or "outofstock" in availability.casefold():
                _set_if_better(
                    candidate,
                    "availability",
                    "sold",
                    source_url=page.url,
                    source_kind="json_ld",
                    confidence=0.85,
                    into_metadata=True,
                )

    # 3) Visible H1 (preferred over OG for editorial project pages)
    h1 = strip_site_title_suffix(clean_text(page.h1), artist_name)
    if h1 and not is_nav_title(h1, artist_name):
        _set_if_better(
            candidate,
            "title",
            h1,
            source_url=page.url,
            source_kind="visible_heading",
            confidence=0.9,
            signal="h1",
        )

    # 4) Open Graph / Twitter (cleaned artist suffix)
    og_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "twitter:title"})
    if og_title and og_title.get("content"):
        raw_og = clean_text(og_title.get("content"))
        cleaned_og = strip_site_title_suffix(raw_og, artist_name)
        title, flags = title_from_card_text(cleaned_og, artist_name)
        title = title or cleaned_og
        if title and not is_nav_title(title, artist_name):
            _set_if_better(
                candidate,
                "title",
                title,
                source_url=page.url,
                source_kind="open_graph",
                confidence=0.8,
                signal="og:title",
            )
            for flag in flags:
                if flag not in candidate.review_flags:
                    candidate.review_flags.append(flag)
        # Never infer year from OG unless an explicit 19xx/20xx year token is present
        # in human-readable text (not CDN asset ids).
        year_match = YEAR_RE.search(cleaned_og)
        if year_match and not candidate.year:
            _set_if_better(
                candidate,
                "year",
                year_match.group(0),
                source_url=page.url,
                source_kind="open_graph",
                confidence=0.75,
            )
            candidate.review_flags.append("year_inferred")

    og_url = soup.find("meta", property="og:url")
    if og_url and og_url.get("content"):
        from burning_man_scraper.sources.artist_website.ingest import is_internal_url

        og_detail = normalize_detail_url(og_url.get("content"), page.url)
        if is_internal_url(og_detail, page.url):
            candidate.detail_url = og_detail
            candidate.page_url = og_detail

    og_desc = soup.find("meta", property="og:description") or soup.find(
        "meta", attrs={"name": "description"}
    )
    if og_desc and og_desc.get("content"):
        desc = clean_text(og_desc.get("content"))
        candidate.excerpt = desc[:700]
        if not candidate.metadata.get("medium"):
            medium_match = re.search(
                r"\b((?:acrylic|oil|watercolor|ink|mixed media)[^.]{0,40}(?:canvas|paper|panel|wood))",
                desc,
                re.I,
            )
            if medium_match:
                _set_if_better(
                    candidate,
                    "medium",
                    medium_match.group(1),
                    source_url=page.url,
                    source_kind="open_graph",
                    confidence=0.7,
                    into_metadata=True,
                )
        if not candidate.metadata.get("dimensions"):
            from burning_man_scraper.sources.artist_website.text_normalize import (
                normalize_dimension_text,
            )

            repaired = normalize_dimension_text(desc)
            dim_match = DIMENSION_RE.search(repaired) or DIMENSION_RE.search(desc)
            if dim_match:
                _set_if_better(
                    candidate,
                    "dimensions",
                    dim_match.group(0),
                    source_url=page.url,
                    source_kind="open_graph",
                    confidence=0.7,
                    into_metadata=True,
                )

    for image in prefer_artwork_images(
        extract_meta_images(soup, page.url),
        artist_name=artist_name,
    ):
        if image.url not in {img.url for img in candidate.images}:
            candidate.images.append(image)

    # 5) Gallery images (prefer non-logo artwork)
    page_images = prefer_artwork_images(
        extract_images_from_soup(soup, page.url),
        artist_name=artist_name,
    )
    for image in page_images:
        if image.url not in {img.url for img in candidate.images}:
            candidate.images.append(image)

    # 6) Image alt only when no visible/OG title
    if not candidate.title:
        for image in page_images:
            if not image.alt:
                continue
            title, flags = title_from_card_text(image.alt, artist_name)
            if title:
                _set_if_better(
                    candidate,
                    "title",
                    title,
                    source_url=page.url,
                    source_kind="image_alt",
                    confidence=0.55,
                )
                candidate.review_flags.append("title_inferred_from_alt")
                candidate.review_flags.extend(flags)
                year_match = YEAR_RE.search(image.alt)
                if year_match and not candidate.year:
                    _set_if_better(
                        candidate,
                        "year",
                        year_match.group(0),
                        source_url=page.url,
                        source_kind="image_alt",
                        confidence=0.5,
                    )
                    candidate.review_flags.append("year_inferred")
                break

    # 7) URL slug fallback — do not invent Title Case
    if not candidate.title:
        from burning_man_scraper.sources.artist_website.text_normalize import title_from_slug_words

        slug = (detail_url.rstrip("/").split("/")[-1] or "").replace("-", " ")
        slug = re.sub(r"^\d+\s*", "", slug)
        if artist_name:
            slug = re.sub(re.escape(artist_name), "", slug, flags=re.I)
        slug = YEAR_RE.sub("", slug).strip()
        title, flags = title_from_card_text(slug, artist_name)
        if not title and slug:
            title = title_from_slug_words(slug)
        if title:
            _set_if_better(
                candidate,
                "title",
                title,
                source_url=page.url,
                source_kind="url_slug",
                confidence=0.35,
            )
            candidate.review_flags.append("title_inferred_from_slug")
            candidate.review_flags.extend(flags)

    if not candidate.excerpt:
        bits = [
            candidate.title,
            candidate.year,
            candidate.metadata.get("medium", ""),
            candidate.metadata.get("dimensions", ""),
            candidate.metadata.get("series", ""),
        ]
        candidate.excerpt = clean_text(" ".join(bits))[:700]

    if not candidate.images:
        candidate.review_flags.append("missing_hero_image")
    if not candidate.title:
        candidate.review_flags.append("low_confidence_entity")
        candidate.confidence = 0.2
    return candidate
