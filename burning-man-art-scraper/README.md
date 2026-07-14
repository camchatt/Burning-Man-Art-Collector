# Burning Man Art Archive Scraper

Standalone Burning Man art archive scraper.

Phase 1 collects and validates interactive input.

Phase 2 adds persistent SQLite state for normalized source URLs. It does not
fetch website content, parse listing pages, or scrape installation details.

Phase 3 fetches and inspects only the exact supplied page plus `robots.txt`.
It does not traverse pagination, discover homepages, fetch neighboring archive
years, or fetch installation detail pages.

Phase 4 parses exactly one installation record for preview. Listing pages use
the first valid unprocessed installation link in source order, trying no more
than five candidates. Single installation detail pages parse only the supplied
page. The scraper stops after the preview.

Burning Man archive pages are treated as inline archive listings: records appear
directly on the supplied page in `title / by: / from: / year:` blocks. Same-page
fragment title links such as `#a2I8X...` are used as candidate record URLs, but
they are not fetched as separate pages.

Phase 5 maps the first-record preview into the current Artelier import contract.
The authoritative field-name and column-order source is
`C:/Users/camch/Downloads/registry-import-template (1).csv`. Older ingestion
code and successful examples were inspected, but they use an older 31-column
schema and are not authoritative for the current import headers.

Phase 6 requires explicit approval after the first-record preview before any
batch can be staged. Approval is tied to the normalized source URL, preview run,
proposed batch number, requested count, previewed record, schema version, parser
version, configuration hash, and source manifest hash. Approved previews create
a pending export batch record before Phase 7 processing begins.

Phase 7 processes an approved number of new records from the supplied source
page only. It resumes by canonical installation URL and record status, reports
source-manifest changes, skips already completed duplicates, keeps failures
retryable, and uses a requested-count-plus-20-percent attempt ceiling.

Phase 8 writes simple export files for each approved batch. Batch exports use
sequential folders, never overwrite by default, write Artelier CSV with the
configured headers/order, write one full JSON export, write one batch manifest,
append one export-history CSV, and regenerate consolidated CSV/JSON from SQLite.

## Run

```bash
python run_scraper.py
```

The launcher asks for:

1. An archive or listing page URL on `history.burningman.org`
2. The number of new records to process

It then prints:

- Entered URL
- Normalized URL
- Requested record count
- Source progress summary
- Resume menu when previous source state exists
- Page inspection report
- First record preview
- `PREVIEW COMPLETE`
- Preview approval menu
- Approved batch result report
- Export file paths

Phase 3 writes:

- `data/previews/source_manifest.json`
- `data/previews/raw_html/<url-hash>.html`
- `data/previews/raw_html/<url-hash>.metadata.json`

Phase 4 writes:

- `data/previews/<preview_run_id>/first_record_preview.json`
- `data/previews/<preview_run_id>/first_record_preview.csv`
- `data/previews/<preview_run_id>/first_record_preview.md`

Phase 5 writes:

- `data/previews/<preview_run_id>/artelier_import_preview.csv` when validation passes
- `data/previews/<preview_run_id>/full_research.json`
- `data/previews/<preview_run_id>/review.csv`

Phase 6 writes approval and pending-batch state to:

- `data/state/scraper_state.sqlite3`

Phase 7 updates `source_records` and `export_batches` in the same SQLite state
database. It reports attempted, succeeded, failed, skipped, duplicates, and the
next unprocessed record.

Phase 8 writes:

- `data/exports/burning_man/<year>/batches/batch_###/artelier_import.csv`
- `data/exports/burning_man/<year>/batches/batch_###/full_export.json`
- `data/exports/burning_man/<year>/batches/batch_###/batch_manifest.json`
- `data/exports/burning_man/export_history.csv`
- `data/exports/burning_man/<year>/consolidated/burning_man_<year>_all_completed.csv`
- `data/exports/burning_man/<year>/consolidated/burning_man_<year>_all_completed.json`

## Test

```bash
python -m unittest discover -s tests
```

## Configuration

Default settings live in `config/default.yaml`.

`max_records_per_run` sets the safety maximum for requested record counts.

`state_database_path` defaults to `data/state/scraper_state.sqlite3`.

`preview_manifest_path`, `raw_html_dir`, request delay, request timeout, retry
count, and user agent are also configurable in `config/default.yaml`.

The Artelier import schema and field mapping live in:

- `config/artelier_import_schema.yaml`
- `config/artelier_field_mapping.yaml`
