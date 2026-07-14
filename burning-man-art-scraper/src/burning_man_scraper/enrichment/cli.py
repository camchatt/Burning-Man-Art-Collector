from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Callable

from burning_man_scraper.config import ScraperConfig
from burning_man_scraper.artelier_schema import load_import_schema
from burning_man_scraper.enrichment.batch_loader import list_available_batches, load_batch_records
from burning_man_scraper.enrichment.models import (
    ENRICHMENT_OUTPUT_FILENAMES,
    BatchRecord,
    ScrapeBatch,
)
from burning_man_scraper.enrichment.research import (
    CachedFetchClient,
    FetchClient,
    build_enrichment_preview,
    write_preview_files,
)
from burning_man_scraper.enrichment.processor import EnrichmentBatchResult, process_approved_enrichment_batch
from burning_man_scraper.enrichment.cache import SearchCache
from burning_man_scraper.enrichment.providers import NoOpSearchProvider, SearchProvider, select_search_provider
from burning_man_scraper.enrichment.state import EnrichmentState
from burning_man_scraper.state import ScraperState


InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]

ENRICHMENT_RESUME_OPTIONS = {
    "1": "continue",
    "2": "retry_failed",
    "3": "reprocess_changed_rules",
    "4": "re_export_approved",
    "5": "cancel",
}

ENRICHMENT_APPROVAL_OPTIONS = {
    "1": "approve_continue",
    "2": "reject_cancel",
    "3": "change_field_mappings",
    "4": "change_source_ranking_rules",
    "5": "edit_proposed_values",
    "6": "select_different_batch",
}


def run_enrichment_workflow(
    config: ScraperConfig,
    state_store: ScraperState,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    resume_mode: bool = False,
    search_client: SearchProvider | None = None,
    fetch_client: FetchClient | None = None,
) -> int:
    batches = list_available_batches(config.export_root_dir)
    if not batches:
        output_func("No completed scrape batches were found.")
        return 1

    batch = prompt_for_batch_selection(batches, input_func=input_func, output_func=output_func)
    records = load_batch_records(batch, state_store=state_store)
    enrichment_state = EnrichmentState(state_store)
    statuses = enrichment_state.latest_status_by_project(batch.export_batch_id, batch.batch_directory)
    resume_action = "continue"
    if resume_mode or statuses:
        resume_action = prompt_for_enrichment_resume_action(
            input_func=input_func,
            output_func=output_func,
        )
        if resume_action == "cancel":
            output_func("")
            output_func("Canceled.")
            return 1

    requested_count = prompt_for_enrichment_count(
        default_count=default_requested_count(records, statuses, resume_action),
        input_func=input_func,
        output_func=output_func,
    )
    selected = enrichment_state.select_records(
        records=records,
        export_batch_id=batch.export_batch_id,
        source_batch_directory=batch.batch_directory,
        requested_count=requested_count,
        resume_action=resume_action,
    )
    print_enrichment_summary(
        batch=batch,
        records=records,
        selected=selected,
        requested_count=requested_count,
        previously_enriched=enrichment_state.count_previously_enriched(
            batch.export_batch_id,
            batch.batch_directory,
        ),
        resume_action=resume_action,
        output_func=output_func,
    )
    output_func("")
    output_func("Reserved enrichment output files:")
    for path in reserved_output_paths(batch.batch_directory):
        output_func(f"- {path}")
    output_func("")
    if not selected:
        output_func("No records were selected for enrichment.")
        output_func("No web searches were started.")
        return 0

    import_schema = load_import_schema(config.artelier_import_schema_path)
    provider_log = None
    if search_client is None:
        search_client, provider_log = select_search_provider(user_agent=config.user_agent)
    if isinstance(search_client, NoOpSearchProvider):
        output_func("ENRICHMENT NOT STARTED")
        output_func("")
        output_func("No web search provider is configured.")
        output_func("")
        output_func("Configure a provider or select a supported no-cost search method before continuing.")
        return 1

    run = enrichment_state.create_run(
        export_batch_id=batch.export_batch_id,
        source_batch_directory=batch.batch_directory,
        requested_count=requested_count,
        selected_records=selected,
        status="searching",
    )
    output_func(f"Enrichment run created: {run.enrichment_run_id}")
    output_func(f"General search provider: {search_client.name}")
    output_func("First-party discovery: enabled")
    output_func("Search cache: enabled")
    output_func("")

    preview = build_enrichment_preview(
        record=selected[0],
        schema=import_schema,
        search_client=search_client,
        fetch_client=fetch_client
        or CachedFetchClient(
            cache_dir=batch.batch_directory / ".cache" / "enrichment_pages",
            user_agent=config.user_agent,
            delay_seconds=config.request_delay_seconds,
        ),
        search_cache=SearchCache(batch.batch_directory / ".cache" / "search_results"),
        refresh_cache=os.environ.get("ENRICHMENT_SEARCH_CACHE_REFRESH", "").lower() in {"1", "true", "yes"},
        provider_failures=provider_log.failures if provider_log else [],
    )
    preview_paths = write_preview_files(preview, batch.batch_directory)
    print_enrichment_preview(preview, preview_paths, output_func=output_func)
    approval = prompt_for_enrichment_preview_approval(input_func=input_func, output_func=output_func)
    output_func("")
    output_func(f"Enrichment preview response: {approval}")
    if approval == "approve_continue":
        result = process_approved_enrichment_batch(
            enrichment_state=enrichment_state,
            run=run,
            batch_records=records,
            selected_records=selected,
            schema=import_schema,
            search_client=search_client,
            fetch_client=fetch_client
            or CachedFetchClient(
                cache_dir=batch.batch_directory / ".cache" / "enrichment_pages",
                user_agent=config.user_agent,
                delay_seconds=config.request_delay_seconds,
            ),
            search_cache=SearchCache(batch.batch_directory / ".cache" / "search_results"),
            approval_mode=os.environ.get("ENRICHMENT_APPROVAL_MODE", "manual_review_required"),
        )
        print_enrichment_completion(batch, result, batch.batch_directory / "artelier_import.csv", output_func)
        return 0
    output_func("Stopped after one-record enrichment preview.")
    return 0


