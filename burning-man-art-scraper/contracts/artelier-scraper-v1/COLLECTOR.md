# Collector adapter notes

Artelier owns `artelier_scraper_csv_v1`. This folder is a local copy for validation.

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
- Keep collector-internal CSV (`artelier_bm_upload_*.csv`) as-is for the Aggregator portal.
- Only the scraper v1 export is the Artelier handoff file.
