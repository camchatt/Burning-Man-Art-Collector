"""Merge collection and detail artwork evidence."""

from __future__ import annotations

from burning_man_scraper.sources.artist_website.discover import normalize_detail_url
from burning_man_scraper.sources.artist_website.models import ArtworkCandidate, ArtworkEvidence


FIELD_PRIORITY = {
    "detail_container": 95,
    "json_ld": 90,
    "visible_heading": 85,
    "open_graph": 80,
    "visible_text": 60,
    "collection_card": 50,
    "figure_caption": 50,
    "image_alt": 40,
    "url_slug": 35,
}


def _best_evidence(candidate: ArtworkCandidate, field: str) -> ArtworkEvidence | None:
    matches = [item for item in candidate.evidence if item.field == field and item.value]
    if not matches:
        return None
    return max(
        matches,
        key=lambda item: (item.confidence, FIELD_PRIORITY.get(item.source_kind, 0)),
    )


def merge_candidates(
    collection: ArtworkCandidate | None,
    detail: ArtworkCandidate | None,
) -> ArtworkCandidate:
    if collection and not detail:
        merged = ArtworkCandidate(
            title=collection.title,
            year=collection.year,
            detail_url=collection.detail_url,
            collection_url=collection.collection_url or collection.page_url,
            images=list(collection.images),
            metadata=dict(collection.metadata),
            evidence=list(collection.evidence),
            confidence=collection.confidence,
            review_flags=list(dict.fromkeys([*collection.review_flags, "collection_only"])),
            excerpt=collection.excerpt,
            source_granularity=collection.source_granularity,
            page_text=collection.page_text,
            page_url=collection.page_url,
        )
        if not merged.detail_url:
            merged.review_flags.append("missing_detail_page")
        return merged

    if detail and not collection:
        return detail

    assert collection is not None and detail is not None
    merged = ArtworkCandidate(
        title="",
        year="",
        detail_url=normalize_detail_url(detail.detail_url or collection.detail_url),
        collection_url=collection.collection_url or collection.page_url,
        images=[],
        metadata={},
        evidence=[],
        confidence=max(collection.confidence, detail.confidence),
        review_flags=[],
        excerpt="",
        source_granularity="Individual project page",
        page_text=detail.page_text or collection.page_text,
        page_url=detail.detail_url or detail.page_url,
    )

    # Combine evidence then resolve fields
    merged.evidence = [*detail.evidence, *collection.evidence]

    for field in ("title", "year"):
        best = _best_evidence(merged, field)
        if best:
            setattr(merged, field, best.value)
        elif getattr(detail, field):
            setattr(merged, field, getattr(detail, field))
        elif getattr(collection, field):
            setattr(merged, field, getattr(collection, field))
            if field == "title" and "title_inferred_from_alt" in collection.review_flags:
                merged.review_flags.append("title_inferred_from_alt")

    for field in (
        "medium",
        "dimensions",
        "series",
        "price",
        "availability",
        "signature",
        "inventory",
        "edition_kind",
        "description",
    ):
        best = _best_evidence(merged, field)
        if best:
            merged.metadata[field] = best.value
        elif collection.metadata.get(field) and field not in merged.metadata:
            merged.metadata[field] = collection.metadata[field]

    # Images: prefer detail real images, keep alts paired, drop logos/format dupes
    from burning_man_scraper.sources.artist_website.images import (
        image_identity_key,
        prefer_artwork_images,
    )

    merged.images = prefer_artwork_images(
        [*detail.images, *collection.images],
        artist_name="",
    )
    if not merged.images:
        seen: set[str] = set()
        for image in [*detail.images, *collection.images]:
            key = image_identity_key(image.url).casefold()
            if image.url and key not in seen:
                seen.add(key)
                merged.images.append(image)

    # Excerpt: prefer detail
    merged.excerpt = detail.excerpt or collection.excerpt
    if len(detail.excerpt) >= len(collection.excerpt):
        merged.excerpt = detail.excerpt or collection.excerpt

    # Flags
    for flag in [*detail.review_flags, *collection.review_flags]:
        if flag in {"collection_only", "missing_detail_page"}:
            continue
        if flag not in merged.review_flags:
            merged.review_flags.append(flag)

    if collection.title and detail.title and collection.title.casefold() != detail.title.casefold():
        # Detail wins; note conflict if collection looked authoritative
        if "title_inferred_from_alt" not in collection.review_flags:
            if "conflicting_title" not in merged.review_flags:
                merged.review_flags.append("conflicting_title")

    if collection.year and detail.year and collection.year != detail.year:
        if "conflicting_year" not in merged.review_flags:
            merged.review_flags.append("conflicting_year")

    if not merged.images:
        merged.review_flags.append("missing_hero_image")
    if not merged.title:
        merged.review_flags.append("low_confidence_entity")

    merged.evidence.append(
        ArtworkEvidence(
            field="merge",
            value=merged.detail_url,
            source_url=merged.detail_url,
            source_kind="merge",
            confidence=1.0,
            selector_or_signal="collection+detail",
        )
    )
    return merged


def dedupe_merged_candidates(
    candidates: list[ArtworkCandidate],
    artist_name: str = "",
) -> list[ArtworkCandidate]:
    from burning_man_scraper.sources.artist_website.ingest import normalize_title

    by_detail: dict[str, ArtworkCandidate] = {}
    no_detail: list[ArtworkCandidate] = []

    for candidate in candidates:
        if candidate.detail_url:
            key = normalize_detail_url(candidate.detail_url)
            existing = by_detail.get(key)
            if not existing:
                by_detail[key] = candidate
            else:
                by_detail[key] = merge_candidates(
                    existing if existing.collection_url else candidate,
                    candidate if candidate.source_granularity == "Individual project page" else existing,
                )
        else:
            no_detail.append(candidate)

    # Secondary dedupe for no-detail: artist+title+year
    secondary: dict[tuple[str, str, str], ArtworkCandidate] = {}
    for candidate in no_detail:
        key = (
            normalize_title(artist_name),
            normalize_title(candidate.title),
            candidate.year or "",
        )
        existing = secondary.get(key)
        if not existing or len(candidate.excerpt) > len(existing.excerpt):
            secondary[key] = candidate

    # Drop no-detail rows that match a detail title+year already present
    detail_title_keys = {
        (normalize_title(item.title), item.year or "") for item in by_detail.values()
    }
    kept_secondary = [
        item
        for item in secondary.values()
        if (normalize_title(item.title), item.year or "") not in detail_title_keys
    ]
    return [*by_detail.values(), *kept_secondary]
