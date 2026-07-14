# Burning Man Artelier ingestion rules

Reusable year-scoped pipeline for `burningman.artelier`.  
Schema contract: [`config/burning_man_schema.json`](../config/burning_man_schema.json).

## Goal

Produce an upload-ready CSV that:

1. Starts from the original PlayaEvents ART spreadsheet (WWW).
2. Enriches from BM Art Collector caches (verification, identity, archive, images, collector exports).
3. Preserves the standard Artelier 36-column core, then appends BM extensions.
4. Performs **no open-web identity searches** in the standard portal/`fast_upload` path.

## Processing modes

| Mode | When | Network identity search |
|------|------|-------------------------|
| `fast_upload` (default) | Aggregator portal upload / `run_bm_ingest.py` | **Never** (`network_requests_attempted = 0` for identity) |
| `deep_research` | Separate explicit `run_identity.py` only | Optional, bounded; never auto-started by upload |

The expensive ~hours-long identity/alias web search belongs only to `deep_research`. Local rule-based classification is required as the fallback inside `fast_upload`.

## Inputs (per year)

| Priority | Source | Path pattern |
|----------|--------|--------------|
| Base | WWW ART CSV | `What When Where Files/PlayaEvents-{year}_ART.csv` |
| Cache | Verification report | `data/verification/{year}/verification_report_{year}.csv` |
| Cache | Identity report | `data/verification/{year}/identity_report_{year}.csv` (or `.json`) |
| Cache | Archive index | `data/verification/{year}/archive_index_{year}.json` |
| Cache | Image manifest | `data/verification/{year}/image_manifest_{year}.json` |
| Cache | Collector consolidated export | `data/exports/burning_man/{year}/consolidated/burning_man_{year}_all_completed.json` (optional) |
| Fallback | Local identity normalization | classifier / `normalize_contributor` (offline) |

Prefer saved verified/identity values over recomputing from the raw credit string.

Do **not** re-run archive verification crawls or alias web-search identity resolution as part of default ingest.

## Join keys

1. Primary: `UID` / `archive_uid` / `www_uid` → `bm_uid`
2. Fallback: normalized title + year (identity title fallback is skipped when conflicting identity rows exist)

## Field precedence

| Field | Precedence |
|-------|------------|
| `project_title` | WWW Title |
| `project_summary` | archive description → WWW Description |
| `project_year` / `bm_year` | WWW / run year |
| `project_location` | archive `artist_location` (hometown only; not playa address) |
| `project_type` | WWW Type when present |
| `proof_external_url` | archive canonical URL → WWW Link → WWW Extra |
| `playa_address` | WWW Where |
| `theme_camp` | WWW Camp |
| `installation_type` | WWW Type |
| Contributor fields | **identity_report** (resolved/partial/structured) → local `normalize_contributor` on archive/verification credit |
| Hero fields | verification/image_manifest (active) → archive image_urls → collector hero |

## Contributor normalization

Portal preview labels (canonical export columns in parentheses):

- **Person or Organization** (`contributor_kind`, collapsed to person/org/multiple/unknown in the UI)
- **Name** (`contributor_display_name` / Artelier `contributor_name`)
- **Alt / Burner Name** (`playa_name`)
- **Additional Credits** (`additional_contributor_credits`)

Rules:

- Always set `source_artist_credit` to the untouched source credit.
- Prefer cached identity when useful; do not dump multi-entity `legal_name` blobs (e.g. `Jeff Tangen; Disciples of the Dust`) into a single name — primary person first, remainder in additional credits.
- Never use Burner/playa name as the primary name when a credible canonical/real name is known.
- Never put the real/canonical name into `playa_name` / alt Burner name.
- Multi-contributor export behavior (locked): **one primary contributor** + preserve remaining people/orgs in `additional_contributor_credits` (no multiple Artelier contribution rows yet).
- Allowed `contributor_kind` values: `individual`, `organization`, `collective`, `multiple`, `studio`, `theme_camp`, `unknown`.
- Uncertain classifications are flagged (`contributor_kind_uncertain`, `playa_name_uncertain`, `identity_needs_review`) rather than invented via web search.

## Hero images

Canonical Artelier display field:

- core `hero_image_url`

BM extensions:

- `bm_hero_image_source_url` (non-colliding BM copy / provenance; **not** a second `hero_image_url`)
- `hero_image_source_page`
- `hero_image_attribution`
- `hero_image_confidence` (`high` \| `medium` \| `low` \| `needs_review`)

Cache-first ranking only in `fast_upload`. Missing heroes produce `hero_missing` / review state — they do **not** trigger open-web identity search.

## Review flags (closed set)

Pipe-delimited codes only:

- `duplicate_candidate`
- `name_split_uncertain`
- `playa_name_uncertain`
- `contributor_kind_uncertain`
- `identity_needs_review`
- `hero_missing`
- `hero_needs_review`
- `missing_archive_cache`
- `honorarium_unknown`

## Outputs

Under `data/bm_ingest/{year}/`:

- `artelier_bm_upload_{year}.csv` — **primary** success output: core 36 + BM extensions
- `artelier_core_only_{year}.csv` — secondary 36-column slice only
- `review_queue_{year}.csv` — rows with any `review_flags`
- `ingest_summary_{year}.json` — includes cache hits, identity fallbacks, `network_requests_attempted`

Portal deploy/download must treat the full BM upload CSV as primary. Core-only must not be presented as the sole “Artelier ready” artifact.

## CLI

```bash
python run_bm_ingest.py --year 2022
python run_bm_ingest.py --year 2022 --fetch-missing-heroes
```

Default: offline, cache-only `fast_upload`.

Optional deep research (separate, explicit, not portal upload):

```bash
python run_identity.py --year 2022
```
