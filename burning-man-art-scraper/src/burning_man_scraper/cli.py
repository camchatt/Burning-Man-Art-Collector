from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from burning_man_scraper.config import ScraperConfig, load_config
from burning_man_scraper.batch import BatchResult, process_approved_batch
from burning_man_scraper.artelier_schema import (
    ArtelierPreview,
    build_artelier_preview,
    load_field_mapping,
    load_import_schema,
)
from burning_man_scraper.fetcher import BoundedFetcher
from burning_man_scraper.enrichment.cli import run_enrichment_workflow
from burning_man_scraper.exporter import export_completed_batch, source_slug
from burning_man_scraper.verification.cli import default_export_path, default_www_dir, run_verification
from burning_man_scraper.inspection import PageInspection, inspect_html, is_installation_detail_url
from burning_man_scraper.preview import (
    configuration_hash,
    record_empty_arrays,
    record_null_fields,
    write_first_record_preview,
    write_artelier_preview_files,
    write_raw_html,
    write_source_manifest,
)
from burning_man_scraper.record_parser import (
    ParsePreview,
    parse_inline_archive_record,
    parse_installation_record,
)
from burning_man_scraper.state import ApprovalContext, Checkpoint, ScraperState, SourceLookup, hash_value
from burning_man_scraper.url_utils import validate_archive_url


InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]

RESUME_OPTIONS = {
    "1": "continue",
    "2": "retry_failed",
    "3": "re_export_completed",
    "4": "new_export_from_beginning",
    "5": "overwrite_previous_exports",
    "6": "cancel",
}

APPROVAL_OPTIONS = {
    "1": "approved",
    "2": "canceled",
    "3": "change_requested_count",
    "4": "change_field_mapping_or_formatting",
    "5": "select_different_source_url",
}

MAIN_MENU_OPTIONS = {
    "1": "scrape",
    "2": "enrich",
    "3": "resume_enrichment",
    "4": "view_export_history",
    "5": "verify",
    "6": "exit",
}


