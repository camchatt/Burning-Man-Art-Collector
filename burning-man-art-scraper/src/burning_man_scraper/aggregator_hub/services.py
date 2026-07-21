from __future__ import annotations

import csv
import io
import json
import re
import shutil
import time
from pathlib import Path

from burning_man_scraper.artelier_schema import ImportSchema, load_import_schema, validate_artelier_row
from burning_man_scraper.bm_ingest.schema import BM_EXTENSION_HEADERS
from burning_man_scraper.bm_ingest.view_bundle import is_row_upload_ready
from burning_man_scraper.url_utils import encode_http_url


_UPLOAD_URL_FIELDS = (
    "hero_image_url",
    "contributor_website",
    "proof_external_url",
    "bm_hero_image_source_url",
    "hero_image_source_page",
    "artist_website",
    "source_record_url",
)


def encode_row_http_urls(row: dict[str, str]) -> dict[str, str]:
    """Percent-encode http(s) URL cells so deploy/download CSVs are fetch-safe."""
    out = dict(row)
    for field in _UPLOAD_URL_FIELDS:
        value = (out.get(field) or "").strip()
        if value:
            out[field] = encode_http_url(value)
    image_urls = (out.get("image_urls") or "").strip()
    if image_urls:
        parts: list[str] = []
        for part in image_urls.split("|"):
            piece = part.strip()
            if piece:
                parts.append(encode_http_url(piece))
        out["image_urls"] = "|".join(parts)
    return out


def _slugify_filter_label(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (label or "filtered").strip().lower()).strip("_")
    return slug or "filtered"


def resolve_ingest_csv(project_root: Path, year: int, *, kind: str = "upload") -> Path | None:
    prefer_bm = kind != "core"
    paths: list[Path] = []
    if prefer_bm:
        paths.append(project_root / "data" / "deploy" / str(year) / f"artelier_bm_upload_{year}.csv")
        paths.append(project_root / "data" / "bm_ingest" / str(year) / f"artelier_bm_upload_{year}.csv")
    paths.append(project_root / "data" / "deploy" / str(year) / f"artelier_core_only_{year}.csv")
    paths.append(project_root / "data" / "bm_ingest" / str(year) / f"artelier_core_only_{year}.csv")
    return next((candidate for candidate in paths if candidate.exists()), None)


def row_export_keys(row: dict[str, str]) -> set[str]:
    keys: set[str] = set()
    for field in ("bm_uid", "project_slug", "project_title"):
        value = (row.get(field) or "").strip()
        if value:
            keys.add(value)
    return keys


def export_filtered_csv(
    project_root: Path,
    *,
    year: int = 0,
    run_id: str = "",
    keys: list[str],
    kind: str = "upload",
    filter_id: str = "all",
    filter_label: str = "All projects",
    unfiltered: bool = False,
) -> dict:
    """Filter an Artelier CSV to the keys shown in the gallery (uid / slug / title)."""
    if run_id:
        from burning_man_scraper.sources.run_store import resolve_run_csv

        source = resolve_run_csv(project_root, run_id)
        if source is None:
            raise FileNotFoundError(f"CSV not found for run {run_id}")
        kind = "core"
    else:
        source = resolve_ingest_csv(project_root, year, kind=kind)
        if source is None:
            raise FileNotFoundError(f"CSV not found for year {year}")

    wanted = {key.strip() for key in keys if key and str(key).strip()}
    if not wanted:
        raise ValueError("No projects match the current filter/search to export.")

    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames:
            raise ValueError(f"CSV has no header: {source}")
        use_core_headers = bool(run_id)
        schema_path = project_root / "config" / "artelier_import_schema.yaml"
        if use_core_headers and schema_path.exists():
            schema = load_import_schema(schema_path)
            fieldnames = list(schema.headers)
        matched_rows: list[dict[str, str]] = []
        for row in reader:
            if row_export_keys(row) & wanted:
                encoded = encode_row_http_urls(row)
                if use_core_headers and schema_path.exists():
                    matched_rows.append({header: encoded.get(header, "") for header in fieldnames})
                else:
                    matched_rows.append(encoded)

    if not matched_rows:
        raise ValueError("No CSV rows matched the filtered projects.")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(matched_rows)
    content = buffer.getvalue().encode("utf-8")

    base = source.stem
    if unfiltered and filter_id in {"", "all"}:
        filename = f"{base}.csv"
    else:
        filename = f"{base}_{_slugify_filter_label(filter_label)}.csv"

    return {
        "ok": True,
        "year": year,
        "run_id": run_id,
        "kind": kind,
        "filter_id": filter_id,
        "filter_label": filter_label,
        "source": str(source),
        "filename": filename,
        "row_count": len(matched_rows),
        "content": content,
    }


