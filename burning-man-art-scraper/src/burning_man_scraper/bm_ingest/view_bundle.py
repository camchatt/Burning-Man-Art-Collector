from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

from burning_man_scraper.bm_ingest.identity_join import collapse_person_or_org

FLAG_LABELS = {
    "duplicate_candidate": "Possible duplicate",
    "name_split_uncertain": "Name split uncertain",
    "playa_name_uncertain": "Playa name uncertain",
    "contributor_kind_uncertain": "Contributor type uncertain",
    "identity_needs_review": "Identity needs review",
    "hero_missing": "Missing hero image",
    "hero_needs_review": "Hero needs review",
    "missing_archive_cache": "Missing archive cache",
    "honorarium_unknown": "Honorarium unknown",
}

# Flags that mean "look before upload" (exclude honorarium-only noise).
ATTENTION_FLAGS = {
    "duplicate_candidate",
    "name_split_uncertain",
    "playa_name_uncertain",
    "contributor_kind_uncertain",
    "identity_needs_review",
    "hero_missing",
    "hero_needs_review",
    "missing_archive_cache",
}


def is_row_upload_ready(row: dict[str, str]) -> bool:
    """Same readiness rule as Aggregator preview: required fields + no attention flags."""
    flags = [flag for flag in (row.get("review_flags") or "").split("|") if flag]
    title = (row.get("project_title") or "").strip()
    slug = (row.get("project_slug") or "").strip()
    proof_url = (row.get("proof_external_url") or "").strip()
    attention_flags = [flag for flag in flags if flag in ATTENTION_FLAGS]
    missing_required = not title or not slug or not proof_url
    return not missing_required and not attention_flags


def build_aggregator_view(*, year: int, rows: list[dict[str, str]]) -> dict:
    projects = [_project_from_row(row) for row in rows]
    needs_attention = [p for p in projects if p["needs_attention"]]
    upload_ready = [p for p in projects if p["upload_ready"]]
    flag_counts: dict[str, int] = {}
    for project in projects:
        for flag in project["review_flags"]:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1

    return {
        "schema_version": "aggregator-view-v1",
        "meta": {
            "year": year,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "about": (
                f"Pre-upload preview of Burning Man {year} Artelier ingest. "
                "Verify heroes, credits, places, and review flags before uploading artelier_bm_upload CSV."
            ),
        },
        "upload_checklist": {
            "project_count": len(projects),
            "upload_ready_count": len(upload_ready),
            "needs_attention_count": len(needs_attention),
            "missing_proof_count": sum(1 for p in projects if not p["proof_url"]),
            "missing_hero_count": sum(1 for p in projects if not p["hero"]["url"]),
            "with_hero_image": sum(1 for p in projects if p["hero"]["url"]),
            "with_playa_address": sum(1 for p in projects if p["place"]["playa_address"]),
            "with_contributor_display_name": sum(
                1 for p in projects if p["people"]["contributor_display_name"]
            ),
        },
        "summary": {
            "review_flag_counts": dict(sorted(flag_counts.items())),
            "flag_labels": FLAG_LABELS,
        },
        "projects": projects,
    }


def _project_from_row(row: dict[str, str]) -> dict:
    flags = [flag for flag in (row.get("review_flags") or "").split("|") if flag]
    provenance = [part for part in (row.get("source_provenance") or "").split("|") if part]
    title = (row.get("project_title") or "").strip()
    slug = (row.get("project_slug") or "").strip()
    proof_url = (row.get("proof_external_url") or "").strip()
    hero_url = (row.get("hero_image_url") or "").strip()
    attention_flags = [flag for flag in flags if flag in ATTENTION_FLAGS]
    missing_required = not title or not slug or not proof_url
    upload_ready = is_row_upload_ready(row)
    needs_attention = not upload_ready

    return {
        "title": title,
        "year": row.get("project_year") or row.get("bm_year") or "",
        "uid": row.get("bm_uid") or "",
        "slug": slug,
        "proof_url": proof_url,
        "summary": row.get("project_summary") or "",
        "people": {
            "source_artist_credit": row.get("source_artist_credit") or "",
            "contributor_display_name": row.get("contributor_display_name") or "",
            "additional_contributor_credits": row.get("additional_contributor_credits") or "",
            "contributor_kind": row.get("contributor_kind") or "unknown",
            "person_or_org": collapse_person_or_org(row.get("contributor_kind") or "unknown"),
            "name": row.get("contributor_display_name") or "",
            "alt_burner_name": row.get("playa_name") or "",
            "contributor_first_name": row.get("contributor_first_name") or "",
            "contributor_last_name": row.get("contributor_last_name") or "",
            "playa_name": row.get("playa_name") or "",
            "playa_name_confidence": row.get("playa_name_confidence") or "none",
        },
        "place": {
            "playa_address": row.get("playa_address") or "",
            "theme_camp": row.get("theme_camp") or "",
            "installation_type": row.get("installation_type") or row.get("project_type") or "",
            "project_location": row.get("project_location") or "",
        },
        "hero": {
            "url": hero_url,
            "source_page": row.get("hero_image_source_page") or "",
            "attribution": row.get("hero_image_attribution") or "",
            "confidence": row.get("hero_image_confidence") or "",
        },
        "review_flags": flags,
        "attention_flags": attention_flags,
        "source_provenance": provenance,
        "needs_attention": needs_attention,
        "upload_ready": upload_ready,
        "review_priority": _review_priority(flags, missing_required=missing_required, has_hero=bool(hero_url)),
        "flag_labels": [FLAG_LABELS.get(flag, flag) for flag in flags],
    }


def _review_priority(flags: list[str], *, missing_required: bool, has_hero: bool) -> int:
    score = 0
    if missing_required:
        score += 100
    if "hero_missing" in flags or not has_hero:
        score += 50
    if "hero_needs_review" in flags:
        score += 30
    if "duplicate_candidate" in flags:
        score += 25
    if "contributor_kind_uncertain" in flags:
        score += 20
    if "playa_name_uncertain" in flags:
        score += 15
    if "name_split_uncertain" in flags:
        score += 10
    if "missing_archive_cache" in flags:
        score += 10
    if flags == ["honorarium_unknown"] or (flags and set(flags) <= {"honorarium_unknown"}):
        score += 1
    return score


def copy_view_to_viewer(view_path: Path, project_root: Path, year: int) -> Path:
    target_dir = project_root / "viewer" / "aggregator" / "data"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"aggregator_view_{year}.json"
    shutil.copy2(view_path, target)
    # Also keep a stable default name for the GUI's first fetch.
    default_target = target_dir / "aggregator_view.json"
    shutil.copy2(view_path, default_target)
    return target
