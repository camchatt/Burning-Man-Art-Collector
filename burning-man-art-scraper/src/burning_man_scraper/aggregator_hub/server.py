from __future__ import annotations

import json
import shutil
import traceback
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from burning_man_scraper.aggregator_hub.config import load_deploy_config
from burning_man_scraper.aggregator_hub.multipart import parse_multipart
from burning_man_scraper.aggregator_hub.prepare import DEFAULT_IDENTITY_LIMIT, run_prepare_pipeline
from burning_man_scraper.aggregator_hub.services import (
    cleanup_temps,
    disk_footprint,
    export_filtered_csv,
    prepare_deploy_package,
    resolve_ingest_csv,
    validate_core_csv,
)
from burning_man_scraper.bm_ingest.merge import run_ingest
from burning_man_scraper.bm_ingest.sources import cache_inventory
from burning_man_scraper.config import load_config
from burning_man_scraper.verification.www_loader import (
    ArtCsvYearMismatchError,
    assert_art_csv_matches_year,
    assert_playaevents_art_csv,
    resolve_art_csv_year,
)


class AggregatorHubHandler(SimpleHTTPRequestHandler):
    project_root: Path = Path(".")
    workspace_root: Path = Path(".")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.project_root / "viewer" / "aggregator"), **kwargs)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        sys_stderr = __import__("sys").stderr
        sys_stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self._json(self._status_payload())
            return
        if parsed.path == "/api/years":
            self._json({"years": self._list_years()})
            return
        if parsed.path in {"/api/download-upload", "/api/download-core"}:
            qs = parse_qs(parsed.query)
            year = int((qs.get("year") or ["0"])[0])
            kind = "upload" if parsed.path == "/api/download-upload" else "core"
            path = resolve_ingest_csv(self.project_root, year, kind=kind)
            if path is None:
                self._json({"ok": False, "error": "CSV not found"}, status=404)
                return
            self._send_csv(path.read_bytes(), path.name)
            return
        if parsed.path in {"/", "/index.html"}:
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/prepare":
                payload = self._handle_prepare()
                status = 409 if payload.get("needs_confirm") else 200
                self._json(payload, status=status)
                return
            if parsed.path == "/api/ingest":
                payload = self._handle_ingest()
                status = 409 if payload.get("needs_confirm") else 200
                self._json(payload, status=status)
                return
            if parsed.path == "/api/inspect-csv":
                self._json(self._handle_inspect_csv())
                return
            if parsed.path == "/api/validate-upload":
                self._json(self._handle_validate())
                return
            if parsed.path == "/api/prepare-deploy":
                self._json(self._handle_deploy())
                return
            if parsed.path == "/api/cleanup":
                deploy_cfg = load_deploy_config(self.project_root)
                result = cleanup_temps(
                    self.project_root,
                    preview_max_age_days=int(deploy_cfg.get("preview_html_max_age_days") or 14),
                )
                result["disk"] = disk_footprint(self.project_root)
                self._json(result)
                return
            if parsed.path == "/api/load-year":
                self._json(self._handle_load_year())
                return
            if parsed.path == "/api/export-csv":
                self._handle_export_csv()
                return
            self._json({"ok": False, "error": f"Unknown endpoint {parsed.path}"}, status=404)
        except ArtCsvYearMismatchError as exc:
            self._json({"ok": False, "error": str(exc)}, status=400)
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self._json(
                {"ok": False, "error": str(exc), "trace": traceback.format_exc().splitlines()[-8:]},
                status=500,
            )

    def _status_payload(self) -> dict:
        deploy_cfg = load_deploy_config(self.project_root)
        years = self._list_years()
        latest = years[0] if years else None
        summary = None
        if latest is not None:
            summary_path = self.project_root / "data" / "bm_ingest" / str(latest) / f"ingest_summary_{latest}.json"
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "years": years,
            "latest_year": latest,
            "latest_summary": summary,
            "year_summaries": self._year_summaries(years),
            "disk": disk_footprint(self.project_root),
            "admin_import_url": deploy_cfg.get("admin_import_url") or "",
            "viewer_data": "./data/aggregator_view.json",
        }

    def _year_summaries(self, years: list[int]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for year in years:
            summary_path = self.project_root / "data" / "bm_ingest" / str(year) / f"ingest_summary_{year}.json"
            if summary_path.exists():
                out[str(year)] = json.loads(summary_path.read_text(encoding="utf-8"))
        return out

    def _existing_year_payload(self, year: int) -> dict | None:
        ingest_dir = self.project_root / "data" / "bm_ingest" / str(year)
        summary_path = ingest_dir / f"ingest_summary_{year}.json"
        if not ingest_dir.exists():
            return None
        has_outputs = any(ingest_dir.glob("*.csv")) or summary_path.exists()
        if not has_outputs:
            return None
        summary = None
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return {
            "year": year,
            "path": str(ingest_dir),
            "project_count": (summary or {}).get("project_count"),
            "upload_ready_count": (summary or {}).get("upload_ready_count"),
            "with_hero_image": (summary or {}).get("with_hero_image"),
        }

    def _list_years(self) -> list[int]:
        root = self.project_root / "data" / "bm_ingest"
        if not root.exists():
            return []
        years: list[int] = []
        for child in root.iterdir():
            if child.is_dir() and child.name.isdigit():
                years.append(int(child.name))
        return sorted(years, reverse=True)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _parse_upload(self) -> tuple[dict[str, str], dict]:
        content_type = self.headers.get("Content-Type") or ""
        body = self._read_body()
        if "multipart/form-data" in content_type:
            return parse_multipart(body, content_type)
        payload = json.loads(body.decode("utf-8") or "{}")
        fields = {k: str(v) for k, v in payload.items() if k != "content_base64"}
        files: dict = {}
        if payload.get("content_base64"):
            import base64

            files["file"] = type(
                "F",
                (),
                {
                    "filename": payload.get("filename") or "upload.csv",
                    "content": base64.b64decode(payload["content_base64"]),
                },
            )()
        return fields, files

    def _write_upload(self, upload, year_hint: int | None = None) -> tuple[Path, Path, str]:
        tmp_root = self.project_root / "data" / "uploads" / "tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        job_dir = tmp_root / str(uuid.uuid4())
        job_dir.mkdir(parents=True, exist_ok=True)
        original_name = getattr(upload, "filename", None) or "upload.csv"
        safe_name = Path(original_name).name or "upload.csv"
        art_path = job_dir / safe_name
        art_path.write_bytes(upload.content)
        return job_dir, art_path, original_name

    def _handle_inspect_csv(self) -> dict:
        fields, files = self._parse_upload()
        upload = files.get("file") or files.get("art_csv")
        if upload is None or not getattr(upload, "content", b""):
            raise ValueError("ART CSV file is required")
        job_dir, art_path, original_name = self._write_upload(upload)
        try:
            assert_playaevents_art_csv(art_path)
            year = resolve_art_csv_year(art_path, original_filename=original_name)
            existing = self._existing_year_payload(year)
            inventory = cache_inventory(self.project_root, year)
            with art_path.open("r", encoding="utf-8-sig", newline="") as handle:
                # Count data rows after header for preflight display.
                row_count = max(sum(1 for _ in handle) - 1, 0)
            return {
                "ok": True,
                "detected_source": "Burning Man Art",
                "year": year,
                "rows": row_count,
                "filename": original_name,
                "already_processed": existing is not None,
                "existing": existing,
                "cache_inventory": inventory,
                "processing_mode": "fast_upload",
                "network_identity_search": False,
                "processing_plan": {
                    "detected_source": "Burning Man Art",
                    "year": year,
                    "rows": row_count,
                    "cached_sources": inventory,
                    "processing_mode": "cache-first",
                    "identity_fallback": "local",
                    "network_identity_search": "disabled",
                },
                "message": (
                    f"{row_count} projects for Burning Man {year}. "
                    "Upload is used for this run only; What When Where Files stay untouched."
                    + (
                        " Aggregator outputs for this year already exist — confirm rebuild in step 2."
                        if existing
                        else ""
                    )
                ),
            }
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def _handle_prepare(self) -> dict:
        deploy_cfg = load_deploy_config(self.project_root)
        fields, files = self._parse_upload()
        upload = files.get("file") or files.get("art_csv")
        if upload is None or not getattr(upload, "content", b""):
            raise ValueError("ART CSV file is required")

        confirm_overwrite = fields.get("confirm_overwrite", "") in {"1", "true", "True", "yes"}
        run_identity_online = fields.get("run_identity_online", "") in {"1", "true", "True", "yes"}
        identity_limit_raw = fields.get("identity_limit") or str(DEFAULT_IDENTITY_LIMIT)
        try:
            identity_limit = int(identity_limit_raw)
        except ValueError:
            identity_limit = DEFAULT_IDENTITY_LIMIT
        if identity_limit <= 0:
            identity_limit = None

        job_dir, art_path, original_name = self._write_upload(upload)
        try:
            result = run_prepare_pipeline(
                project_root=self.project_root,
                art_path=art_path,
                original_filename=original_name,
                confirm_overwrite=confirm_overwrite,
                run_identity_online=run_identity_online,
                identity_limit=identity_limit,
            )
            if result.get("ok") and deploy_cfg.get("cleanup_tmp_on_success"):
                shutil.rmtree(job_dir, ignore_errors=True)
            elif not result.get("ok"):
                shutil.rmtree(job_dir, ignore_errors=True)
            result["disk"] = disk_footprint(self.project_root)
            return result
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

    def _handle_ingest(self) -> dict:
        deploy_cfg = load_deploy_config(self.project_root)
        fields, files = self._parse_upload()
        upload = files.get("file") or files.get("art_csv")
        if upload is None or not getattr(upload, "content", b""):
            raise ValueError("ART CSV file is required")

        confirm_overwrite = fields.get("confirm_overwrite", "") in {"1", "true", "True", "yes"}
        job_dir, art_path, original_name = self._write_upload(upload)
        steps: list[dict] = [{"id": "read", "label": "Read ART CSV", "status": "done"}]

        try:
            year = resolve_art_csv_year(art_path, original_filename=original_name)
            steps.append({"id": "year", "label": f"Detected year {year}", "status": "done"})

            # Optional sanity: if client sent a year, it must match CSV.
            form_year = int(fields.get("year") or 0) if fields.get("year") else 0
            if form_year and form_year != year:
                raise ArtCsvYearMismatchError(
                    f"ART CSV looks like year {year} but UI year is {form_year}"
                )

            existing = self._existing_year_payload(year)
            if existing and not confirm_overwrite:
                return {
                    "ok": False,
                    "needs_confirm": True,
                    "year": year,
                    "error": (
                        f"Year {year} was already processed "
                        f"({existing.get('project_count') or '?'} projects). "
                        "Confirm overwrite to replace existing Aggregator outputs."
                    ),
                    "existing": existing,
                    "steps": steps
                    + [{"id": "overwrite", "label": "Overwrite confirmation required", "status": "blocked"}],
                }

            if existing:
                steps.append({"id": "overwrite", "label": f"Overwrite existing {year} outputs", "status": "done"})
            else:
                steps.append({"id": "overwrite", "label": "No prior outputs for this year", "status": "done"})

            assert_playaevents_art_csv(art_path)
            assert_art_csv_matches_year(art_path, year, original_filename=original_name)
            steps.append(
                {
                    "id": "www",
                    "label": "Using uploaded template for this run (library left untouched)",
                    "status": "done",
                }
            )

            steps.append({"id": "merge", "label": "Running offline merge + hero resolve", "status": "running"})
            scraper_cfg = load_config(self.project_root / "config" / "default.yaml")
            paths = run_ingest(
                project_root=self.project_root,
                year=year,
                www_file=art_path,
                fetch_missing_heroes=False,
                user_agent=scraper_cfg.user_agent,
                original_filename=original_name,
            )
            steps[-1]["status"] = "done"
            steps.append({"id": "write", "label": "Wrote Artelier CSVs + Aggregator preview", "status": "done"})

            if deploy_cfg.get("cleanup_tmp_on_success"):
                shutil.rmtree(job_dir, ignore_errors=True)

            summary_path = paths["summary"]
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            checklist = {}
            view_path = paths.get("view")
            if view_path and view_path.exists():
                view = json.loads(view_path.read_text(encoding="utf-8"))
                checklist = view.get("upload_checklist") or {}

            steps.append(
                {
                    "id": "done",
                    "label": (
                        f"Complete — {summary.get('project_count')} projects, "
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
                "saved_www_path": "",
                "uploaded_art_path": str(art_path),
                "viewer_reload": "./data/aggregator_view.json",
                "paths": {key: str(value) for key, value in paths.items()},
                "disk": disk_footprint(self.project_root),
                "steps": steps,
                "overwrote": bool(existing),
                "www_library_untouched": True,
            }
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

    def _handle_validate(self) -> dict:
        body = json.loads(self._read_body().decode("utf-8") or "{}")
        year = int(body.get("year") or 0)
        result = validate_core_csv(self.project_root, year, upload_ready_only=True)
        result["ok_flag"] = result["ok"]
        return result

    def _handle_deploy(self) -> dict:
        body = json.loads(self._read_body().decode("utf-8") or "{}")
        year = int(body.get("year") or 0)
        force = bool(body.get("export_anyway"))
        deploy_cfg = load_deploy_config(self.project_root)
        return prepare_deploy_package(
            self.project_root,
            year,
            force=force,
            admin_import_url=str(deploy_cfg.get("admin_import_url") or ""),
            upload_ready_only=True,
        )

    def _handle_load_year(self) -> dict:
        body = json.loads(self._read_body().decode("utf-8") or "{}")
        year = int(body.get("year") or 0)
        source = self.project_root / "data" / "bm_ingest" / str(year) / f"aggregator_view_{year}.json"
        if not source.exists():
            return {"ok": False, "error": f"No aggregator view for {year}"}
        target_dir = self.project_root / "viewer" / "aggregator" / "data"
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_dir / "aggregator_view.json")
        shutil.copy2(source, target_dir / f"aggregator_view_{year}.json")
        return {"ok": True, "year": year, "viewer_reload": "./data/aggregator_view.json"}

    def _handle_export_csv(self) -> None:
        body = json.loads(self._read_body().decode("utf-8") or "{}")
        year = int(body.get("year") or 0)
        if not year:
            raise ValueError("year is required")
        kind = str(body.get("kind") or "upload")
        if kind not in {"upload", "core"}:
            raise ValueError("kind must be 'upload' or 'core'")
        keys = body.get("keys") or []
        if not isinstance(keys, list):
            raise ValueError("keys must be a list of project identifiers")
        result = export_filtered_csv(
            self.project_root,
            year=year,
            keys=[str(key) for key in keys],
            kind=kind,
            filter_id=str(body.get("filter_id") or "all"),
            filter_label=str(body.get("filter_label") or "All projects"),
            unfiltered=bool(body.get("unfiltered")),
        )
        self._send_csv(result["content"], result["filename"])

    def _send_csv(self, data: bytes, filename: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def create_handler(project_root: Path):
    class BoundHandler(AggregatorHubHandler):
        pass

    BoundHandler.project_root = project_root
    BoundHandler.workspace_root = project_root.parent
    return BoundHandler


def serve(project_root: Path, *, port: int = 8765) -> None:
    handler = create_handler(project_root)

    class HubHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = False

    try:
        server = HubHTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise SystemExit(
            f"Could not bind http://127.0.0.1:{port}/ ({exc}). "
            "Stop the other Aggregator hub process (or free that port), then try again."
        ) from exc
    print(f"Aggregator hub: http://127.0.0.1:{port}/")
    print(f"Project root: {project_root}")
    server.serve_forever()
