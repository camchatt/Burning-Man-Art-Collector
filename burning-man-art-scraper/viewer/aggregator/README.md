# Artelier Aggregator — how to use it

Source → Prepare → Review → Export. One workflow for artist websites and Burning Man CSVs.

## 1. Start the hub

```bash
py run_aggregator_hub.py
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/)

## 2. Wizard

### Source
Pick **Artist website** (default) or **Burning Man CSV**.

- Artist website: enter artist name + URL (for example Clara Berta / https://claraberta.com/). Continue as soon as both are filled; optional inspect is advisory only. Optional portfolio URL, crawl limit, and Playwright rendering.
- Burning Man CSV: upload a PlayaEvents ART file (`Title`, `Description`, `Link`, `UID`). Do **not** upload an Artelier export.

Optional inspection can confirm what was detected; it does not block starting an artist crawl.

### Prepare
Builds shared normalized review records, writes an immutable run folder under `data/runs/`, and opens the gallery. Burning Man years still also write `data/bm_ingest/<year>/` as before.

### Review
Filter for export-ready, low confidence, missing images, missing attribution, duplicates, or incomplete fields. Open a card to edit core fields and see source evidence. Blocked records show why they cannot export yet.

### Export
Download the currently filtered Artelier CSV using the authoritative **36-column** schema. Validate export-ready rows first when you want a strict check.

## API surface (source-neutral)

| Endpoint | Role |
|----------|------|
| `GET /api/sources` | List adapters |
| `POST /api/inspect` | Inspect file or artist URL |
| `POST /api/prepare-run` | Start preparation |
| `GET /api/run-progress?run_id=` | Progress / status |
| `GET /api/records?run_id=` | Normalized review records |
| `POST /api/records/update` | Save corrections |
| `POST /api/validate-upload` | Validate (year or run_id) |
| `POST /api/export-csv` | Download filtered Artelier CSV |

Legacy Burning Man routes (`/api/inspect-csv`, `/api/prepare`, year downloads) remain as compatibility wrappers.

## Where files live

| Path | Use |
|------|-----|
| `data/runs/<run_id>/` | Immutable artist (and mirrored BM) run folders |
| `data/bm_ingest/<year>/` | Burning Man year outputs (unchanged) |
| `config/artelier_import_schema.yaml` | Authoritative 36-column export contract |
