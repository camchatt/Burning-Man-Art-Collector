"""Per-page extraction audit artifact (artist-website-page-audit-v1)."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Sequence

from burning_man_scraper.sources.artist_website.confidence import (
    calibrate_candidate_flags,
    rollup_confidence,
)
from burning_man_scraper.sources.artist_website.models import ArtworkCandidate, PageInterpretation
from burning_man_scraper.sources.artist_website.text_normalize import normalize_identity_url

SCHEMA_VERSION = "artist-website-page-audit-v1"


def _candidate_audit(candidate: ArtworkCandidate) -> dict[str, Any]:
    rollup = rollup_confidence(candidate)
    return {
        "title": candidate.title,
        "year": candidate.year,
        "detail_url": candidate.detail_url,
        "collection_url": candidate.collection_url,
        "confidence": rollup,
        "review_flags": list(candidate.review_flags),
        "source_granularity": candidate.source_granularity,
        "field_provenance": [
            {
                "field": item.field,
                "value": item.value,
                "source_url": item.source_url,
                "source_kind": item.source_kind,
                "confidence": item.confidence,
                "selector_or_signal": item.selector_or_signal,
            }
            for item in candidate.evidence
        ],
        "images": [{"url": img.url, "alt": img.alt, "source_kind": img.source_kind} for img in candidate.images],
        "metadata": dict(candidate.metadata),
    }


def page_audit_entry(
    page,
    interpretation: PageInterpretation,
    *,
    accept: bool,
    reject_reasons: list[str] | None = None,
    merge_decisions: list[dict[str, Any]] | None = None,
    final_candidates: Sequence[ArtworkCandidate] | None = None,
) -> dict[str, Any]:
    for candidate in interpretation.candidates:
        calibrate_candidate_flags(candidate, page_interpretation=interpretation)
    finals = list(final_candidates or [])
    for candidate in finals:
        calibrate_candidate_flags(candidate, page_interpretation=interpretation)

    return {
        "url": normalize_identity_url(getattr(page, "url", "")),
        "requested_url": getattr(page, "requested_url", "") or "",
        "classification": {
            "page_type": interpretation.page_type,
            "confidence": interpretation.confidence,
            "scores": dict(interpretation.scores),
            "reasons": list(interpretation.reasons),
        },
        "discovered_candidates": [_candidate_audit(c) for c in interpretation.candidates],
        "discovered_detail_urls": list(interpretation.discovered_detail_urls),
        "render_recommendation": {
            "recommended": interpretation.render_recommended,
            "reasons": list(interpretation.render_reasons),
        },
        "accept": accept,
        "reject_reasons": list(reject_reasons or []),
        "merge_decisions": list(merge_decisions or []),
        "final_candidates": [_candidate_audit(c) for c in finals],
    }


def build_run_audit(
    pages: Sequence,
    interpretations: Sequence[PageInterpretation],
    *,
    merged: Sequence[ArtworkCandidate],
    merge_log: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pages_by_url = {
        normalize_identity_url(page.url): page for page in pages if getattr(page, "url", None)
    }
    merge_by_url: dict[str, list[dict[str, Any]]] = {}
    for decision in merge_log or []:
        key = normalize_identity_url(str(decision.get("url") or ""))
        merge_by_url.setdefault(key, []).append(decision)

    merged_by_page: dict[str, list[ArtworkCandidate]] = {}
    for candidate in merged:
        keys = {
            normalize_identity_url(candidate.detail_url) if candidate.detail_url else "",
            normalize_identity_url(candidate.collection_url) if candidate.collection_url else "",
            normalize_identity_url(candidate.page_url) if candidate.page_url else "",
        }
        for key in keys:
            if key:
                merged_by_page.setdefault(key, []).append(candidate)

    page_entries: list[dict[str, Any]] = []
    for interpretation, page in zip(interpretations, pages):
        page_key = normalize_identity_url(page.url)
        skip_types = {"commerce_utility", "irrelevant", "navigation"}
        accept = interpretation.page_type not in skip_types
        reject_reasons = []
        if not accept:
            reject_reasons.append(f"page_type={interpretation.page_type}")
        page_entries.append(
            page_audit_entry(
                page,
                interpretation,
                accept=accept,
                reject_reasons=reject_reasons,
                merge_decisions=merge_by_url.get(page_key, []),
                final_candidates=merged_by_page.get(page_key, []),
            )
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "page_count": len(page_entries),
        "merged_candidate_count": len(merged),
        "pages": page_entries,
        "merged_candidates": [_candidate_audit(c) for c in merged],
        # Retain page map size for debugging without dumping HTML.
        "known_page_urls": sorted(pages_by_url.keys()),
    }


def write_page_extraction_audit(run_path: Path, audit: dict[str, Any]) -> Path:
    out = run_path / "page_extraction_audit.json"
    out.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return out


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
