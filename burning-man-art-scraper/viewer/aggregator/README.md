# Aggregator Hub — how to use it

Three steps. That’s the whole product surface.

## 1. Start the hub

```bash
py run_aggregator_hub.py
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/)

## 2. Wizard

### Upload
Choose a PlayaEvents ART CSV (`Title`, `Description`, `Link`, `UID`, …). Do **not** upload an Artelier / Aggregator export (`project_title`, `bm_uid`, …).

The year is detected automatically. The upload is used for this prepare run only; **What When Where Files are never overwritten by the hub.**

### Prepare
One button runs:

1. Use the uploaded template (library untouched)
2. Archive verification (heroes + proof links — uses network)
3. Optional: online identity checks (names / Burner names — can be slow; limited batch)
4. Build the preloader preview (offline merge)

If the year already has Aggregator outputs, confirm overwrite first (outputs only — not WWW templates).

### Review & download
Use the gallery filters (Needs review, Has hero photo, etc.) or search. **Download Artelier CSV** exports only the projects currently shown; the filename includes the filter name when narrowed.

Advanced bits (core slice, validate, cleanup, admin URL) live under **More options**.

## Template library

Keep real PlayaEvents files under `What When Where Files/`. If a year was previously corrupted by an export overwrite, restore that year’s CSV from PlayaEvents / What When Where (there may be a `.INVALID_ARTELIER_EXPORT` quarantine sibling).

## Outputs

| File | Use |
|------|-----|
| `data/bm_ingest/<year>/artelier_bm_upload_<year>.csv` | Primary Artelier/BM upload |
| `data/bm_ingest/<year>/aggregator_view_<year>.json` | Preloader data |
| `data/verification/<year>/` | Verification + identity caches created by Prepare |