def prompt_for_batch_selection(
    batches: list[ScrapeBatch],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> ScrapeBatch:
    output_func("Available batches:")
    output_func("")
    for index, batch in enumerate(batches, start=1):
        output_func(f"{index}. {batch.display_label}")
    output_func("")
    output_func("Which batch should be enriched?")
    output_func("")

    while True:
        raw_choice = input_func("> ").strip()
        try:
            choice = int(raw_choice)
        except ValueError:
            output_func("Invalid batch choice: enter one listed number.")
            output_func("")
            continue
        if 1 <= choice <= len(batches):
            return batches[choice - 1]
        output_func("Invalid batch choice: enter one listed number.")
        output_func("")


def prompt_for_enrichment_count(
    default_count: int,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> int:
    while True:
        output_func("How many records should be enriched?")
        output_func("")
        raw_count = input_func("> ").strip()
        if not raw_count:
            return default_count
        try:
            count = int(raw_count)
        except ValueError:
            output_func("Invalid enrichment count: enter a positive integer or leave blank for all remaining.")
            output_func("")
            continue
        if count <= 0:
            output_func("Invalid enrichment count: enter a positive integer.")
            output_func("")
            continue
        return count


def prompt_for_enrichment_resume_action(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> str:
    output_func("ENRICHMENT RESUME MENU")
    output_func("")
    output_func("1. Continue with next unenriched records")
    output_func("2. Retry failed records")
    output_func("3. Reprocess records with changed enrichment rules")
    output_func("4. Re-export approved enrichment")
    output_func("5. Cancel")
    output_func("")

    while True:
        choice = input_func("> ").strip()
        if not choice:
            return ENRICHMENT_RESUME_OPTIONS["1"]
        if choice in ENRICHMENT_RESUME_OPTIONS:
            return ENRICHMENT_RESUME_OPTIONS[choice]
        output_func("Invalid enrichment resume option: enter 1, 2, 3, 4, or 5.")
        output_func("")


def prompt_for_enrichment_preview_approval(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> str:
    output_func("")
    output_func("1. Approve the enrichment format and continue")
    output_func("2. Reject and cancel")
    output_func("3. Change field mappings")
    output_func("4. Change source ranking rules")
    output_func("5. Edit the proposed values")
    output_func("6. Select a different batch")
    output_func("")

    while True:
        choice = input_func("> ").strip()
        if choice in ENRICHMENT_APPROVAL_OPTIONS:
            return ENRICHMENT_APPROVAL_OPTIONS[choice]
        output_func("Invalid enrichment preview option: enter 1, 2, 3, 4, 5, or 6.")
        output_func("")


def default_requested_count(
    records: list[BatchRecord],
    statuses: dict[str, str],
    resume_action: str,
) -> int:
    if resume_action == "retry_failed":
        return len([record for record in records if statuses.get(record.project_record_id) == "failed"])
    if resume_action == "re_export_approved":
        return len([record for record in records if statuses.get(record.project_record_id) == "approved"])
    if resume_action == "reprocess_changed_rules":
        return len([record for record in records if statuses.get(record.project_record_id) != "approved"])
    return len([record for record in records if record.project_record_id not in statuses])


def print_enrichment_summary(
    batch: ScrapeBatch,
    records: list[BatchRecord],
    selected: list[BatchRecord],
    requested_count: int,
    previously_enriched: int,
    resume_action: str,
    output_func: OutputFunc = print,
) -> None:
    remaining = max(0, len(records) - previously_enriched)
    output_func("")
    output_func("ENRICHMENT SUMMARY")
    output_func("")
    output_func("Batch:")
    output_func(f"{batch.year} / {batch.batch_name}")
    output_func("")
    output_func(f"Records in batch: {len(records)}")
    output_func("")
    output_func(f"Previously enriched: {previously_enriched}")
    output_func("")
    output_func(f"Remaining: {remaining}")
    output_func("")
    output_func(f"Requested now: {requested_count}")
    output_func("")
    output_func(f"Resume action: {resume_action}")
    output_func("")
    output_func(f"Proposed records: {proposed_range_text(selected)}")


def proposed_range_text(selected: list[BatchRecord]) -> str:
    if not selected:
        return "none"
    return f"{selected[0].batch_index} through {selected[-1].batch_index}"


def reserved_output_paths(batch_directory: Path) -> list[Path]:
    return [batch_directory / filename for filename in ENRICHMENT_OUTPUT_FILENAMES]


def print_enrichment_preview(preview, preview_paths: tuple[Path, Path, Path, Path], output_func: OutputFunc = print) -> None:
    record = preview.batch_record
    output_func("")
    output_func("PROJECT")
    output_func("")
    output_func(f"title: {record.project_title}")
    output_func(f"year: {record.year or ''}")
    output_func(f"contributor: {record.contributor_name or ''}")
    output_func(f"original source URL: {record.source_url or ''}")
    output_func("")
    output_func("SOURCES FOUND")
    for source in preview.sources:
        output_func(f"- {source.title}")
        output_func(f"  URL: {source.url}")
        output_func(f"  Source type: {source.source_type}")
        output_func(f"  Relevance score: {source.relevance_score}")
        output_func(f"  Matching identifiers: {', '.join(source.matching_identifiers)}")
    if not preview.sources:
        output_func("- none")
    output_func("")
    output_func("PROPOSED ENRICHMENT")
    for change in preview.proposed_changes:
        output_func(f"- Artelier field name: {change.artelier_field}")
        output_func(f"  Original value: {change.original_value}")
        output_func(f"  Proposed value: {change.proposed_value}")
        output_func(f"  Evidence classification: {change.evidence_classification}")
        output_func(f"  Confidence: {change.confidence}")
        output_func(f"  Source URL: {change.source_url}")
        output_func(f"  Source excerpt: {change.source_excerpt}")
        output_func(f"  Review required: {change.review_required}")
    if not preview.proposed_changes:
        output_func("- none")
    output_func("")
    output_func("UNRESOLVED FIELDS")
    for field, reason in preview.unresolved_fields.items():
        output_func(f"- {field}: {reason}")
    if not preview.unresolved_fields:
        output_func("- none")
    output_func("")
    output_func("ARTELIER ROW PREVIEW")
    output_func(",".join(preview.headers))
    output_func(",".join(preview.artelier_row[header] for header in preview.headers))
    output_func("")
    output_func(f"Enrichment preview JSON: {preview_paths[0]}")
    output_func(f"Enrichment review preview CSV: {preview_paths[1]}")
    output_func(f"Enriched Artelier row preview CSV: {preview_paths[2]}")
    output_func(f"Source report: {preview_paths[3]}")


def print_enrichment_completion(
    batch: ScrapeBatch,
    result: EnrichmentBatchResult,
    original_artelier_csv: Path,
    output_func: OutputFunc = print,
) -> None:
    pending_review = result.unresolved_change_count
    output_func("")
    output_func("ENRICHMENT COMPLETE")
    output_func("")
    output_func("Batch:")
    output_func(f"{batch.year} / {batch.batch_name}")
    output_func("")
    output_func(f"Requested records: {result.requested_count}")
    output_func("")
    output_func(f"Completed: {result.completed_count}")
    output_func("")
    output_func(f"No credible sources found: {result.no_sources_count}")
    output_func("")
    output_func(f"Failed: {result.failed_count}")
    output_func("")
    output_func(f"Approved field changes: {result.approved_change_count}")
    output_func("")
    output_func(f"Pending review: {pending_review}")
    output_func("")
    output_func(f"Original Artelier file: {original_artelier_csv}")
    output_func("")
    output_func(f"Enriched Artelier file: {result.enriched_csv}")
    output_func("")
    output_func(f"Review file: {result.review_csv}")
    output_func("")
    output_func(f"Next unenriched record: {result.next_unenriched_record}")
