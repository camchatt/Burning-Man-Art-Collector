# Burning Man Art Aggregator — start here

Turn one year’s PlayaEvents ART spreadsheet into an Artelier-ready CSV, with hero photos and proof links from the Burning Man History Archive.

## Open the hub

```bash
cd burning-man-art-scraper
py run_aggregator_hub.py
```

Then open [http://127.0.0.1:8765/](http://127.0.0.1:8765/) and hard-refresh if the page looks stale.

If a year has already been prepared, the portal **auto-loads the latest year** into the gallery. Use “Open year” only to switch years.

## What each step means

1. **Choose year file** — Pick `PlayaEvents-YYYY_ART.csv` (Title / Description / Link / UID). Do not pick an Artelier export (`project_title`, `bm_uid`). The hub never overwrites ART templates under `What When Where Files/`.
2. **Match & build** — Match each project to the History Archive (heroes + proof links), attach names from local/cache data, write CSVs under `data/bm_ingest/<year>/` and a gallery preview under `What When Where Files/aggregator_previews/`.
3. **Review & download** — Check the gallery, then download `artelier_bm_upload_<year>.csv` for Artelier.

UI details: [`burning-man-art-scraper/viewer/aggregator/README.md`](burning-man-art-scraper/viewer/aggregator/README.md)

## Artelier handoff (scraper CSV v1)

The Aggregator’s internal `artelier_bm_upload_<year>.csv` is for this collector. To produce the **versioned Artelier import contract** file:

```bash
cd burning-man-art-scraper
py run_artelier_scraper_export.py --year 2016
```

Contract copy: [`burning-man-art-scraper/contracts/artelier-scraper-v1/`](burning-man-art-scraper/contracts/artelier-scraper-v1/). The collector does not write to Artelier’s database.

## Disk hygiene

- Hero images are remote URLs only (never downloaded).
- Year CSV outputs overwrite under `data/bm_ingest/{year}/` only when you confirm rebuild.
- Gallery JSON may be updated under `What When Where Files/aggregator_previews/` (never ART CSVs).
- Clean leftovers: `py run_aggregator_hub.py --cleanup`

## Key paths

| Path | Purpose |
|------|---------|
| `burning-man-art-scraper/viewer/aggregator/` | Hub UI |
| `burning-man-art-scraper/data/bm_ingest/{year}/` | Artelier CSVs + backup preview JSON |
| `What When Where Files/PlayaEvents-*_ART.csv` | Source templates (hub never overwrites) |
| `What When Where Files/aggregator_previews/` | Gallery preview JSON next to WWW |
| `burning-man-art-scraper/data/deploy/{year}/` | Validated deploy package |