def validate_core_csv(
    project_root: Path,
    year: int = 0,
    *,
    run_id: str = "",
    max_errors: int = 25,
    upload_ready_only: bool = True,
) -> dict:
    schema = load_import_schema(project_root / "config" / "artelier_import_schema.yaml")
    if run_id:
        from burning_man_scraper.sources.run_store import resolve_run_csv

        path = resolve_run_csv(project_root, run_id)
        if path is None:
            return {
                "ok": False,
                "year": year,
                "run_id": run_id,
                "path": "",
                "row_count": 0,
                "error_count": 1,
                "errors": [{"row": 0, "field": "file", "messages": [f"Missing ingest CSV for run {run_id}"]}],
                "upload_ready_only": upload_ready_only,
            }
        result = validate_core_csv_path(
            path,
            schema,
            year=year or None,
            max_errors=max_errors,
            upload_ready_only=upload_ready_only,
        )
        result["run_id"] = run_id
        return result

    path = project_root / "data" / "bm_ingest" / str(year) / f"artelier_bm_upload_{year}.csv"
    if not path.exists():
        path = project_root / "data" / "bm_ingest" / str(year) / f"artelier_core_only_{year}.csv"
    if not path.exists():
        return {
            "ok": False,
            "year": year,
            "path": str(path),
            "row_count": 0,
            "error_count": 1,
            "errors": [{"row": 0, "field": "file", "messages": [f"Missing ingest CSV for {year}"]}],
            "upload_ready_only": upload_ready_only,
        }
    return validate_core_csv_path(
        path,
        schema,
        year=year,
        max_errors=max_errors,
        upload_ready_only=upload_ready_only,
    )


def validate_core_csv_path(
    path: Path,
    schema: ImportSchema,
    *,
    year: int | None = None,
    max_errors: int = 25,
    upload_ready_only: bool = False,
) -> dict:
    errors: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        # Accept BM extended CSV (core headers first) or exact core-only CSV.
        if headers[: len(schema.headers)] != schema.headers and headers != schema.headers:
            return {
                "ok": False,
                "year": year,
                "path": str(path),
                "row_count": 0,
                "error_count": 1,
                "skipped_not_ready": 0,
                "errors": [
                    {
                        "row": 0,
                        "field": "headers",
                        "messages": [
                            "CSV headers do not match Artelier import schema order/names.",
                            f"expected {len(schema.headers)} core columns first, got {len(headers)}",
                        ],
                    }
                ],
                "upload_ready_only": upload_ready_only,
            }
        row_count = 0
        skipped_not_ready = 0
        for index, row in enumerate(reader, start=2):
            if upload_ready_only and "review_flags" in (reader.fieldnames or []) and not is_row_upload_ready(row):
                skipped_not_ready += 1
                continue
            row_count += 1
            formatted = {header: (row.get(header) or "") for header in schema.headers}
            validations = validate_artelier_row(formatted, schema)
            failures = [item for item in validations if not item.valid]
            if failures and len(errors) < max_errors:
                errors.append(
                    {
                        "row": index,
                        "field": failures[0].field_name,
                        "messages": [msg for item in failures for msg in item.errors],
                        "title": formatted.get("project_title", ""),
                    }
                )
    return {
        "ok": len(errors) == 0 and row_count > 0,
        "year": year,
        "path": str(path),
        "row_count": row_count,
        "skipped_not_ready": skipped_not_ready,
        "error_count": len(errors),
        "errors": errors,
        "upload_ready_only": upload_ready_only,
    }


def _write_filtered_csv(
    *,
    source: Path,
    dest: Path,
    headers: list[str],
    upload_ready_only: bool,
) -> tuple[int, int]:
    kept = 0
    skipped = 0
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, str]] = []
        for row in reader:
            if upload_ready_only and not is_row_upload_ready(row):
                skipped += 1
                continue
            encoded = encode_row_http_urls(row)
            rows.append({header: (encoded.get(header) or "") for header in headers})
            kept += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return kept, skipped


