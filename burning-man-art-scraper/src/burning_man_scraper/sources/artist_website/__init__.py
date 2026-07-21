"""Artist website source adapter."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from burning_man_scraper.sources.artelier_map import artist_internal_to_artelier36, artelier_headers
from burning_man_scraper.sources.base import (
    FieldValue,
    NormalizedRecord,
    SourceDescriptor,
    SourceInspectResult,
)
from burning_man_scraper.sources.artist_website import ingest as artist_ingest
from burning_man_scraper.sources.run_store import (
    create_run,
    load_manifest,
    update_progress,
    write_artelier_outputs,
    write_manifest,
    write_normalized_records,
)


DESCRIPTOR = SourceDescriptor(
    id="artist_website",
    label="Artist website",
    description="Crawl an artist portfolio site and build Artelier review records.",
    input_kind="form",
    fields=[
        {"name": "artist_name", "label": "Artist name", "required": True, "type": "text"},
        {"name": "website_url", "label": "Website URL", "required": True, "type": "url"},
        {"name": "portfolio_url", "label": "Portfolio / index URL", "required": False, "type": "url"},
        {"name": "max_pages", "label": "Crawl limit", "required": False, "type": "number", "default": 150},
        {
            "name": "render_javascript",
            "label": "JavaScript rendering (Playwright)",
            "required": False,
            "type": "boolean",
            "default": False,
        },
    ],
)


def _field(
    value: str,
    *,
    status: str,
    confidence: str,
    evidence: list[str] | None = None,
) -> FieldValue:
    return FieldValue(
        value=value or "",
        status=status if value else "missing",  # type: ignore[arg-type]
        confidence=confidence if value else "none",
        evidence=evidence or [],
    )


def _record_id(source_url: str, title: str) -> str:
    digest = hashlib.sha1(f"{source_url}|{title}".encode("utf-8")).hexdigest()[:12]
    return f"aw_{digest}"


def internal_row_to_normalized(row: dict[str, Any], headers: list[str]) -> NormalizedRecord:
    artelier = artist_internal_to_artelier36(row, headers)
    proof_conf = str(row.get("proof_confidence") or "").lower()
    class_conf = str(row.get("classification_confidence") or "").lower()
    desc_conf = str(row.get("description_confidence") or "").lower()
    granularity = str(row.get("source_granularity") or "")
    excerpt = str(row.get("proof_excerpt") or "")
    evidence_bits = [bit for bit in [granularity, excerpt[:240]] if bit]

    flags: list[str] = []
    row_flags = row.get("review_flags") or []
    if isinstance(row_flags, str):
        row_flags = [part for part in row_flags.split("|") if part]
    flags.extend(str(flag) for flag in row_flags)
    if granularity == "Image-only inference" or proof_conf == "low":
        flags.append("sparse_evidence")
    if class_conf == "low" or desc_conf == "low" or proof_conf == "low":
        flags.append("low_confidence")
    if not artelier.get("hero_image_url"):
        flags.append("hero_missing")
    if not artelier.get("contributor_name"):
        flags.append("missing_attribution")
    if not artelier.get("project_title") or not artelier.get("proof_external_url"):
        flags.append("incomplete_fields")
    # Dedupe flags preserving order
    flags = list(dict.fromkeys(flags))

    artelier["review_flags"] = "|".join(flags)
    artelier["source_provenance"] = "artist_website|crawl"
    if row.get("collaborators"):
        artelier["source_provenance"] += "|collaborators_detected"

    title_status = "sourced" if granularity != "Image-only inference" else "inferred"
    type_status = "sourced" if class_conf == "high" else ("inferred" if artelier.get("project_type") else "missing")

    collaborators = str(row.get("collaborators") or "")
    tags = artelier.get("project_tags") or ""
    materials = artelier.get("project_materials") or ""

    unsupported: dict[str, Any] = {
        "collaborators": collaborators,
        "dimensions": row.get("dimensions") or "",
        "institution": row.get("institution") or "",
        "image_urls": row.get("image_urls") or [],
        "styles_tags": tags,
        "collections_preview": artelier.get("project_context_tags") or "",
        "medium": row.get("medium") or "",
        "price": row.get("price") or "",
        "availability": row.get("availability") or "",
        "series": row.get("series") or "",
        "inventory": row.get("inventory") or "",
        "collection_url": row.get("collection_url") or "",
    }

    return NormalizedRecord(
        record_id=_record_id(artelier.get("proof_external_url") or "", artelier.get("project_title") or ""),
        source_id="artist_website",
        source_record_id=artelier.get("project_slug") or "",
        source_record_url=artelier.get("proof_external_url") or "",
        project_title=_field(artelier.get("project_title") or "", status=title_status, confidence=proof_conf or "medium", evidence=evidence_bits),
        contributor_name=_field(artelier.get("contributor_name") or "", status="sourced", confidence="high", evidence=[row.get("artist_website") or ""]),
        project_year=_field(
            artelier.get("project_year") or "",
            status="sourced" if artelier.get("project_year") else "missing",
            confidence="high" if artelier.get("project_year") else "none",
            evidence=evidence_bits,
        ),
        project_location=_field(
            artelier.get("project_location") or "",
            status="sourced" if artelier.get("project_location") else "missing",
            confidence="medium" if artelier.get("project_location") else "none",
            evidence=evidence_bits,
        ),
        project_type=_field(artelier.get("project_type") or "", status=type_status, confidence=class_conf or "medium", evidence=evidence_bits),
        collection=_field(
            artelier.get("project_context_tags") or "",
            status="inferred" if artelier.get("project_context_tags") else "missing",
            confidence="low" if artelier.get("project_context_tags") else "none",
            evidence=evidence_bits,
        ),
        hero_image_url=_field(
            artelier.get("hero_image_url") or "",
            status="sourced" if artelier.get("hero_image_url") else "missing",
            confidence="high" if artelier.get("hero_image_url") else "none",
            evidence=[artelier.get("proof_external_url") or ""],
        ),
        proof_external_url=_field(artelier.get("proof_external_url") or "", status="sourced", confidence="high", evidence=[artelier.get("proof_external_url") or ""]),
        project_summary=_field(
            artelier.get("project_summary") or "",
            status="inferred",
            confidence=desc_conf or "medium",
            evidence=evidence_bits,
        ),
        project_tags=_field(tags, status="inferred" if tags else "missing", confidence="medium" if tags else "none", evidence=evidence_bits),
        project_materials=_field(materials, status="sourced" if materials else "missing", confidence="medium" if materials else "none", evidence=evidence_bits),
        project_fabrication_methods=_field(
            artelier.get("project_fabrication_methods") or "",
            status="inferred" if artelier.get("project_fabrication_methods") else "missing",
            confidence="medium" if artelier.get("project_fabrication_methods") else "none",
            evidence=evidence_bits,
        ),
        project_context_tags=_field(
            artelier.get("project_context_tags") or "",
            status="inferred" if artelier.get("project_context_tags") else "missing",
            confidence="low" if artelier.get("project_context_tags") else "none",
            evidence=evidence_bits,
        ),
        collaboration_status=_field(
            artelier.get("collaboration_status") or "",
            status="sourced" if collaborators else "inferred",
            confidence="high" if collaborators else "medium",
            evidence=evidence_bits,
        ),
        collaborators=_field(collaborators, status="sourced" if collaborators else "missing", confidence="high" if collaborators else "none", evidence=evidence_bits),
        client_name=_field(
            artelier.get("client_name") or "",
            status="sourced" if artelier.get("client_name") else "missing",
            confidence="medium" if artelier.get("client_name") else "none",
            evidence=evidence_bits,
        ),
        review_flags=flags,
        relationships={
            "contributors": [artelier.get("contributor_name") or ""],
            "collaborators": [part.strip() for part in collaborators.split(";") if part.strip()],
            "styles_tags": [part for part in tags.split("|") if part],
            "collections": [part for part in (artelier.get("project_context_tags") or "").split("|") if part],
        },
        raw_evidence={
            "source_granularity": granularity,
            "proof_excerpt": excerpt,
            "import_notes": row.get("import_notes") or "",
            "proof_confidence": row.get("proof_confidence"),
            "classification_confidence": row.get("classification_confidence"),
            "description_confidence": row.get("description_confidence"),
            "unsupported": unsupported,
        },
        artelier_row=artelier,
    )


class ArtistWebsiteAdapter:
    descriptor = DESCRIPTOR

    def inspect(
        self,
        *,
        artist_name: str = "",
        website_url: str = "",
        portfolio_url: str = "",
        max_pages: int = 150,
        render_javascript: bool = False,
        **_: Any,
    ) -> SourceInspectResult:
        artist_name = (artist_name or "").strip()
        website_url = (website_url or "").strip()
        if not artist_name:
            return SourceInspectResult(
                ok=False,
                source_id=self.descriptor.id,
                detected_label=self.descriptor.label,
                message="",
                error="Artist name is required",
            )
        if not website_url:
            return SourceInspectResult(
                ok=False,
                source_id=self.descriptor.id,
                detected_label=self.descriptor.label,
                message="",
                error="Website URL is required",
            )
        parsed = urlparse(website_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return SourceInspectResult(
                ok=False,
                source_id=self.descriptor.id,
                detected_label=self.descriptor.label,
                message="",
                error="Website URL must be an http(s) URL",
            )
        host = parsed.netloc
        return SourceInspectResult(
            ok=True,
            source_id=self.descriptor.id,
            detected_label=self.descriptor.label,
            message=(
                f"Detected artist website for {artist_name} on {host}. "
                "The crawler will stay on this domain, respect robots.txt, "
                "and build Artelier review records without a spreadsheet."
            ),
            summary={
                "artist_name": artist_name,
                "website_url": artist_ingest.normalize_url(website_url),
                "portfolio_url": artist_ingest.normalize_url(portfolio_url) if portfolio_url else "",
                "host": host,
                "max_pages": int(max_pages or 150),
                "render_javascript": bool(render_javascript),
            },
        )

    def prepare(
        self,
        *,
        project_root: Path,
        artist_name: str,
        website_url: str,
        portfolio_url: str = "",
        max_pages: int = 150,
        delay: float = 1.0,
        timeout: int = 20,
        render_javascript: bool = False,
        pages: list | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        inspection = self.inspect(
            artist_name=artist_name,
            website_url=website_url,
            portfolio_url=portfolio_url,
            max_pages=max_pages,
            render_javascript=render_javascript,
        )
        if not inspection.ok:
            raise ValueError(inspection.error or "Invalid artist website input")

        run_path = create_run(
            project_root,
            source_id=self.descriptor.id,
            label=artist_name.strip(),
            input_summary=inspection.summary,
        )
        steps: list[dict[str, str]] = []

        def step(step_id: str, label: str, status: str) -> None:
            steps.append({"id": step_id, "label": label, "status": status})

        update_progress(run_path, percent=5, phase="crawl", message="Starting website crawl", status="running")
        step("inspect", f"Inspected {inspection.summary['host']}", "done")

        logs: list[artist_ingest.LogEntry] = []
        if pages is None:
            step("crawl", "Crawling artist domain", "running")
            pages, logs = artist_ingest.crawl_site(
                website_url,
                portfolio_url or None,
                run_path,
                max_pages=int(max_pages or 150),
                delay=float(delay or 1.0),
                timeout=int(timeout or 20),
                use_playwright=bool(render_javascript),
            )
            steps[-1]["status"] = "done"
            steps[-1]["label"] = f"Crawled {len(pages)} page(s)"
        else:
            step("crawl", f"Using {len(pages)} provided page(s)", "done")

        update_progress(run_path, percent=55, phase="extract", message="Extracting artworks")
        step("extract", "Extracting artwork candidates", "running")
        from burning_man_scraper.sources.artist_website.pipeline import extract_site_artworks

        artworks = extract_site_artworks(
            pages, artist_name=artist_name.strip(), logs=logs, run_path=run_path
        )
        internal_rows: list[dict[str, Any]] = [
            artist_ingest.candidate_to_row(
                artist_ingest.artwork_to_candidate(item),
                artist_name.strip(),
                website_url,
            )
            for item in artworks
            if item.title
        ]
        if not internal_rows:
            project_pages = artist_ingest.detect_project_pages(pages, portfolio_url or None)
            for page in project_pages:
                candidates = artist_ingest.extract_project_entries(
                    page, artist_name=artist_name.strip()
                )
                if not candidates:
                    logs.append(
                        artist_ingest.LogEntry(
                            artist_ingest.timestamp(),
                            page.url,
                            "extract",
                            "skipped",
                            detail="No project entries",
                        )
                    )
                    continue
                logs.append(
                    artist_ingest.LogEntry(
                        artist_ingest.timestamp(),
                        page.url,
                        "extract",
                        "parsed",
                        str(page.status_code),
                        f"{len(candidates)} candidate entries",
                    )
                )
                internal_rows.extend(
                    artist_ingest.candidate_to_row(candidate, artist_name, website_url)
                    for candidate in candidates
                )
        internal_rows = artist_ingest.deduplicate_rows(internal_rows, logs)
        for row in internal_rows:
            artist_ingest.validate_row(row)
        steps[-1]["status"] = "done"
        steps[-1]["label"] = f"Extracted {len(internal_rows)} artwork(s)"

        update_progress(run_path, percent=80, phase="normalize", message="Normalizing to Artelier schema")
        step("normalize", "Mapping to Artelier 36-column schema", "running")
        headers = artelier_headers(project_root)
        records = [internal_row_to_normalized(row, headers) for row in internal_rows]
        write_normalized_records(run_path, records)
        artelier_rows = []
        unsupported_relationships: list[dict[str, Any]] = []
        for record in records:
            row = dict(record.artelier_row)
            row["review_flags"] = "|".join(record.review_flags)
            artelier_rows.append(row)
            unsupported = (record.raw_evidence or {}).get("unsupported") or {}
            if unsupported.get("collaborators") or unsupported.get("dimensions") or unsupported.get("institution"):
                unsupported_relationships.append(
                    {
                        "record_id": record.record_id,
                        "project_title": row.get("project_title"),
                        "relationships": unsupported,
                        "note": "Preserved in run manifest; not flattened into Artelier CSV columns.",
                    }
                )
        paths = write_artelier_outputs(
            run_path,
            project_root,
            artelier_rows=artelier_rows,
            label=artist_name.strip(),
            unsupported_relationships=unsupported_relationships,
        )
        # Persist scrape log for audit.
        artist_ingest.write_dict_csv(
            run_path / "scrape_log.csv",
            [artist_ingest.asdict(entry) for entry in logs],
            ["timestamp_utc", "url", "action", "status", "http_status", "detail"],
        )
        audit_path = run_path / "page_extraction_audit.json"
        if audit_path.exists():
            paths = dict(paths)
            paths["page_audit"] = audit_path
            loaded = load_manifest(run_path)
            loaded_paths = dict(loaded.get("paths") or {})
            loaded_paths["page_audit"] = str(audit_path)
            loaded["paths"] = loaded_paths
            write_manifest(run_path, loaded)
        steps[-1]["status"] = "done"
        step("ready", f"Ready — {len(artelier_rows)} projects for review", "done")
        update_progress(run_path, percent=100, phase="ready", message="Review ready", status="ready")

        summary = {
            "project_count": len(artelier_rows),
            "upload_ready_count": sum(
                1 for row in artelier_rows if not row.get("review_flags")
            ),
        }
        return {
            "ok": True,
            "run_id": run_path.name,
            "source_id": self.descriptor.id,
            "label": artist_name.strip(),
            "project_count": len(artelier_rows),
            "summary": summary,
            "unsupported_relationships": unsupported_relationships,
            "viewer_reload": f"/api/view?run_id={run_path.name}",
            "paths": {key: str(value) for key, value in paths.items()},
            "steps": steps,
        }
