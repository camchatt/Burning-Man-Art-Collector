# Aggregator Hub — how to use it

Three steps. That’s the whole product surface.

## 1. Start the hub

```bash
py run_aggregator_hub.py
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/)

If years are already prepared, the **latest year auto-loads** into the gallery. Use **Open year** only when switching years.

## 2. Wizard

### Upload
Choose a PlayaEvents ART CSV (`Title`, `Description`, `Link`, `UID`, …). Do **not** upload an Artelier / Aggregator export (`project_title`, `bm_uid`, …).

The year is detected automatically. The upload is used for this prepare run only; **PlayaEvents ART templates under What When Where Files are never overwritten.**

### Prepare
One button runs:

1. Use the uploaded template (ART library untouched)
2. Archive verification (heroes + proof links — uses network)
3. Optional: online identity checks (names / Burner names — can be slow; limited batch)
4. Build the gallery preview + Artelier CSV

If the year already has Aggregator outputs, confirm overwrite first (outputs only — not WWW ART templates).

### Review & download
Use the gallery filters (Needs review, Has hero photo, etc.) or search. **Download Artelier CSV** exports only the projects currently shown; the filename includes the filter name when narrowed.

Advanced bits (core slice, validate, cleanup, admin URL) live under **More options**.

## Where files live

| File | Use |
|------|-----|
| `What When Where Files/PlayaEvents-<year>_ART.csv` | Source year template |
| `What When Where Files/aggregator_previews/aggregator_view_<year>.json` | Gallery preview (auto-loaded by the portal) |
| `data/bm_ingest/<year>/artelier_bm_upload_<year>.csv` | Primary Artelier/BM download |
| `data/bm_ingest/<year>/aggregator_view_<year>.json` | Backup of the same preview |
| `data/verification/<year>/` | Verification + identity caches from Prepare |

Gallery JSON is derived (not a replacement for the ART template). Keep ART CSVs and previews in separate WWW subfolders so source vs processed stays clear.