def prepare_deploy_package(
    project_root: Path,
    year: int,
    *,
    force: bool = False,
    admin_import_url: str = "",
    schema_path: Path | None = None,
    upload_ready_only: bool = True,
) -> dict:
    schema_file = schema_path or (project_root / "config" / "artelier_import_schema.yaml")
    schema = load_import_schema(schema_file)
    bm_src = project_root / "data" / "bm_ingest" / str(year) / f"artelier_bm_upload_{year}.csv"
    core_src = project_root / "data" / "bm_ingest" / str(year) / f"artelier_core_only_{year}.csv"
    source = bm_src if bm_src.exists() else core_src
    if not source.exists():
        validation = {
            "ok": False,
            "year": year,
            "path": str(source),
            "row_count": 0,
            "skipped_not_ready": 0,
            "error_count": 1,
            "errors": [{"row": 0, "field": "file", "messages": [f"Missing ingest CSV for {year}"]}],
            "upload_ready_only": upload_ready_only,
        }
    else:
        validation = validate_core_csv_path(
            source,
            schema,
            year=year,
            upload_ready_only=upload_ready_only,
        )
    if not validation["ok"] and not force:
        return {
            "ok": False,
            "forced": False,
            "validation": validation,
            "deploy_dir": "",
            "core_csv": "",
            "bm_upload_csv": "",
            "admin_import_url": admin_import_url,
            "upload_ready_only": upload_ready_only,
            "message": "Validation failed. Fix errors or retry with export_anyway.",
        }

    deploy_dir = project_root / "data" / "deploy" / str(year)
    if deploy_dir.exists():
        shutil.rmtree(deploy_dir)
    deploy_dir.mkdir(parents=True, exist_ok=True)

    bm_headers = list(schema.headers) + list(BM_EXTENSION_HEADERS)
    bm_dest = deploy_dir / f"artelier_bm_upload_{year}.csv"
    core_dest = deploy_dir / f"artelier_core_only_{year}.csv"
    if bm_src.exists():
        kept, skipped = _write_filtered_csv(
            source=bm_src,
            dest=bm_dest,
            headers=bm_headers,
            upload_ready_only=upload_ready_only,
        )
        _write_filtered_csv(
            source=bm_src,
            dest=core_dest,
            headers=list(schema.headers),
            upload_ready_only=upload_ready_only,
        )
        primary_csv = str(bm_dest)
        primary_download = f"/api/download-upload?year={year}"
    else:
        kept, skipped = _write_filtered_csv(
            source=source,
            dest=core_dest,
            headers=list(schema.headers),
            upload_ready_only=upload_ready_only,
        )
        primary_csv = str(core_dest)
        primary_download = f"/api/download-core?year={year}"
        bm_dest = Path("")

    manifest = {
        "year": year,
        "prepared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "primary_output": "artelier_bm_upload" if bm_src.exists() else "artelier_core_only",
        "bm_upload_csv": str(bm_dest) if bm_src.exists() else "",
        "core_csv": str(core_dest),
        "validation_ok": validation["ok"],
        "forced": force and not validation["ok"],
        "admin_import_url": admin_import_url,
        "row_count": kept,
        "skipped_not_ready": skipped,
        "upload_ready_only": upload_ready_only,
        "instructions": (
            "Primary Burning Man package is artelier_bm_upload (36 Artelier columns + BM extensions). "
            "artelier_core_only is a secondary 36-column slice. "
            "Package includes upload-ready projects only in fast_upload mode."
        ),
    }
    (deploy_dir / "deploy_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "forced": force and not validation["ok"],
        "validation": validation,
        "deploy_dir": str(deploy_dir),
        "bm_upload_csv": str(bm_dest) if bm_src.exists() else "",
        "core_csv": str(core_dest),
        "primary_csv": primary_csv,
        "download_path": primary_download,
        "download_core_path": f"/api/download-core?year={year}",
        "admin_import_url": admin_import_url,
        "row_count": kept,
        "skipped_not_ready": skipped,
        "upload_ready_only": upload_ready_only,
        "message": (
            f"Deploy package ready: {kept} upload-ready row(s) in full BM CSV"
            + (f"; skipped {skipped} needing attention." if skipped else ".")
        ),
    }


def disk_footprint(project_root: Path) -> dict[str, float]:
    data_root = project_root / "data"
    result: dict[str, float] = {}
    if not data_root.exists():
        return result
    for child in sorted(data_root.iterdir()):
        if not child.is_dir():
            continue
        total = sum(path.stat().st_size for path in child.rglob("*") if path.is_file())
        result[child.name] = round(total / 1_000_000, 2)
    result["total_mb"] = round(sum(v for k, v in result.items() if k != "total_mb"), 2)
    return result


def cleanup_temps(project_root: Path, *, preview_max_age_days: int = 14) -> dict:
    removed: list[str] = []
    bytes_freed = 0

    tmp = project_root / "data" / "uploads" / "tmp"
    if tmp.exists():
        for path in tmp.rglob("*"):
            if path.is_file() and path.name != ".gitkeep":
                bytes_freed += path.stat().st_size
                removed.append(str(path.relative_to(project_root)))
        for path in sorted(tmp.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
            elif path.is_file() and path.name != ".gitkeep":
                path.unlink(missing_ok=True)
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / ".gitkeep").write_text("", encoding="utf-8")

    raw_html = project_root / "data" / "previews" / "raw_html"
    cutoff = time.time() - (preview_max_age_days * 86400)
    if raw_html.exists():
        for path in raw_html.rglob("*"):
            if path.is_file() and path.name != ".gitkeep" and path.stat().st_mtime < cutoff:
                bytes_freed += path.stat().st_size
                removed.append(str(path.relative_to(project_root)))
                path.unlink(missing_ok=True)

    for path in project_root.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(str(path.relative_to(project_root)))

    return {
        "removed_count": len(removed),
        "removed_sample": removed[:40],
        "bytes_freed": bytes_freed,
        "mb_freed": round(bytes_freed / 1_000_000, 3),
    }
