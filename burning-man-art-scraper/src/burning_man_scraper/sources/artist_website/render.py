"""Render decision helpers for artist website pages."""

from __future__ import annotations

from burning_man_scraper.sources.artist_website.images import (
    has_lazy_placeholders,
    page_has_client_gallery_markers,
)
from burning_man_scraper.sources.artist_website.models import ArtworkCandidate


def initial_render_reasons(page, candidates: list[ArtworkCandidate] | None = None) -> list[str]:
    """Static-HTML reasons to request Playwright. Never depends on a rendered page."""
    reasons: list[str] = []
    candidates = candidates or []
    if has_lazy_placeholders(page.html):
        reasons.append("lazy_image_placeholders")
    usable = [url for url in page.image_urls if url and not url.startswith("data:")]
    if candidates and not usable and not any(c.images for c in candidates):
        reasons.append("collection_missing_usable_images")
    elif candidates and not any(c.images for c in candidates) and has_lazy_placeholders(page.html):
        reasons.append("cards_missing_resolved_images")
    if page_has_client_gallery_markers(page.html):
        # Only recommend render when images look unresolved
        if has_lazy_placeholders(page.html) or not usable:
            reasons.append("client_gallery_markers")
    incomplete = [
        c
        for c in candidates
        if (c.detail_url and not c.title) or (c.title and not c.detail_url and not c.excerpt)
    ]
    if incomplete and page_has_client_gallery_markers(page.html):
        reasons.append("incomplete_card_evidence")
    # Legacy sparse signal as a soft reason
    if len(page.text) < 120 or (not page.h1 and len(usable) < 2):
        reasons.append("sparse_static_html")
    return reasons


def rendered_is_richer(static_page, rendered_page) -> bool:
    static_score = len(static_page.text) + 10 * len(static_page.image_urls)
    rendered_score = len(rendered_page.text) + 10 * len(rendered_page.image_urls)
    return rendered_score > static_score


def material_extraction_disagreement(static_candidates: list, rendered_candidates: list) -> bool:
    static_keys = {
        (c.detail_url or "", (c.title or "").casefold(), c.year or "") for c in static_candidates
    }
    rendered_keys = {
        (c.detail_url or "", (c.title or "").casefold(), c.year or "") for c in rendered_candidates
    }
    if not static_keys and rendered_keys:
        return True
    if rendered_keys - static_keys:
        return True
    static_images = sum(len(c.images) for c in static_candidates)
    rendered_images = sum(len(c.images) for c in rendered_candidates)
    return rendered_images > static_images
