# Collector adapter notes

Artelier owns `artelier_scraper_csv_v1`. This folder is a local copy for validation.

## Artelier dual handoff (2026+)

Artelier's unified Burning Man upload wizard
(`/admin/registry/burning-man-import`) accepts **both**:

| Collector file | Artelier write paths |
|---|---|
| `artelier_bm_upload_{year}.csv` (hybrid) | Registry staging **and** Burning Man placements, with separate confirms |
| `artelier_scraper_csv_v1_{year}.csv` (true v1) | Registry staging via Expanded scraper import (or unified wizard when detected) |

Hybrid files do **not** require `contract_version`. Artelier detects them as
`bm_upload_hybrid` when headers include `project_title` plus `bm_uid` or
`playa_address`, then aliases columns into scraper staging fields. BM extension
columns stay in `raw_metadata`. Registry rows never auto-publish.

### Follow-up: align the true v1 exporter

Keep `artelier_bm_upload_*` for the Aggregator portal / placement path.

Ensure `run_artelier_scraper_export.py` emits a **true** `artelier_scraper_csv_v1`
file with:

- `contract_version=artelier_scraper_csv_v1` on every row
- canonical header order from Artelier's contract
- `contributor_kind` already mapped to `person|organization|collective|unknown`
  (`individual` → `person`; `multiple|studio|theme_camp` → `collective`)
- BM-only fields either omitted from canonical columns or carried as namespaced
  companion metadata / preserved columns that Artelier stores in `raw_metadata`
- image URLs in `image_urls` (pipe-separated), with hero primary first

When header renames land, update:

- `field_mapping_{year}.md`
- `compatibility_vs_bm_upload_{year}.md`

Artelier should keep accepting the older hybrid export during transition.

## Export command

From `burning-man-art-scraper/`:

```bash
py run_bm_ingest.py --year 2016
py run_artelier_scraper_export.py --year 2016
```

Optional:

```bash
py run_artelier_scraper_export.py --year 2016 --source-csv "path\to\artelier_bm_upload_2016.csv"
```

Outputs land in:

```text
data/exports/artelier_scraper_csv_v1/<year>/<timestamp>/
  artelier_scraper_csv_v1_<year>.csv
  artelier_scraper_csv_v1_<year>_rejected.csv
  validation_summary_<year>.json
  validation_report_<year>.md
  field_mapping_<year>.md
  compatibility_vs_bm_upload_<year>.md
```

## Rules

- Do not write to Artelier databases from this repo.
- Do not mutate `data/bm_ingest/` uploads; export to a new run directory.
- Keep collector-internal CSV (`artelier_bm_upload_*.csv`) as-is for the Aggregator portal
  and for Artelier's hybrid unified upload (placements + staging aliases).
- Prefer the true scraper v1 export for pure Registry staging handoff; hybrid
  remains valid while Artelier aliases both.