def prompt_for_main_menu(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> str:
    output_func("1. Scrape a Burning Man archive page")
    output_func("2. Enrich an existing batch")
    output_func("3. Resume an enrichment batch")
    output_func("4. View export history")
    output_func("5. Verify projects against the official archive")
    output_func("6. Exit")
    output_func("")

    while True:
        choice = input_func("> ").strip()
        if choice in MAIN_MENU_OPTIONS:
            return MAIN_MENU_OPTIONS[choice]
        if choice.startswith(("http://", "https://")):
            return choice
        output_func("Invalid menu option: enter 1, 2, 3, 4, or 5.")
        output_func("")


def prompt_for_url(input_func: InputFunc = input, output_func: OutputFunc = print) -> tuple[str, str]:
    while True:
        output_func("Enter the archive or listing page URL:")
        output_func("")
        entered_url = input_func("> ").strip()
        try:
            normalized_url = validate_archive_url(entered_url)
        except ValueError as exc:
            output_func(f"Invalid URL: {exc}")
            output_func("")
            continue
        return entered_url, normalized_url


def prompt_for_record_count(
    max_records: int,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> int:
    while True:
        output_func("How many new records should be processed?")
        output_func("")
        raw_count = input_func("> ").strip()
        if not raw_count:
            output_func("Invalid record count: value cannot be empty.")
            output_func("")
            continue

        try:
            count = int(raw_count)
        except ValueError:
            output_func("Invalid record count: enter a positive integer.")
            output_func("")
            continue

        if count <= 0:
            output_func("Invalid record count: enter a positive integer.")
            output_func("")
            continue

        if count > max_records:
            output_func(f"Invalid record count: maximum allowed is {max_records}.")
            output_func("")
            continue

        return count


def prompt_for_resume_action(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> str:
    output_func("RESUME MENU")
    output_func("")
    output_func("1. Continue with next unprocessed records")
    output_func("2. Retry previously failed records")
    output_func("3. Re-export completed records")
    output_func("4. Start a new export from the beginning without deleting history")
    output_func("5. Overwrite previous exports")
    output_func("6. Cancel")
    output_func("")

    while True:
        choice = input_func("> ").strip()
        if not choice:
            return RESUME_OPTIONS["1"]
        if choice in RESUME_OPTIONS:
            return RESUME_OPTIONS[choice]
        output_func("Invalid resume option: enter 1, 2, 3, 4, 5, or 6.")
        output_func("")


def prompt_for_preview_approval(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> str:
    output_func("The first record of the proposed batch has been parsed.")
    output_func("")
    output_func("Continue with the requested batch?")
    output_func("")
    output_func("1. Yes, process this batch")
    output_func("2. No, cancel")
    output_func("3. Change requested count")
    output_func("4. Change field mapping or formatting")
    output_func("5. Select a different source URL")
    output_func("")

    while True:
        choice = input_func("> ").strip()
        if choice in APPROVAL_OPTIONS:
            return APPROVAL_OPTIONS[choice]
        output_func("Invalid approval option: enter 1, 2, 3, 4, or 5.")
        output_func("")


def prompt_for_overwrite_batch(
    source_lookup: SourceLookup,
    export_root: Path,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> int | None:
    year_or_slug = source_lookup.source.detected_year or source_slug(source_lookup.source.normalized_url)
    batches_dir = export_root / "burning_man" / year_or_slug / "batches"
    existing_batches = sorted(
        int(path.name.split("_", 1)[1])
        for path in batches_dir.glob("batch_*")
        if path.is_dir() and path.name.startswith("batch_") and path.name.split("_", 1)[1].isdigit()
    )
    if not existing_batches:
        output_func("No previous batch export folders were found to overwrite.")
        output_func("A new batch export folder will be created instead.")
        return None

    output_func("OVERWRITE EXPORT")
    output_func("")
    output_func("Choose the one batch export folder to overwrite.")
    for batch_number in existing_batches:
        batch_dir = batches_dir / f"batch_{batch_number:03d}"
        output_func(f"{batch_number}. {batch_dir}")
    output_func("")

    while True:
        raw_choice = input_func("> ").strip()
        try:
            batch_number = int(raw_choice)
        except ValueError:
            output_func("Invalid overwrite choice: enter one listed batch number.")
            output_func("")
            continue
        if batch_number not in existing_batches:
            output_func("Invalid overwrite choice: enter one listed batch number.")
            output_func("")
            continue

        target = batches_dir / f"batch_{batch_number:03d}"
        output_func("")
        output_func(f"This will replace files only in: {target}")
        output_func("- artelier_import.csv")
        output_func("- full_export.json")
        output_func("- batch_manifest.json")
        output_func("Overwrite this batch export? Type yes to confirm.")
        confirmation = input_func("> ").strip().lower()
        if confirmation == "yes":
            return batch_number
        output_func("Overwrite not confirmed. A new batch export folder will be created instead.")
        return None


def summarize_source(
    source_lookup: SourceLookup,
    requested_count: int,
    output_func: OutputFunc = print,
) -> None:
    checkpoint = source_lookup.checkpoint or Checkpoint(
        source_id=source_lookup.source.source_id,
        last_discovered_position=0,
        last_completed_position=0,
        last_exported_position=0,
        updated_at="",
    )
    next_position = checkpoint.last_completed_position + 1

    output_func("SOURCE SUMMARY")
    output_func("")
    output_func("Normalized URL:")
    output_func(source_lookup.source.normalized_url)
    output_func("")
    output_func(f"Previously exported: {checkpoint.last_exported_position} records")
    output_func("")
    output_func(f"Last completed source position: {checkpoint.last_completed_position}")
    output_func("")
    output_func(f"Next source position: {next_position}")
    output_func("")
    output_func(f"Requested new records: {requested_count}")


def print_inspection_report(
    inspection: PageInspection,
    raw_html_path: object,
    manifest_path: object,
    output_func: OutputFunc = print,
) -> None:
    output_func("PAGE INSPECTION")
    output_func("")
    output_func(f"Entered URL: {inspection.entered_url}")
    output_func(f"Normalized URL: {inspection.normalized_url}")
    output_func(f"Final URL after redirects: {inspection.final_url}")
    output_func(f"Canonical URL: {inspection.canonical_url or 'not found'}")
    output_func(f"Page title: {inspection.page_title or 'not found'}")
    output_func(f"Detected year: {inspection.detected_year or 'not found'}")
    output_func(f"Detected page type: {inspection.detected_page_type}")
    output_func(f"Robots.txt status: {inspection.robots_txt_status}")
    output_func(f"Candidate installation links: {len(inspection.candidate_installation_links)}")
    output_func(f"Pagination detected: {inspection.pagination_detected}")
    output_func("Candidate internal links:")
    for url in inspection.candidate_internal_links:
        output_func(f"- {url}")
    if not inspection.candidate_internal_links:
        output_func("- none")
    output_func("Excluded links:")
    for link in inspection.excluded_links:
        output_func(f"- {link.url} ({link.reason})")
    if not inspection.excluded_links:
        output_func("- none")
    output_func(f"Parser that would be used: {inspection.parser_version}")
    output_func(f"Raw HTML saved: {raw_html_path}")
    output_func(f"Source manifest saved: {manifest_path}")


def build_first_record_preview(
    inspection: PageInspection,
    source_lookup: SourceLookup,
    source_result,
    fetcher: BoundedFetcher,
    state_store: ScraperState,
    preview_run_id: str,
    output_func: OutputFunc = print,
) -> ParsePreview | None:
    if inspection.detected_page_type == "single installation detail page":
        parse_preview = parse_installation_record(
            source_result,
            source_archive_url=inspection.normalized_url,
            source_position=1,
            scrape_run_id=preview_run_id,
        )
        if is_valid_preview(parse_preview):
            state_store.mark_source_record(
                source_lookup.source.source_id,
                1,
                inspection.normalized_url,
                parse_preview.record.canonical_source_url,
                parse_preview.record.record_id,
                "previewed",
                source_result.response_hash,
            )
            return parse_preview
        state_store.mark_source_record(
            source_lookup.source.source_id,
            1,
            inspection.normalized_url,
            parse_preview.record.canonical_source_url,
            parse_preview.record.record_id,
            "failed",
            source_result.response_hash,
        )
        return None

    if inspection.detected_page_type not in {"archive listing page", "filtered archive listing page"}:
        output_func("No installation preview was parsed because this page type is unsupported.")
        return None

    attempts = 0
    for source_position, installation_url in enumerate(inspection.candidate_installation_links, start=1):
        if attempts >= 5:
            break
        status = state_store.source_record_status(source_lookup.source.source_id, source_position)
        if status == "previewed":
            continue

        attempts += 1
        try:
            if is_same_page_fragment_candidate(installation_url, inspection.normalized_url):
                detail_result = source_result
                parse_preview = parse_inline_archive_record(
                    source_result,
                    source_archive_url=inspection.normalized_url,
                    source_position=source_position,
                    scrape_run_id=preview_run_id,
                )
            else:
                detail_result = fetcher.fetch(installation_url, allowed_urls={installation_url})
                parse_preview = parse_installation_record(
                    detail_result,
                    source_archive_url=inspection.normalized_url,
                    source_position=source_position,
                    scrape_run_id=preview_run_id,
                )
            if is_valid_preview(parse_preview):
                state_store.mark_source_record(
                    source_lookup.source.source_id,
                    source_position,
                    installation_url,
                    parse_preview.record.canonical_source_url,
                    parse_preview.record.record_id,
                    "previewed",
                    detail_result.response_hash,
                )
                return parse_preview
            state_store.mark_source_record(
                source_lookup.source.source_id,
                source_position,
                installation_url,
                parse_preview.record.canonical_source_url,
                parse_preview.record.record_id,
                "failed",
                detail_result.response_hash,
            )
        except Exception as exc:
            state_store.mark_source_record(
                source_lookup.source.source_id,
                source_position,
                installation_url,
                None,
                None,
                "failed",
                None,
            )
            output_func(f"Preview candidate failed at source position {source_position}: {exc}")

    return None


def is_same_page_fragment_candidate(candidate_url: str, normalized_source_url: str) -> bool:
    if "#" not in candidate_url:
        return False
    base_url = candidate_url.split("#", 1)[0]
    return base_url == normalized_source_url


def is_valid_preview(parse_preview: ParsePreview) -> bool:
    return bool(parse_preview.record.title and not parse_preview.record.parsing_errors)


def print_record_preview(
    parse_preview: ParsePreview,
    preview_paths: tuple[Path, Path, Path],
    artelier_preview: ArtelierPreview,
    artelier_paths: tuple[Path | None, Path, Path],
    output_func: OutputFunc = print,
) -> None:
    record = parse_preview.record
    output_func("FIRST RECORD PREVIEW")
    output_func("")
    output_func(f"Schema version: {record.schema_version}")
    output_func(f"Parser version: {record.parser_version}")
    output_func(f"Source position: {parse_preview.source_position}")
    output_func("")
    output_func("Fields:")
    for key, value in record.model_dump(mode="json").items():
        output_func(f"{key}: {value!r}")
    output_func("")
    output_func(f"Null fields: {record_null_fields(record)}")
    output_func(f"Empty arrays: {record_empty_arrays(record)}")
    output_func(f"Warnings: {record.warnings}")
    output_func("")
    output_func("Provenance summary:")
    output_func(f"Source URL: {record.source_url}")
    output_func(f"Canonical source URL: {record.canonical_source_url}")
    output_func(f"Source archive URL: {record.source_archive_url}")
    output_func(f"Source accessed at: {record.source_accessed_at}")
    output_func(f"Scrape run ID: {record.scrape_run_id}")
    output_func("")
    output_func(f"Preview JSON saved: {preview_paths[0]}")
    output_func(f"Preview CSV saved: {preview_paths[1]}")
    output_func(f"Preview Markdown saved: {preview_paths[2]}")
    output_func("")
    output_func("ARTELIER IMPORT PREVIEW")
    output_func("")
    for validation in artelier_preview.validations:
        result = "valid" if validation.valid else f"invalid: {'; '.join(validation.errors)}"
        output_func(f"{validation.field_name}: {validation.value!r} [{result}]")
    output_func("")
    output_func("CSV HEADER")
    output_func(",".join(artelier_preview.schema.headers))
    output_func("CSV FIRST ROW")
    output_func(",".join(artelier_preview.row[field] for field in artelier_preview.schema.headers))
    output_func("")
    output_func("UNMAPPED SOURCE FIELDS")
    for field_name in artelier_preview.unmapped_source_fields:
        output_func(f"- {field_name}")
    if not artelier_preview.unmapped_source_fields:
        output_func("- none")
    output_func("")
    if artelier_paths[0] is None:
        output_func("Artelier import CSV not written because validation failed.")
    else:
        output_func(f"Artelier import CSV saved: {artelier_paths[0]}")
        output_func(f"Full research JSON saved: {artelier_paths[1]}")
    output_func(f"Review CSV saved: {artelier_paths[2]}")


def print_batch_report(batch_result: BatchResult, output_func: OutputFunc = print) -> None:
    output_func("")
    output_func("APPROVED BATCH RESULT")
    output_func("")
    output_func(f"Attempted: {batch_result.attempted}")
    output_func(f"Succeeded: {batch_result.succeeded}")
    output_func(f"Failed: {batch_result.failed}")
    output_func(f"Skipped: {batch_result.skipped}")
    output_func(f"Duplicates: {batch_result.duplicates}")
    output_func(f"Attempt ceiling: {batch_result.attempt_ceiling}")
    output_func(f"Next unprocessed record: {batch_result.next_unprocessed_record}")
    output_func("Source change check:")
    output_func(f"- New links: {len(batch_result.manifest_changes.new_links)}")
    output_func(f"- Removed links: {len(batch_result.manifest_changes.removed_links)}")
    output_func(f"- Reordered links: {len(batch_result.manifest_changes.reordered_links)}")
    output_func(f"- Unchanged links: {len(batch_result.manifest_changes.unchanged_links)}")


def build_approval_context(
    source_lookup: SourceLookup,
    parse_preview: ParsePreview,
    preview_run_id: str,
    requested_count: int,
    proposed_batch_number: int,
    config: ScraperConfig,
    manifest_path: Path,
) -> ApprovalContext:
    return ApprovalContext(
        preview_run_id=preview_run_id,
        source_id=source_lookup.source.source_id,
        normalized_source_url=source_lookup.source.normalized_url,
        proposed_batch_number=proposed_batch_number,
        requested_count=requested_count,
        preview_record_id=parse_preview.record.record_id or "",
        schema_version=parse_preview.record.schema_version,
        parser_version=parse_preview.record.parser_version or "",
        configuration_hash=configuration_hash(config),
        source_manifest_hash=hash_value(manifest_path.read_text(encoding="utf-8")),
    )


def run_scrape_workflow(
    config: ScraperConfig | None = None,
    state_store: ScraperState | None = None,
    fetcher: BoundedFetcher | None = None,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    initial_entered_url: str | None = None,
) -> int:
    config = config or load_config()
    state_store = state_store or ScraperState(config.state_database_path)
    fetcher = fetcher or BoundedFetcher(
        user_agent=config.user_agent,
        delay_seconds=config.request_delay_seconds,
        timeout_seconds=config.request_timeout_seconds,
        max_retries=config.max_retries,
    )

    if initial_entered_url is None:
        entered_url, normalized_url = prompt_for_url(input_func=input_func, output_func=output_func)
    else:
        entered_url = initial_entered_url
        normalized_url = validate_archive_url(entered_url)
    output_func("")
    requested_count = prompt_for_record_count(
        config.max_records_per_run,
        input_func=input_func,
        output_func=output_func,
    )
    output_func("")

    output_func(f"Entered URL: {entered_url}")
    output_func(f"Normalized URL: {normalized_url}")
    output_func(f"Requested record count: {requested_count}")
    output_func("")

    source_lookup = state_store.get_or_create_source(entered_url, normalized_url)
    summarize_source(source_lookup, requested_count, output_func=output_func)

    resume_action = "continue"
    if source_lookup.has_previous_state:
        output_func("")
        resume_action = prompt_for_resume_action(
            input_func=input_func,
            output_func=output_func,
        )
        if resume_action == "cancel":
            output_func("")
            output_func("Canceled.")
            return 1
        output_func("")
        output_func(f"Resume action: {resume_action}")

    output_func("")
    output_func("Fetching supplied page and robots.txt only...")
    source_result, robots_result = fetcher.fetch_source_and_robots(normalized_url)
    raw_html_path, _metadata_path = write_raw_html(source_result, config.raw_html_dir)
    inspection = inspect_html(
        entered_url=entered_url,
        normalized_url=normalized_url,
        fetch_result=source_result,
        robots_result=robots_result,
    )
    manifest_path = write_source_manifest(inspection, requested_count, config)
    output_func("")
    print_inspection_report(
        inspection,
        raw_html_path=raw_html_path,
        manifest_path=manifest_path,
        output_func=output_func,
    )
    preview_run_id = str(uuid4())
    parse_preview = build_first_record_preview(
        inspection=inspection,
        source_lookup=source_lookup,
        source_result=source_result,
        fetcher=fetcher,
        state_store=state_store,
        preview_run_id=preview_run_id,
        output_func=output_func,
    )
    output_func("")
    if parse_preview is None:
        output_func("PREVIEW COMPLETE")
        output_func("No valid installation record preview could be produced.")
        output_func("No additional installation records were processed.")
        return 0

    preview_paths = write_first_record_preview(
        parse_preview,
        config.preview_manifest_path.parent / preview_run_id,
    )
    import_schema = load_import_schema(config.artelier_import_schema_path)
    mapping_config = load_field_mapping(config.artelier_field_mapping_path)
    artelier_preview = build_artelier_preview(parse_preview.record, import_schema, mapping_config)
    artelier_paths = write_artelier_preview_files(
        artelier_preview,
        parse_preview,
        config.preview_manifest_path.parent / preview_run_id,
    )
    print_record_preview(
        parse_preview,
        preview_paths,
        artelier_preview,
        artelier_paths,
        output_func=output_func,
    )
    output_func("")
    proposed_batch_number = state_store.next_proposed_batch_number(source_lookup.source.source_id)
    approval_context = build_approval_context(
        source_lookup=source_lookup,
        parse_preview=parse_preview,
        preview_run_id=preview_run_id,
        requested_count=requested_count,
        proposed_batch_number=proposed_batch_number,
        config=config,
        manifest_path=manifest_path,
    )
    approval_status = prompt_for_preview_approval(input_func=input_func, output_func=output_func)
    state_store.save_preview_approval(approval_context, approval_status)
    output_func("")
    if approval_status == "approved":
        export_batch_id = state_store.create_pending_export_batch(approval_context)
        output_func(f"Approval recorded for preview run: {preview_run_id}")
        output_func(f"Pending export batch created: {export_batch_id}")
        batch_result = process_approved_batch(
            source_lookup=source_lookup,
            inspection=inspection,
            source_result=source_result,
            fetcher=fetcher,
            state_store=state_store,
            requested_count=requested_count,
            export_batch_id=export_batch_id,
            preview_run_id=preview_run_id,
            import_schema=import_schema,
            mapping_config=mapping_config,
        )
        print_batch_report(batch_result, output_func=output_func)
        overwrite_batch = None
        if resume_action == "overwrite_previous_exports":
            overwrite_batch = prompt_for_overwrite_batch(
                source_lookup=source_lookup,
                export_root=config.export_root_dir,
                input_func=input_func,
                output_func=output_func,
            )
        export_paths = export_completed_batch(
            state_store=state_store,
            source=source_lookup.source,
            export_batch_id=export_batch_id,
            batch_result=batch_result,
            requested_count=requested_count,
            schema=import_schema,
            export_root=config.export_root_dir,
            overwrite_batch=overwrite_batch,
            overwrite_confirmed=overwrite_batch is not None,
        )
        output_func("")
        output_func("EXPORT FILES")
        output_func(f"Batch directory: {export_paths.batch_directory}")
        output_func(f"Artelier import CSV: {export_paths.artelier_csv}")
        output_func(f"Full JSON export: {export_paths.full_json}")
        output_func(f"Batch manifest: {export_paths.batch_manifest}")
        output_func(f"Export history: {export_paths.export_history}")
        output_func(f"Consolidated CSV: {export_paths.consolidated_csv}")
        output_func(f"Consolidated JSON: {export_paths.consolidated_json}")
    elif approval_status == "canceled":
        output_func("Approval canceled. No pending export batch was created.")
    elif approval_status == "change_requested_count":
        output_func("Requested count change selected. A new preview is required.")
    elif approval_status == "change_field_mapping_or_formatting":
        output_func("Field mapping or formatting change selected. A new preview is required.")
    elif approval_status == "select_different_source_url":
        output_func("Different source URL selected. A new preview is required.")
    output_func("")
    output_func("PREVIEW COMPLETE")
    output_func("")
    output_func("No additional installation records were processed.")

    return 0


def run_interactive(
    config: ScraperConfig | None = None,
    state_store: ScraperState | None = None,
    fetcher: BoundedFetcher | None = None,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> int:
    config = config or load_config()
    state_store = state_store or ScraperState(config.state_database_path)

    output_func("BURNING MAN ART ARCHIVE SCRAPER")
    output_func("")
    menu_action = prompt_for_main_menu(input_func=input_func, output_func=output_func)
    output_func("")

    if menu_action == "scrape":
        return run_scrape_workflow(
            config=config,
            state_store=state_store,
            fetcher=fetcher,
            input_func=input_func,
            output_func=output_func,
        )
    if menu_action == "enrich":
        return run_enrichment_workflow(
            config=config,
            state_store=state_store,
            input_func=input_func,
            output_func=output_func,
        )
    if menu_action == "resume_enrichment":
        return run_enrichment_workflow(
            config=config,
            state_store=state_store,
            input_func=input_func,
            output_func=output_func,
            resume_mode=True,
        )
    if menu_action == "view_export_history":
        return view_export_history(config.export_root_dir, output_func=output_func)
    if menu_action == "verify":
        return run_verify_workflow(config=config, input_func=input_func, output_func=output_func)
    if menu_action == "exit":
        output_func("Goodbye.")
        return 0

    return run_scrape_workflow(
        config=config,
        state_store=state_store,
        fetcher=fetcher,
        input_func=input_func,
        output_func=output_func,
        initial_entered_url=menu_action,
    )


def run_verify_workflow(
    config: ScraperConfig,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> int:
    project_root = Path(__file__).resolve().parents[2]
    output_func("Enter archive year to verify (default 2022):")
    year_text = input_func("> ").strip() or "2022"
    try:
        year = int(year_text)
    except ValueError:
        output_func("Invalid year.")
        return 1

    export_path = default_export_path(project_root, year)
    if not export_path.exists():
        output_func(f"No consolidated export found at {export_path}.")
        output_func("Verification can still run with --scope www or archive via run_verify.py.")
        return 1

    return run_verification(
        config=config,
        year=year,
        www_dir=default_www_dir(project_root),
        export_path=export_path,
        output_dir=project_root / "data" / "verification" / str(year),
        scope="export",
        validate_images=True,
        check_legacy_links=False,
        output_func=output_func,
    )


def view_export_history(export_root: Path, output_func: OutputFunc = print) -> int:
    history_path = export_root / "burning_man" / "export_history.csv"
    output_func("EXPORT HISTORY")
    output_func("")
    if not history_path.exists():
        output_func("No export history was found.")
        return 0
    output_func(history_path.read_text(encoding="utf-8-sig").strip())
    return 0


def main() -> int:
    return run_interactive()
