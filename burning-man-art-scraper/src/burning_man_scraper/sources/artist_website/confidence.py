"""Confidence calibration and review-flag helpers for artist-website candidates."""

from __future__ import annotations

from typing import Any

from burning_man_scraper.sources.artist_website.models import ArtworkCandidate, PageInterpretation

# Known review flags (extend, do not invent silent fields).
FLAG_LOW_CONFIDENCE_ENTITY = "low_confidence_entity"
FLAG_AMBIGUOUS_PAGE_TYPE = "ambiguous_page_type"
FLAG_WEAK_PROJECT_TYPE = "weak_project_type"
FLAG_METADATA_CONFLICT = "metadata_conflict"
FLAG_MISSING_HERO = "missing_hero_image"
FLAG_COLLECTION_ONLY = "collection_only"
FLAG_TITLE_FROM_SLUG = "title_inferred_from_slug"
FLAG_TITLE_FROM_ALT = "title_inferred_from_alt"


def _label_from_float(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def field_confidence_map(candidate: ArtworkCandidate) -> dict[str, dict[str, Any]]:
    """Per-field confidence preserving nuance from evidence when present."""
    by_field: dict[str, list] = {}
    for item in candidate.evidence:
        if not item.field or item.field == "merge":
            continue
        by_field.setdefault(item.field, []).append(item)

    fields = {
        "title": candidate.title,
        "year": candidate.year,
        "detail_url": candidate.detail_url,
        "hero_image": candidate.images[0].url if candidate.images else "",
        "medium": candidate.metadata.get("medium", ""),
        "dimensions": candidate.metadata.get("dimensions", ""),
        "series": candidate.metadata.get("series", ""),
        "price": candidate.metadata.get("price", ""),
        "description": candidate.metadata.get("description", "") or candidate.excerpt,
    }
    result: dict[str, dict[str, Any]] = {}
    for name, value in fields.items():
        evidence_items = by_field.get(name, [])
        if not value:
            result[name] = {
                "value": "",
                "confidence": "none",
                "score": 0.0,
                "source_kind": "",
                "inferred": False,
            }
            continue
        if evidence_items:
            best = max(evidence_items, key=lambda item: item.confidence)
            score = float(best.confidence)
            source_kind = best.source_kind
        else:
            score = 0.45
            source_kind = "implicit"
        inferred = any(
            flag in candidate.review_flags
            for flag in (
                FLAG_TITLE_FROM_SLUG,
                FLAG_TITLE_FROM_ALT,
                "year_inferred",
            )
        ) and name in {"title", "year"}
        if inferred:
            score = min(score, 0.45)
        result[name] = {
            "value": value,
            "confidence": _label_from_float(score),
            "score": round(score, 3),
            "source_kind": source_kind,
            "inferred": inferred,
        }
    return result


def rollup_confidence(candidate: ArtworkCandidate) -> dict[str, Any]:
    """Candidate rollup that keeps per-field nuance."""
    fields = field_confidence_map(candidate)
    scores = [item["score"] for item in fields.values() if item["value"]]
    title_score = fields.get("title", {}).get("score", 0.0)
    if not candidate.title:
        overall = 0.15
    elif scores:
        # Conservative: weight title heavily, do not average away weak title.
        overall = min(candidate.confidence or 0.5, 0.55 * title_score + 0.45 * (sum(scores) / len(scores)))
    else:
        overall = float(candidate.confidence or 0.3)
    if FLAG_LOW_CONFIDENCE_ENTITY in candidate.review_flags:
        overall = min(overall, 0.35)
    if FLAG_TITLE_FROM_SLUG in candidate.review_flags:
        overall = min(overall, 0.4)
    return {
        "overall_score": round(overall, 3),
        "overall_label": _label_from_float(overall),
        "fields": fields,
    }


def calibrate_candidate_flags(
    candidate: ArtworkCandidate,
    *,
    page_interpretation: PageInterpretation | None = None,
    project_type: str = "",
    mapped_type_confidence: str = "",
) -> list[str]:
    """Ensure review flags reflect weak / ambiguous evidence; return updated flag list."""
    flags = list(dict.fromkeys(candidate.review_flags))

    if page_interpretation and page_interpretation.confidence == "low":
        if FLAG_AMBIGUOUS_PAGE_TYPE not in flags:
            flags.append(FLAG_AMBIGUOUS_PAGE_TYPE)

    if page_interpretation and page_interpretation.page_type == "unknown":
        if FLAG_AMBIGUOUS_PAGE_TYPE not in flags:
            flags.append(FLAG_AMBIGUOUS_PAGE_TYPE)

    if mapped_type_confidence == "low" or project_type in {"", "Other"}:
        if FLAG_WEAK_PROJECT_TYPE not in flags and project_type:
            flags.append(FLAG_WEAK_PROJECT_TYPE)

    if "conflicting_title" in flags or "conflicting_year" in flags:
        if FLAG_METADATA_CONFLICT not in flags:
            flags.append(FLAG_METADATA_CONFLICT)

    if not candidate.title or candidate.confidence < 0.4:
        if FLAG_LOW_CONFIDENCE_ENTITY not in flags:
            flags.append(FLAG_LOW_CONFIDENCE_ENTITY)

    if not candidate.images and FLAG_MISSING_HERO not in flags:
        flags.append(FLAG_MISSING_HERO)

    # Inferred fields must carry a flag (DoD invariant).
    title_evidence = [e for e in candidate.evidence if e.field == "title"]
    if candidate.title and title_evidence:
        kinds = {e.source_kind for e in title_evidence}
        if kinds <= {"url_slug", "image_alt"} and FLAG_TITLE_FROM_SLUG not in flags and FLAG_TITLE_FROM_ALT not in flags:
            if "url_slug" in kinds:
                flags.append(FLAG_TITLE_FROM_SLUG)
            if "image_alt" in kinds:
                flags.append(FLAG_TITLE_FROM_ALT)

    candidate.review_flags = list(dict.fromkeys(flags))
    rollup = rollup_confidence(candidate)
    candidate.confidence = rollup["overall_score"]
    return candidate.review_flags


def apply_conservative_project_type(
    project_type: str,
    *,
    mapped_confidence: str,
    flags: list[str],
) -> tuple[str, str]:
    """Refuse aggressive type mapping when evidence is weak."""
    if mapped_confidence == "low" or FLAG_WEAK_PROJECT_TYPE in flags or FLAG_LOW_CONFIDENCE_ENTITY in flags:
        if project_type and project_type != "Other":
            # Keep suggested type but force low confidence for human review.
            return project_type, "low"
        return "Other", "low"
    return project_type, mapped_confidence
