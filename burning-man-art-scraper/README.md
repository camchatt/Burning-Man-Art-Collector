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

## Resolve artist identities

After verification, resolve archive credits into legal names / collectives and
separate playa names when the pattern is reliable (`aka` / `a.k.a.` /
clear parenthetical nicknames).

```bash
python run_identity.py --year 2022
```

Outputs in `data/verification/<year>/`:

- `identity_report_<year>.csv` (includes `legal_name` and `playa_name` columns)
- `identity_report_<year>.json`
- `identity_summary_<year>.json`

Use `--skip-search` to classify credits only without web lookup.

## Burning Man Artelier ingest (upload-ready CSV)

Builds a `burningman.artelier` upload CSV from the WWW ART spreadsheet plus
cached verification/archive data. **Default run makes no network requests.**

```bash
python run_bm_ingest.py --year 2022
```

Optional hero page probing only when explicitly requested:

```bash
python run_bm_ingest.py --year 2022 --fetch-missing-heroes
```

Schema and rules:

- `config/burning_man_schema.json`
- `docs/burning_man_ingestion_rules.md`

Outputs in `data/bm_ingest/<year>/`:

- `artelier_bm_upload_<year>.csv` (Artelier core + BM extensions)
- `artelier_core_only_<year>.csv`
- `review_queue_<year>.csv`
- `ingest_summary_<year>.json`
- `aggregator_view_<year>.json` (pre-upload preview data)

Ingest also merges GPS from
`../What When Where Files/Coordinate Data/GIS-{year}.json` (or `art_{year}.json`)
into `playa_latitude` / `playa_longitude` and prefers the GIS `location_string`
for `playa_address`.

To patch existing ready upload CSVs before Artelier import:

```bash
py -3 scripts/enrich_artelier_upload_coords.py --dry-run
py -3 scripts/enrich_artelier_upload_coords.py
```

That updates files under `../What When Where Files/Artelier Upload/`.

### Aggregator hub (recommended daily workflow)

The hub is the main UI: preview, upload a new year ART CSV, validate Artelier
format, and prepare a deploy package.

```bash
python run_aggregator_hub.py
```

Opens [http://127.0.0.1:8765/](http://127.0.0.1:8765/) (also auto-starts on
folder open in Cursor when automatic tasks are allowed).

**In the UI**

1. **Process another year** — upload `PlayaEvents-YYYY_ART.csv`, run offline ingest.
2. **Preview** — filter Needs attention; check credits, hero, proof, playa address.
3. **Validate CSV** — Artelier 36-column core schema check.
4. **Prepare deploy package** — writes `data/deploy/<year>/artelier_core_only_<year>.csv`.
5. Upload that CSV in Artelier admin (set `admin_import_url` in
   `config/artelier_deploy.yaml` to enable **Open Artelier import**).

Full walkthrough: [`viewer/aggregator/README.md`](viewer/aggregator/README.md)  
Repo overview: [`../START_HERE.md`](../START_HERE.md) and [`../README.md`](../README.md)

Cleanup temps without starting the server:

```bash
python run_aggregator_hub.py --cleanup
```

## Test

```bash
python -m unittest discover -s tests
```

## Verify projects against the official archive

The verification pipeline uses `history.burningman.org` as the primary source,
cross-checks optional What/When/Where CSV references, validates image URLs, and
captures attribution metadata for Artelier.

```bash
python run_verify.py --year 2022
```

Useful options:

- `--scope export|www|archive|all`
- `--skip-image-validation` for faster index-only runs
- `--check-legacy-links` to test old PlayaEvents URLs

Outputs are written to `data/verification/<year>/`:

- `verification_report_<year>.csv`
- `verification_report_<year>.json`
- `image_manifest_<year>.json`
- `archive_index_<year>.json`
- `verification_summary_<year>.json`

You can also run verification from the interactive scraper menu (option 5).

## Configuration

Default settings live in `config/default.yaml`.

`max_records_per_run` sets the safety maximum for requested record counts.

`state_database_path` defaults to `data/state/scraper_state.sqlite3`.

`preview_manifest_path`, `raw_html_dir`, request delay, request timeout, retry
count, and user agent are also configurable in `config/default.yaml`.

The Artelier import schema and field mapping live in:

- `config/artelier_import_schema.yaml`
- `config/artelier_field_mapping.yaml`
