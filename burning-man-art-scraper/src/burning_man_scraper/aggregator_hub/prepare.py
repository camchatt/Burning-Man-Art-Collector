from __future__ import annotations

import json
import shutil
from pathlib import Path

from burning_man_scraper.bm_ingest.merge import run_ingest
from burning_man_scraper.bm_ingest.sources import cache_inventory
from burning_man_scraper.config import load_config
from burning_man_scraper.identity.cli import run_identity_resolution
from burning_man_scraper.verification.cli import run_verification
from burning_man_scraper.verification.www_loader import (
    assert_art_csv_matches_year,
    assert_playaevents_art_csv,
    resolve_art_csv_year,
)


DEFAULT_IDENTITY_LIMIT = 50


def year_has_outputs(project_root: Path, year: int) -> bool:
    ingest_dir = project_root / "data" / "bm_ingest" / str(year)
    verify_dir = project_root / "data" / "verification" / str(year)
    if ingest_dir.exists() and (any(ingest_dir.glob("*.csv")) or (ingest_dir / f"ingest_summary_{year}.json").exists()):
        return True
    if verify_dir.exists() and (verify_dir / f"verification_report_{year}.csv").exists():
        return True
    return False


def stage_upload_for_www_loader(art_path: Path, year: int) -> Path:
    """Ensure job-local file is named PlayaEvents-{year}_ART.csv (never writes to WWW library)."""
    staged = art_path.parent / f"PlayaEvents-{year}_ART.csv"
    if staged.resolve() != art_path.resolve():
        shutil.copy2(art_path, staged)
    return staged


def run_prepare_pipeline(
    *,
    project_root: Path,
    art_path: Path,
    original_filename: str | None = None,
    confirm_overwrite: bool = False,
    run_identity_online: bool = False,
    identity_limit: int | None = DEFAULT_IDENTITY_LIMIT,
) -> dict:
    """Validate upload → verify (www scope) → optional identity online → offline ingest → preview.

    Never writes or overwrites files under What When Where Files/.
    """
    steps: list[dict] = [{"id": "read", "label": "Read ART CSV", "status": "done"}]
    assert_playaevents_art_csv(art_path)
    year = resolve_art_csv_year(art_path, original_filename=original_filename)
    assert_art_csv_matches_year(art_path, year, original_filename=original_filename)
    steps.append({"id": "year", "label": f"Detected year {year}", "status": "done"})

    existing = year_has_outputs(project_root, year)
    if existing and not confirm_overwrite:
        return {
            "ok": False,
            "needs_confirm": True,
            "year": year,
            "error": (
                f"Year {year} already has verification or Aggregator outputs. "
                "Confirm overwrite to continue."
            ),
            "steps": steps
            + [{"id": "overwrite", "label": "Overwrite confirmation required", "status": "blocked"}],
            "cache_inventory": cache_inventory(project_root, year),
        }

    steps.append(
        {
            "id": "overwrite",
            "label": f"Overwrite existing {year} outputs" if existing else "No prior outputs for this year",
            "status": "done",
        }
    )

    staged_www = stage_upload_for_www_loader(art_path, year)
    job_www_dir = staged_www.parent
    steps.append(
        {
            "id": "www",
            "label": "Using uploaded PlayaEvents file (disk library untouched)",
            "status": "done",
        }
    )

    config = load_config(project_root / "config" / "default.yaml")
    verify_dir = project_root / "data" / "verification" / str(year)
    steps.append(
        {
            "id": "verify",
            "label": "Matching History Archive for heroes & proof links",
            "status": "running",
        }
    )
    run_verification(
        config=config,
        year=year,
        www_dir=job_www_dir,
        export_path=None,
        output_dir=verify_dir,
        scope="www",
        validate_images=True,
        check_legacy_links=False,
        output_func=lambda _msg: None,
    )
    steps[-1]["status"] = "done"

    identity_online_ran = False
    if run_identity_online:
        steps.append(
            {
                "id": "identity",
                "label": f"Web search for unclear artist / Burner names (limit {identity_limit or 'all'})",
                "status": "running",
            }
        )
        code = run_identity_resolution(
            project_root=project_root,
            year=year,
            enable_search=True,
            enable_page_fetch=True,
            limit=identity_limit,
            only_needing_search=True,
            aliases_only=False,
            www_dir=job_www_dir,
            output_func=lambda _msg: None,
        )
        if code != 0:
            steps[-1]["status"] = "error"
            steps[-1]["label"] = "Name web search failed (archive match still available)"
        else:
            steps[-1]["status"] = "done"
            identity_online_ran = True
    else:
        steps.append(
            {
                "id": "identity",
                "label": "Using local names only (no web search)",
                "status": "done",
            }
        )

    steps.append({"id": "ingest", "label": "Writing Artelier CSV + gallery preview", "status": "running"})
    paths = run_ingest(
        project_root=project_root,
        year=year,
        www_file=staged_www,
        fetch_missing_heroes=False,
        user_agent=config.user_agent,
        original_filename=original_filename or staged_www.name,
    )
    steps[-1]["status"] = "done"

    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    checklist = {}
    view_path = paths.get("view")
    if view_path and view_path.exists():
        view = json.loads(view_path.read_text(encoding="utf-8"))
        checklist = view.get("upload_checklist") or {}

    steps.append(
        {
            "id": "done",
            "label": (
                f"Ready — {summary.get('project_count')} projects, "
                f"{summary.get('upload_ready_count')} upload-ready"
            ),
            "status": "done",
        }
    )

    return {
        "ok": True,
        "year": year,
        "project_count": summary.get("project_count"),
        "checklist": checklist,
        "summary": summary,
        "uploaded_art_path": str(art_path),
        "saved_www_path": "",
        "viewer_reload": f"/api/view?year={year}",
        "paths": {key: str(value) for key, value in paths.items()},
        "steps": steps,
        "overwrote": existing,
        "identity_online_ran": identity_online_ran,
        "cache_inventory": cache_inventory(project_root, year),
        "download_path": f"/api/download-upload?year={year}",
        "www_library_untouched": True,
    }
