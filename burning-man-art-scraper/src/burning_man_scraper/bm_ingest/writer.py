from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from burning_man_scraper.bm_ingest.schema import BM_EXTENSION_HEADERS
from burning_man_scraper.bm_ingest.view_bundle import build_aggregator_view, copy_view_to_viewer


def write_ingest_outputs(
    *,
    output_dir: Path,
    year: int,
    rows: list[dict[str, str]],
    artelier_headers: list[str],
    fetch_missing_heroes: bool,
    project_root: Path | None = None,
    stats: dict | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    extended_headers = list(artelier_headers) + list(BM_EXTENSION_HEADERS)
    # Guard against accidental header collisions (historically produced hero_image_url.1).
    if len(extended_headers) != len(set(extended_headers)):
        dupes = [h for h in extended_headers if extended_headers.count(h) > 1]
        raise ValueError(f"Duplicate CSV headers are not allowed: {sorted(set(dupes))}")

    upload_path = output_dir / f"artelier_bm_upload_{year}.csv"
    core_path = output_dir / f"artelier_core_only_{year}.csv"
    review_path = output_dir / f"review_queue_{year}.csv"
    summary_path = output_dir / f"ingest_summary_{year}.json"
    view_path = output_dir / f"aggregator_view_{year}.json"

    warnings: list[str] = []
    upload_path = _write_csv(upload_path, rows, extended_headers, warnings=warnings)
    core_path = _write_csv(core_path, rows, artelier_headers, warnings=warnings)

    review_rows = [row for row in rows if (row.get("review_flags") or "").strip()]
    review_path = _write_csv(review_path, review_rows, extended_headers, warnings=warnings)

    flag_counter: Counter[str] = Counter()
    for row in rows:
        for flag in (row.get("review_flags") or "").split("|"):
            if flag:
                flag_counter[flag] += 1

    view = build_aggregator_view(year=year, rows=rows)
    view_path.write_text(json.dumps(view, indent=2), encoding="utf-8")

    viewer_copy = None
    if project_root is not None:
        viewer_copy = copy_view_to_viewer(view_path, project_root, year)

    checklist = view["upload_checklist"]
    summary = {
        "schema_version": "burning-man-artelier-ingest-v1",
        "year": year,
        "project_count": len(rows),
        "source_rows": (stats or {}).get("source_rows", len(rows)),
        "export_rows": len(rows),
        "review_queue_count": len(review_rows),
        "fetch_missing_heroes": fetch_missing_heroes,
        "processing_mode": (stats or {}).get("processing_mode") or "fast_upload",
        "network_requests_attempted": (stats or {}).get("network_requests_attempted", 0),
        "with_hero_image": checklist["with_hero_image"],
        "with_playa_address": checklist["with_playa_address"],
        "with_contributor_display_name": checklist["with_contributor_display_name"],
        "with_bm_uid": sum(1 for row in rows if row.get("bm_uid")),
        "upload_ready_count": checklist["upload_ready_count"],
        "needs_attention_count": checklist["needs_attention_count"],
        "review_flag_counts": dict(sorted(flag_counter.items())),
        "cache_inventory": (stats or {}).get("cache_inventory") or {},
        "identity_cache_matches": (stats or {}).get("identity_cache_matches", 0),
        "identity_local_fallbacks": (stats or {}).get("identity_local_fallbacks", 0),
        "identity_title_fallback_matches": (stats or {}).get("identity_title_fallback_matches", 0),
        "verification_matches": (stats or {}).get("verification_matches", 0),
        "uid_matches": (stats or {}).get("uid_matches", 0),
        "resolved_people": (stats or {}).get("resolved_people", 0),
        "resolved_organizations": (stats or {}).get("resolved_organizations", 0),
        "resolved_multiple_credits": (stats or {}).get("resolved_multiple_credits", 0),
        "burner_names_found": (stats or {}).get("burner_names_found", 0),
        "hero_images_found": (stats or {}).get("hero_images_found", 0),
        "proof_links_found": (stats or {}).get("proof_links_found", 0),
        "rows_needing_review": checklist["needs_attention_count"],
        "rows_missing_primary_name": (stats or {}).get("rows_missing_primary_name", 0),
        "rows_missing_hero": (stats or {}).get("rows_missing_hero", 0),
        "duplicate_uid_count": (stats or {}).get("duplicate_uid_count", 0),
        "title_fallback_match_count": (stats or {}).get("title_fallback_match_count", 0),
        "primary_output": "artelier_bm_upload",
        "write_warnings": warnings,
        "output_files": {
            "artelier_bm_upload": str(upload_path),
            "artelier_core_only": str(core_path),
            "review_queue": str(review_path),
            "aggregator_view": str(view_path),
            "aggregator_view_www_preview": str(viewer_copy) if viewer_copy else "",
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    paths = {
        "upload": upload_path,
        "core": core_path,
        "review": review_path,
        "summary": summary_path,
        "view": view_path,
    }
    if viewer_copy is not None:
        paths["www_preview"] = viewer_copy
        paths["viewer_view"] = viewer_copy
    return paths


def _write_csv(
    path: Path,
    rows: list[dict[str, str]],
    headers: list[str],
    *,
    warnings: list[str] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})
    try:
        tmp_path.replace(path)
        return path
    except PermissionError:
        # Windows file lock (CSV open in Excel/OneDrive): keep a writable sidecar.
        sidecar = path.with_name(path.stem + "_fresh" + path.suffix)
        tmp_path.replace(sidecar)
        if warnings is not None:
            warnings.append(
                f"Could not overwrite locked file {path.name}; wrote {sidecar.name} instead."
            )
        return sidecar
