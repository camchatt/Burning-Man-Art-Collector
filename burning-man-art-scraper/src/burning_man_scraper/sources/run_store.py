"""Immutable run folders for Artelier Aggregator preparation jobs."""

from __future__ import annotations

import csv
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from burning_man_scraper.bm_ingest.view_bundle import build_aggregator_view
from burning_man_scraper.sources.artelier_map import artelier_headers, export_blockers_for_row
from burning_man_scraper.sources.base import NormalizedRecord


def runs_root(project_root: Path) -> Path:
    path = project_root / "data" / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_run_id(source_id: str, label: str = "") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", (label or source_id).casefold()).strip("-")[:40] or source_id
    return f"{source_id}_{slug}_{stamp}_{uuid.uuid4().hex[:8]}"


def run_dir(project_root: Path, run_id: str) -> Path:
    return runs_root(project_root) / run_id


def create_run(
    project_root: Path,
    *,
    source_id: str,
    label: str,
    input_summary: dict[str, Any],
) -> Path:
    run_id = new_run_id(source_id, label)
    path = run_dir(project_root, run_id)
    path.mkdir(parents=True, exist_ok=False)
    (path / "raw").mkdir(exist_ok=True)
    (path / "evidence").mkdir(exist_ok=True)
    manifest = {
        "run_id": run_id,
        "source_id": source_id,
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "created",
        "progress": {"percent": 0, "phase": "created", "message": "Run created"},
        "input": input_summary,
        "unsupported_relationships": [],
        "paths": {},
    }
    write_manifest(path, manifest)
    return path


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    (path / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads((path / "run_manifest.json").read_text(encoding="utf-8"))


def update_progress(path: Path, *, percent: int, phase: str, message: str, status: str | None = None) -> None:
    manifest = load_manifest(path)
    manifest["progress"] = {"percent": percent, "phase": phase, "message": message}
    if status:
        manifest["status"] = status
    write_manifest(path, manifest)


def write_normalized_records(path: Path, records: list[NormalizedRecord]) -> Path:
    payload = [record.to_dict() for record in records]
    out = path / "normalized_records.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def load_normalized_records(path: Path) -> list[dict[str, Any]]:
    file_path = path / "normalized_records.json"
    if not file_path.exists():
        return []
    return json.loads(file_path.read_text(encoding="utf-8"))


def write_artelier_outputs(
    path: Path,
    project_root: Path,
    *,
    artelier_rows: list[dict[str, str]],
    label: str,
    unsupported_relationships: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    headers = artelier_headers(project_root)
    core_path = path / "artelier_core_only.csv"
    review_path = path / "review_queue.csv"
    view_path = path / "aggregator_view.json"
    summary_path = path / "ingest_summary.json"

    with core_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in artelier_rows:
            writer.writerow({header: row.get(header, "") for header in headers})

    review_rows = []
    for row in artelier_rows:
        blockers = export_blockers_for_row(row)
        if blockers or (row.get("review_flags") or "").strip():
            review_rows.append(row)
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers + ["export_blockers"], extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in review_rows:
            payload = dict(row)
            payload["export_blockers"] = "|".join(export_blockers_for_row(row))
            writer.writerow(payload)

    # Reuse gallery builder; encode synthetic year 0 for non-BM runs.
    view = build_aggregator_view(year=0, rows=artelier_rows)
    view["schema_version"] = "aggregator-view-v2"
    view["meta"]["run_label"] = label
    view["meta"]["source_id"] = load_manifest(path).get("source_id")
    view["meta"]["run_id"] = path.name
    view["meta"]["about"] = (
        f"Artelier Aggregator review for {label}. "
        "Correct uncertain fields, then download export-ready Artelier CSV rows."
    )
    for project, row in zip(view["projects"], artelier_rows):
        blockers = export_blockers_for_row(row)
        project["export_blockers"] = blockers
        project["export_blocked_reason"] = (
            ", ".join(blockers).replace("_", " ") if blockers else ""
        )
        project["collection"] = row.get("project_context_tags") or ""
        project["project_type"] = row.get("project_type") or ""
        project["approval_status"] = row.get("approval_status") or "draft"
        project["evidence"] = {
            "proof_url": row.get("proof_external_url") or "",
            "proof_description": row.get("proof_description") or "",
            "source_provenance": (row.get("source_provenance") or "").split("|")
            if row.get("source_provenance")
            else [],
            "hero_source_page": row.get("hero_image_source_page") or "",
        }
        # Prefer location over playa for generic sources.
        if not project["place"].get("playa_address"):
            project["place"]["display"] = row.get("project_location") or ""
        else:
            project["place"]["display"] = project["place"]["playa_address"]
    view_path.write_text(json.dumps(view, indent=2), encoding="utf-8")

    checklist = view["upload_checklist"]
    summary = {
        "schema_version": "artelier-aggregator-run-v1",
        "run_id": path.name,
        "label": label,
        "project_count": len(artelier_rows),
        "upload_ready_count": checklist["upload_ready_count"],
        "needs_attention_count": checklist["needs_attention_count"],
        "with_hero_image": checklist["with_hero_image"],
        "missing_proof_count": checklist["missing_proof_count"],
        "unsupported_relationships": unsupported_relationships or [],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    manifest = load_manifest(path)
    manifest["status"] = "ready"
    manifest["progress"] = {"percent": 100, "phase": "ready", "message": "Review ready"}
    manifest["unsupported_relationships"] = unsupported_relationships or []
    manifest["summary"] = summary
    manifest["paths"] = {
        "core": str(core_path),
        "review": str(review_path),
        "view": str(view_path),
        "summary": str(summary_path),
        "normalized": str(path / "normalized_records.json"),
    }
    write_manifest(path, manifest)

    return {
        "core": core_path,
        "review": review_path,
        "view": view_path,
        "summary": summary_path,
    }


def list_runs(project_root: Path) -> list[dict[str, Any]]:
    root = runs_root(project_root)
    runs: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        manifest_path = child / "run_manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        runs.append(
            {
                "run_id": child.name,
                "source_id": manifest.get("source_id"),
                "label": manifest.get("label"),
                "status": manifest.get("status"),
                "created_at": manifest.get("created_at"),
                "summary": manifest.get("summary") or {},
            }
        )
    return runs


def resolve_run_view(project_root: Path, run_id: str) -> Path | None:
    path = run_dir(project_root, run_id) / "aggregator_view.json"
    return path if path.exists() else None


def resolve_run_csv(project_root: Path, run_id: str) -> Path | None:
    path = run_dir(project_root, run_id) / "artelier_core_only.csv"
    return path if path.exists() else None


def apply_record_corrections(
    project_root: Path,
    run_id: str,
    *,
    record_id: str,
    corrections: dict[str, Any],
) -> dict[str, Any]:
    path = run_dir(project_root, run_id)
    records = load_normalized_records(path)
    if not records:
        raise ValueError(f"No normalized records for run {run_id}")

    target = None
    for record in records:
        if record.get("record_id") == record_id:
            target = record
            break
        artelier = record.get("artelier_row") or {}
        if artelier.get("project_slug") == record_id or artelier.get("project_title") == record_id:
            target = record
            break
    if target is None:
        raise ValueError(f"Record not found: {record_id}")

    field_map = {
        "project_title": "project_title",
        "contributor_name": "contributor_name",
        "project_year": "project_year",
        "project_location": "project_location",
        "project_type": "project_type",
        "collection": "collection",
        "hero_image_url": "hero_image_url",
        "approval_status": None,
    }
    artelier = dict(target.get("artelier_row") or {})
    for key, value in corrections.items():
        if key == "approval_status":
            target["approval_status"] = str(value)
            artelier["approval_status"] = str(value)
            continue
        if key not in field_map:
            continue
        fv = target.get(key) if isinstance(target.get(key), dict) else None
        if fv is not None:
            fv["value"] = str(value)
            fv["status"] = "corrected"
            fv["confidence"] = "high"
        # Map into artelier row columns.
        artelier_key = {
            "project_title": "project_title",
            "contributor_name": "contributor_name",
            "project_year": "project_year",
            "project_location": "project_location",
            "project_type": "project_type",
            "collection": "project_context_tags",
            "hero_image_url": "hero_image_url",
        }.get(key)
        if artelier_key:
            artelier[artelier_key] = str(value)
        if key == "project_title":
            from burning_man_scraper.sources.artelier_map import slugify

            artelier["project_slug"] = slugify(str(value))
            artelier["proof_title"] = str(value)

    # Clear sparse/incomplete flags when core fields are filled by a human.
    flags = [flag for flag in (target.get("review_flags") or []) if flag]
    if artelier.get("hero_image_url"):
        flags = [flag for flag in flags if flag != "hero_missing"]
    if artelier.get("contributor_name"):
        flags = [flag for flag in flags if flag != "missing_attribution"]
    if artelier.get("project_title") and artelier.get("proof_external_url"):
        flags = [flag for flag in flags if flag not in {"incomplete_fields", "sparse_evidence"}]
    target["review_flags"] = flags
    artelier["review_flags"] = "|".join(flags)
    target["export_blockers"] = export_blockers_for_row(artelier)
    target["artelier_row"] = artelier

    (path / "normalized_records.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    rows = [dict(rec.get("artelier_row") or {}) for rec in records]
    for row, rec in zip(rows, records):
        row["review_flags"] = "|".join(rec.get("review_flags") or [])
    write_artelier_outputs(
        path,
        project_root,
        artelier_rows=rows,
        label=load_manifest(path).get("label") or run_id,
        unsupported_relationships=load_manifest(path).get("unsupported_relationships") or [],
    )
    return target
