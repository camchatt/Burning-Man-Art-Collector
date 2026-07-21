"""Site-level artwork extraction orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from burning_man_scraper.sources.artist_website.audit import build_run_audit, write_page_extraction_audit
from burning_man_scraper.sources.artist_website.classify import classify_page
from burning_man_scraper.sources.artist_website.confidence import calibrate_candidate_flags
from burning_man_scraper.sources.artist_website.discover import normalize_detail_url
from burning_man_scraper.sources.artist_website.extract import extract_detail_candidate
from burning_man_scraper.sources.artist_website.merge import dedupe_merged_candidates, merge_candidates
from burning_man_scraper.sources.artist_website.models import ArtworkCandidate, PageInterpretation


def interpret_pages(
    pages: Sequence,
    artist_name: str = "",
) -> list[PageInterpretation]:
    return [classify_page(page, artist_name=artist_name) for page in pages]


def extract_site_artworks(
    pages: Sequence,
    artist_name: str = "",
    logs: list | None = None,
    *,
    run_path: Path | None = None,
) -> list[ArtworkCandidate]:
    """Classify pages, merge collection/detail evidence, return one candidate per artwork."""
    from burning_man_scraper.sources.artist_website.ingest import LogEntry, timestamp

    logs = logs if logs is not None else []
    interpretations = interpret_pages(pages, artist_name=artist_name)
    pages_by_url = {normalize_detail_url(page.url): page for page in pages}

    collection_candidates: list[ArtworkCandidate] = []
    detail_by_url: dict[str, ArtworkCandidate] = {}
    merge_log: list[dict] = []

    for interpretation, page in zip(interpretations, pages):
        if interpretation.confidence == "low" or interpretation.page_type == "unknown":
            for candidate in interpretation.candidates:
                if "ambiguous_page_type" not in candidate.review_flags:
                    candidate.review_flags.append("ambiguous_page_type")
        logs.append(
            LogEntry(
                timestamp(),
                page.url,
                "classify",
                interpretation.page_type,
                detail=(
                    f"confidence={interpretation.confidence}; "
                    f"scores={interpretation.scores}; "
                    f"reasons={','.join(interpretation.reasons)}; "
                    f"render={interpretation.render_recommended}:{','.join(interpretation.render_reasons)}"
                ),
            )
        )
        if interpretation.page_type in {"commerce_utility", "irrelevant", "navigation"}:
            logs.append(
                LogEntry(
                    timestamp(),
                    page.url,
                    "extract",
                    "skipped",
                    detail=f"page_type={interpretation.page_type}",
                )
            )
            continue

        if interpretation.page_type == "artwork_detail":
            try:
                detail = extract_detail_candidate(page, artist_name=artist_name)
            except Exception as error:  # noqa: BLE001 - keep crawl resilient
                logs.append(
                    LogEntry(
                        timestamp(),
                        page.url,
                        "extract",
                        "error",
                        detail=str(error),
                    )
                )
                if interpretation.candidates:
                    for candidate in interpretation.candidates:
                        candidate.review_flags.append("low_confidence_entity")
                        collection_candidates.append(candidate)
                continue
            if detail.title or detail.images:
                calibrate_candidate_flags(detail, page_interpretation=interpretation)
                detail_by_url[normalize_detail_url(detail.detail_url or page.url)] = detail
                logs.append(
                    LogEntry(
                        timestamp(),
                        page.url,
                        "extract",
                        "detail",
                        detail=f"title={detail.title!r}; flags={detail.review_flags}",
                    )
                )
            elif interpretation.candidates:
                for candidate in interpretation.candidates:
                    candidate.review_flags.append("low_confidence_entity")
                    collection_candidates.append(candidate)
            continue

        # Collections and editorial pages contribute provisional cards
        for candidate in interpretation.candidates:
            calibrate_candidate_flags(candidate, page_interpretation=interpretation)
            collection_candidates.append(candidate)
        # Also treat editorial single-page projects without cards
        if (
            interpretation.page_type == "editorial_project_detail"
            and not interpretation.candidates
        ):
            try:
                detail = extract_detail_candidate(page, artist_name=artist_name)
            except Exception as error:  # noqa: BLE001
                logs.append(
                    LogEntry(timestamp(), page.url, "extract", "error", detail=str(error))
                )
                continue
            if detail.title:
                # No detail_url identity beyond page URL
                detail.detail_url = normalize_detail_url(page.url)
                detail.page_url = detail.detail_url
                calibrate_candidate_flags(detail, page_interpretation=interpretation)
                detail_by_url[detail.detail_url] = detail

        if interpretation.discovered_detail_urls:
            logs.append(
                LogEntry(
                    timestamp(),
                    page.url,
                    "discover",
                    "detail_urls",
                    detail=f"{len(interpretation.discovered_detail_urls)} urls",
                )
            )

    # Merge collection cards with detail pages
    merged: list[ArtworkCandidate] = []
    used_details: set[str] = set()

    for candidate in collection_candidates:
        detail_key = normalize_detail_url(candidate.detail_url) if candidate.detail_url else ""
        detail = detail_by_url.get(detail_key) if detail_key else None
        if detail_key and detail_key not in pages_by_url and detail is None:
            candidate.review_flags.append("missing_detail_page")
            if "collection_only" not in candidate.review_flags:
                candidate.review_flags.append("collection_only")
        if detail:
            used_details.add(detail_key)
            merged_candidate = merge_candidates(candidate, detail)
            calibrate_candidate_flags(merged_candidate)
            merge_log.append(
                {
                    "url": detail_key or candidate.page_url,
                    "action": "combined",
                    "collection_title": candidate.title,
                    "detail_title": detail.title,
                    "merged_title": merged_candidate.title,
                    "flags": list(merged_candidate.review_flags),
                }
            )
            logs.append(
                LogEntry(
                    timestamp(),
                    detail_key or candidate.page_url,
                    "merge",
                    "combined",
                    detail=f"title={merged_candidate.title!r}",
                )
            )
            merged.append(merged_candidate)
        else:
            solo = merge_candidates(candidate, None)
            calibrate_candidate_flags(solo)
            merge_log.append(
                {
                    "url": candidate.detail_url or candidate.page_url,
                    "action": "collection_only",
                    "merged_title": solo.title,
                    "flags": list(solo.review_flags),
                }
            )
            merged.append(solo)

    for key, detail in detail_by_url.items():
        if key not in used_details:
            # Avoid double-counting category listing pages mistaken as details when
            # they already contributed collection cards with the same title.
            calibrate_candidate_flags(detail)
            merge_log.append(
                {
                    "url": key,
                    "action": "detail_only",
                    "merged_title": detail.title,
                    "flags": list(detail.review_flags),
                }
            )
            merged.append(detail)

    result = dedupe_merged_candidates(merged, artist_name=artist_name)
    for candidate in result:
        calibrate_candidate_flags(candidate)
        if candidate.confidence < 0.4 or "low_confidence_entity" in candidate.review_flags:
            logs.append(
                LogEntry(
                    timestamp(),
                    candidate.detail_url or candidate.page_url,
                    "review",
                    "low_confidence",
                    detail=f"title={candidate.title!r}; flags={candidate.review_flags}",
                )
            )

    if run_path is not None:
        audit = build_run_audit(
            pages,
            interpretations,
            merged=result,
            merge_log=merge_log,
        )
        write_page_extraction_audit(Path(run_path), audit)

    return result
