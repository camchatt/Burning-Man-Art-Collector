"""Page classification for artist-owned websites."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from burning_man_scraper.sources.artist_website.discover import (
    DETAIL_PATH_RE,
    CATEGORY_PATH_RE,
    NEGATIVE_PATH_RE,
    STRUCTURAL_CARD_SELECTORS,
    discover_collection_candidates,
    score_detail_url,
    strip_site_title_suffix,
)
from burning_man_scraper.sources.artist_website.images import (
    extract_images_from_soup,
    prefer_artwork_images,
)
from burning_man_scraper.sources.artist_website.models import PageInterpretation, PageType
from burning_man_scraper.sources.artist_website.render import initial_render_reasons

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
HARD_UTILITY_RE = re.compile(
    r"\b(cart|checkout|login|sign[ -]?in|account|privacy|terms|contact|cv|biography|bio|"
    r"press|newsletter|mailing[ -]?list|cookie)\b",
    re.I,
)
NAV_ONLY_RE = re.compile(
    r"\b(menu|home|about|news|events?|lectures?|timeline|feed|tag|map)\b",
    re.I,
)
ARTWORK_PATH_RE = re.compile(
    r"/(?:artworks?|products?|store|shop|portfolio|gallery|murals?)/",
    re.I,
)


def _count_structural_cards(soup: BeautifulSoup) -> int:
    seen: set[int] = set()
    count = 0
    for selector in STRUCTURAL_CARD_SELECTORS:
        for card in soup.select(selector):
            ident = id(card)
            if ident in seen:
                continue
            if card.find_parent(["nav", "header", "footer"]):
                continue
            seen.add(ident)
            count += 1
    return count


def classify_page(page, artist_name: str = "") -> PageInterpretation:
    """Classify a fetched page and attach provisional collection candidates when useful."""
    from burning_man_scraper.sources.artist_website.ingest import clean_text, normalize_url

    path = urlparse(page.url).path or "/"
    title_h1 = f"{page.title} {page.h1}"
    scores: dict[str, int] = {
        "artwork_collection": 0,
        "artwork_detail": 0,
        "editorial_project_detail": 0,
        "navigation": 0,
        "commerce_utility": 0,
        "irrelevant": 0,
        "unknown": 0,
    }
    reasons: list[str] = []

    if HARD_UTILITY_RE.search(path) or HARD_UTILITY_RE.search(title_h1):
        if re.search(
            r"\b(cart|checkout|login|account|privacy|terms|cookie)\b",
            f"{path} {title_h1}",
            re.I,
        ):
            scores["commerce_utility"] = 10
            reasons.append("hard_utility_path_or_title")
        elif re.search(
            r"\b(contact|cv|biography|bio|press|newsletter|mailing)\b",
            f"{path} {title_h1}",
            re.I,
        ):
            scores["irrelevant"] = 8
            reasons.append("utility_or_bio_page")
    if NEGATIVE_PATH_RE.search(path) and not ARTWORK_PATH_RE.search(path):
        if "about" in path.casefold():
            scores["navigation"] += 4
            reasons.append("about_path")

    candidates = discover_collection_candidates(page, artist_name=artist_name)
    detail_urls = [c.detail_url for c in candidates if c.detail_url]
    unique_details = list(dict.fromkeys(detail_urls))
    bare_candidates = [c for c in candidates if c.title and not c.detail_url]
    soup = BeautifulSoup(page.html, "html.parser")
    images = prefer_artwork_images(
        extract_images_from_soup(soup, page.url),
        artist_name=artist_name,
    )
    structural_cards = _count_structural_cards(soup)

    if len(unique_details) >= 2:
        scores["artwork_collection"] += 4
        reasons.append(f"repeated_detail_urls:{len(unique_details)}")
    if structural_cards >= 3 and len(unique_details) >= 2:
        scores["artwork_collection"] += 4
        reasons.append(f"structural_cards:{structural_cards}")
    elif len(unique_details) == 1 and len(candidates) >= 1:
        scores["artwork_detail"] += 2
        reasons.append("single_detail_url_group")

    if len(bare_candidates) >= 2:
        scores["artwork_collection"] += 5
        reasons.append(f"repeated_caption_figures:{len(bare_candidates)}")
        # Caption grids are collections, not a single editorial project
        scores["editorial_project_detail"] = min(scores["editorial_project_detail"], 1)

    artwork_alts = [
        img.alt
        for img in images
        if img.alt and YEAR_RE.search(img.alt) and len(img.alt.split()) >= 3
    ]
    if len({alt.casefold() for alt in artwork_alts}) >= 2:
        scores["artwork_collection"] += 3
        reasons.append("distinct_artwork_alts")

    if ARTWORK_PATH_RE.search(path) and not CATEGORY_PATH_RE.search(path):
        scores["artwork_collection"] += 1
        scores["artwork_detail"] += 1
        reasons.append("artwork_like_path")

    if CATEGORY_PATH_RE.search(path):
        scores["artwork_detail"] -= 5
        scores["artwork_collection"] += 2
        reasons.append("category_path_not_detail")

    if soup.find("script", attrs={"type": "application/ld+json"}):
        scores["artwork_detail"] += 2
        reasons.append("json_ld_present")

    og_title_tag = soup.find("meta", property="og:title")
    og_url_tag = soup.find("meta", property="og:url")
    og_title = ""
    if og_title_tag and og_title_tag.get("content"):
        og_title = strip_site_title_suffix(
            clean_text(og_title_tag.get("content")),
            artist_name,
        )
        scores["artwork_detail"] += 1
        scores["editorial_project_detail"] += 1
        reasons.append("og_title")

    og_url_matches = False
    if og_url_tag and og_url_tag.get("content"):
        og_url = normalize_url(og_url_tag.get("content"), page.url)
        og_url_matches = og_url == normalize_url(page.url)
        if og_url_matches:
            scores["artwork_detail"] += 1
            scores["editorial_project_detail"] += 2
            reasons.append("og_url_matches_page")

    detail_url_score = score_detail_url(page.url)
    if detail_url_score >= 4 and not CATEGORY_PATH_RE.search(path):
        scores["artwork_detail"] += 4
        reasons.append(f"detail_url_score:{detail_url_score}")

    has_subtitle_title = bool(
        soup.select_one(".subtitle .title, .title_and_year_title, [itemprop='name']")
    )
    has_medium = bool(soup.select_one(".medium, [class*='medium'], [itemprop='material']"))
    has_dimensions = bool(soup.select_one(".dimensions, [class*='dimension']"))
    if has_subtitle_title and (has_medium or has_dimensions):
        scores["artwork_detail"] += 4
        reasons.append("explicit_artwork_detail_fields")

    if DETAIL_PATH_RE.search(path) and not CATEGORY_PATH_RE.search(path):
        scores["artwork_detail"] += 3
        reasons.append("detail_path_pattern")

    if len(page.captions) >= 2 and len(unique_details) == 0:
        scores["editorial_project_detail"] += 3
        scores["artwork_collection"] += 2
        reasons.append("multi_caption_collection")

    h1 = clean_text(page.h1)
    h1_clean = strip_site_title_suffix(h1, artist_name)
    gallery_like = bool(
        soup.select(".project-slide-image, .sqs-gallery, .gallery-fullscreen-slideshow")
    ) or len(images) >= 2

    if (
        h1_clean
        and not HARD_UTILITY_RE.search(h1_clean)
        and structural_cards < 2
        and len(unique_details) <= 1
        and len(bare_candidates) < 2
        and (len(images) >= 1 or gallery_like)
    ):
        scores["editorial_project_detail"] += 3
        reasons.append("individual_heading_with_images")
        if og_title and (
            og_title.casefold() == h1_clean.casefold()
            or h1_clean.casefold() in og_title.casefold()
        ):
            scores["editorial_project_detail"] += 2
            reasons.append("h1_matches_og_title")
        if og_url_matches:
            scores["editorial_project_detail"] += 1
        if gallery_like or len(images) >= 2:
            scores["editorial_project_detail"] += 2
            reasons.append("gallery_images_present")
        # Sparse metadata is expected for editorial project pages
        if not has_medium and not has_dimensions:
            scores["editorial_project_detail"] += 1
            reasons.append("sparse_project_metadata")

    if NAV_ONLY_RE.search(path) and scores["artwork_collection"] < 2 and scores["artwork_detail"] < 2:
        scores["navigation"] += 3
        reasons.append("nav_path")

    # Avoid treating an editorial detail page as a collection when footer links exist
    if (
        scores["editorial_project_detail"] >= 5
        and structural_cards < 2
        and scores["artwork_collection"] > 0
        and scores["artwork_collection"] <= scores["editorial_project_detail"]
    ):
        scores["artwork_collection"] = min(scores["artwork_collection"], 2)
        reasons.append("prefer_editorial_over_footer_links")

    page_type, confidence = _resolve_scores(scores, unique_details, page.url, structural_cards)
    keep_candidates = page_type in {
        "artwork_collection",
        "artwork_detail",
        "editorial_project_detail",
        "unknown",
    }
    # Detail/editorial pages should not keep provisional footer cards as primary evidence
    kept = candidates if keep_candidates else []
    if page_type in {"artwork_detail", "editorial_project_detail"} and structural_cards < 2:
        kept = []
    render_reasons = initial_render_reasons(page, kept if keep_candidates else [])
    return PageInterpretation(
        page_type=page_type,
        confidence=confidence,
        reasons=reasons,
        scores=scores,
        candidates=kept if keep_candidates else [],
        discovered_detail_urls=unique_details,
        render_recommended=bool(render_reasons),
        render_reasons=render_reasons,
    )


def _resolve_scores(
    scores: dict[str, int],
    unique_details: list[str],
    page_url: str,
    structural_cards: int = 0,
) -> tuple[PageType, str]:
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_type, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else -999

    if top_score < 2:
        return "unknown", "low"

    if top_score - second_score >= 2:
        conf = "high" if top_score >= 4 else "medium"
        return top_type, conf  # type: ignore[return-value]

    contenders = {
        name
        for name, score in scores.items()
        if score >= top_score - 1
        and name in {"artwork_collection", "artwork_detail", "editorial_project_detail"}
    }
    path = urlparse(page_url).path or "/"
    if "artwork_collection" in contenders and (
        len(unique_details) >= 2 and structural_cards >= 2
    ):
        return "artwork_collection", "medium"
    if "artwork_collection" in contenders and scores.get("artwork_collection", 0) >= 5:
        return "artwork_collection", "medium"
    if "artwork_detail" in contenders and (
        len(unique_details) <= 1 and DETAIL_PATH_RE.search(path)
    ):
        return "artwork_detail", "medium"
    if "editorial_project_detail" in contenders and structural_cards < 2:
        return "editorial_project_detail", "medium"
    if "artwork_collection" in contenders and len(unique_details) >= 2:
        return "artwork_collection", "medium"
    if top_score >= 2:
        return top_type, "medium"  # type: ignore[return-value]
    return "unknown", "low"
