from __future__ import annotations

import csv
import json
from pathlib import Path

from burning_man_scraper.record_parser import normalize_title
from burning_man_scraper.verification.www_loader import load_www_art_csv, load_www_records


def load_verification_by_key(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    by_key: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            uid = (row.get("archive_uid") or row.get("www_uid") or "").strip()
            title = normalize_title(row.get("project_title") or "")
            year = (row.get("year") or "").strip()
            if uid:
                by_key[f"uid:{uid}"] = row
            if title:
                by_key.setdefault(f"title:{year}:{title}", row)
    return by_key


def load_archive_by_key(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_key: dict[str, dict] = {}
    for record in payload.get("records", []):
        uid = (record.get("uid") or "").strip()
        title = record.get("normalized_title") or normalize_title(record.get("title") or "")
        year = str(record.get("year") or "").strip()
        if uid:
            by_key[f"uid:{uid}"] = record
        if title:
            by_key.setdefault(f"title:{year}:{title}", record)
    return by_key


def load_image_manifest_by_key(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_key: dict[str, dict] = {}
    for entry in payload.get("images", []):
        uid = (entry.get("archive_uid") or "").strip()
        title = normalize_title(entry.get("project_title") or "")
        if uid:
            by_key[f"uid:{uid}"] = entry
        if title:
            by_key.setdefault(f"title:{title}", entry)
    return by_key


def load_collector_exports(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else payload.get("records", payload.get("items", []))
    by_key: dict[str, dict] = {}
    for item in items:
        mapped = item.get("mapped_artelier_values") or {}
        original = item.get("original_scraped_values") or {}
        title = normalize_title(
            mapped.get("project_title") or original.get("title") or item.get("project_title") or ""
        )
        year = str(mapped.get("project_year") or original.get("year") or "").strip()
        uid = (
            original.get("uid")
            or original.get("archive_uid")
            or item.get("archive_uid")
            or ""
        ).strip()
        bundle = {"mapped": mapped, "original": original, "item": item}
        if uid:
            by_key[f"uid:{uid}"] = bundle
        if title:
            by_key.setdefault(f"title:{year}:{title}", bundle)
    return by_key


def lookup(index: dict, *, uid: str | None, year: int | str | None, title: str | None):
    if uid:
        hit = index.get(f"uid:{uid}")
        if hit is not None:
            return hit
    if title:
        normalized = normalize_title(title)
        year_text = str(year or "").strip()
        hit = index.get(f"title:{year_text}:{normalized}")
        if hit is not None:
            return hit
        hit = index.get(f"title:{normalized}")
        if hit is not None:
            return hit
    return None


def lookup_identity(
    identity_index: dict,
    *,
    uid: str | None,
    year: int | str | None,
    title: str | None,
) -> tuple[dict | None, str]:
    """Return (row, match_mode) where match_mode is uid|title|''."""
    if uid:
        hit = identity_index.get(f"uid:{uid}")
        if hit is not None:
            return hit, "uid"
    if title:
        normalized = normalize_title(title)
        year_text = str(year or "").strip()
        conflict_key = f"title_conflict:{year_text}:{normalized}"
        if identity_index.get(conflict_key):
            return None, ""
        hit = identity_index.get(f"title:{year_text}:{normalized}")
        if hit is not None:
            return hit, "title"
    return None, ""


def load_identity_by_key(csv_path: Path, json_path: Path | None = None) -> dict[str, dict]:
    """Load identity_report rows keyed by uid and title; mark conflicting title matches."""
    rows: list[dict] = []
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    elif json_path and json_path.exists():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            rows = payload
        else:
            rows = payload.get("results") or payload.get("records") or payload.get("items") or []

    by_key: dict[str, dict] = {}
    for row in rows:
        mapped = dict(row)
        uid = (mapped.get("archive_uid") or mapped.get("uid") or "").strip()
        title = normalize_title(mapped.get("project_title") or mapped.get("title") or "")
        year = str(mapped.get("year") or "").strip()
        if uid:
            by_key[f"uid:{uid}"] = mapped
        if title and year:
            title_key = f"title:{year}:{title}"
            existing = by_key.get(title_key)
            if existing is None:
                by_key[title_key] = mapped
            else:
                existing_uid = (existing.get("archive_uid") or existing.get("uid") or "").strip()
                existing_legal = (existing.get("legal_name") or "").strip().lower()
                new_legal = (mapped.get("legal_name") or "").strip().lower()
                if (uid and existing_uid and uid != existing_uid) or (
                    existing_legal and new_legal and existing_legal != new_legal
                ):
                    by_key[f"title_conflict:{year}:{title}"] = {"conflict": True}
                    by_key.pop(title_key, None)
    return by_key


def default_www_dir(project_root: Path) -> Path:
    return project_root.parent / "What When Where Files"


def aggregator_previews_dir(project_root: Path) -> Path:
    """Derived Aggregator gallery previews live next to WWW templates (not mixed into ART CSVs)."""
    return default_www_dir(project_root) / "aggregator_previews"


def aggregator_preview_path(project_root: Path, year: int) -> Path:
    return aggregator_previews_dir(project_root) / f"aggregator_view_{year}.json"


def bm_ingest_preview_path(project_root: Path, year: int) -> Path:
    return project_root / "data" / "bm_ingest" / str(year) / f"aggregator_view_{year}.json"


def default_verification_dir(project_root: Path, year: int) -> Path:
    return project_root / "data" / "verification" / str(year)


def default_export_path(project_root: Path, year: int) -> Path:
    return (
        project_root
        / "data"
        / "exports"
        / "burning_man"
        / str(year)
        / "consolidated"
        / f"burning_man_{year}_all_completed.json"
    )


def cache_inventory(project_root: Path, year: int) -> dict[str, bool]:
    verification_dir = default_verification_dir(project_root, year)
    return {
        "verification_report": (verification_dir / f"verification_report_{year}.csv").exists(),
        "identity_report": (verification_dir / f"identity_report_{year}.csv").exists()
        or (verification_dir / f"identity_report_{year}.json").exists(),
        "archive_index": (verification_dir / f"archive_index_{year}.json").exists(),
        "image_manifest": (verification_dir / f"image_manifest_{year}.json").exists(),
        "collector_export": default_export_path(project_root, year).exists(),
    }


def load_year_sources(
    *,
    project_root: Path,
    year: int,
    www_dir: Path | None = None,
    www_file: Path | None = None,
) -> dict:
    if www_file is not None:
        www = load_www_art_csv(www_file, year)
    else:
        www = load_www_records(www_dir or default_www_dir(project_root), year=year)
    verification_dir = default_verification_dir(project_root, year)
    identity = load_identity_by_key(
        verification_dir / f"identity_report_{year}.csv",
        verification_dir / f"identity_report_{year}.json",
    )
    return {
        "www": www,
        "verification": load_verification_by_key(verification_dir / f"verification_report_{year}.csv"),
        "identity": identity,
        "archive": load_archive_by_key(verification_dir / f"archive_index_{year}.json"),
        "images": load_image_manifest_by_key(verification_dir / f"image_manifest_{year}.json"),
        "collector": load_collector_exports(default_export_path(project_root, year)),
        "verification_dir": verification_dir,
        "cache_inventory": cache_inventory(project_root, year),
    }
