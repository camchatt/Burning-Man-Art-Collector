# `artelier_scraper_csv_v1` integration guide

`artelier_scraper_csv_v1` is the stable file-exchange boundary between external
collector projects and Artelier’s Registry staging importer.

Collectors and Artelier remain independently deployable. A collector produces
CSV files; it does not connect to Artelier’s database, use Artelier table names,
or publish Registry records.

## Contract artifacts

- TypeScript definition: `src/lib/artelierScraperCsvV1.ts`
- JSON Schema: `schemas/artelier_scraper_csv_v1.schema.json`
- Sample: `examples/artelier_scraper_csv_v1.csv`
- Legacy behavior: `docs/artelier-scraper-csv-existing-behavior.md`

## Finalized CSV header

```csv
contract_version,source_name,source_namespace,source_record_id,source_record_url,contributor_kind,artist_name,artist_alias,artist_website,organization_name,project_title,proof_title,proof_external_url,proof_excerpt,source_granularity,project_type,tags,materials,fabrication_methods,context_tags,what_they_did,why_it_matters,contributor_role,collaboration_status,collaborators,location,year,dimensions,client_or_commissioner,institution,image_urls,proof_confidence,classification_confidence,description_confidence,classification_source,review_status,permission_status,import_notes
```

Every v1 row must set:

```text
contract_version=artelier_scraper_csv_v1
```

Do not mix contract versions in one file.

## CSV encoding and cells

- Encode files as UTF-8 with one header row.
- Headers are case-sensitive lowercase `snake_case`.
- Quote cells containing commas, double quotes, or line breaks according to
  RFC 4180.
- Represent list fields (`tags`, `materials`, `fabrication_methods`, and
  `context_tags`) with `|`.
- `image_urls` may be one URL, a `|`-delimited list, a comma-delimited URL
  list, or a JSON array encoded inside the CSV cell.
- URLs should be absolute HTTP(S) URLs.
- Unknown columns are allowed. Prefix source-owned extensions with a stable
  namespace such as `bm_`.

## Minimum usable data

A row must contain at least one of:

- `project_title`
- `artist_name`
- `organization_name`
- `proof_external_url`

For useful review records, collectors should provide a project title, a source
record URL or proof URL, and the best available identity.

Missing or uncertain identity information does not need to be guessed. Use
`contributor_kind=unknown` and explain uncertainty in `import_notes` or a
source-namespaced field.

## Identity fields

`contributor_kind` accepts:

- `person`
- `organization`
- `collective`
- `unknown`

For a person:

- `artist_name` is the canonical/publicly documented name.
- `artist_alias` is an alternate Burner, stage, or credited name.
- `artist_website` supports identity matching.

For an organization:

- set `contributor_kind=organization`;
- put the canonical organization identity in `organization_name`;
- use `artist_name` only when the source separately credits that text.

Collectives and unknown identities remain in staging until a reviewer resolves
how they should be represented. Artelier does not require collectors to resolve
ambiguous identities.

## Universal source provenance

- `source_name`: human-readable source, for example `Burning Man Art Archive`
- `source_namespace`: stable producer/source key, for example
  `burning_man_2025_playaevents`
- `source_record_id`: source-system identifier, preserved as text
- `source_record_url`: canonical source record page

`proof_external_url` is evidence supporting the Registry claim. It may be the
same as `source_record_url`, but the two fields have different meanings.

Unknown source-specific fields are stored in staging `raw_metadata`. The entire
original row is also stored in `raw_row`.

## Controlled values

Confidence fields (`proof_confidence`, `classification_confidence`, and
`description_confidence`):

```text
high | medium | low
```

`classification_source`:

```text
manual | scraper | ai | imported
```

`source_granularity`:

```text
Individual project page
Portfolio index page
Gallery caption
Bio/CV reference
Press article
Image-only inference
```

`review_status`:

```text
Needs review
Approved
Rejected
Needs better source
Needs permission
Duplicate
```

`permission_status`:

```text
Needs permission
Permission granted
Permission not required
Do not publish
Unknown
```

When omitted, Artelier applies staging defaults:

- `classification_source=scraper`
- `review_status=Needs review`
- `permission_status=Needs permission`

Invalid controlled values are surfaced as warnings and sanitized to safe
staging defaults rather than trusted.

## Duplicate handling

The preview compares:

- project titles, years, slugs, contributors, proof URLs, and image URLs;
- canonical and alternate contributor names;
- contributor and organization website domains;
- organization names;
- proof source URLs;
- repeated project/identity pairs within the uploaded file.

Matches are candidates for admin review. A collector must not attempt to use
Artelier database IDs.

## Import lifecycle and publication

1. An admin uploads the CSV and reviews version, provenance, identity,
   warnings, unknown metadata, and duplicate candidates.
2. Accepted rows are written only to Registry staging, with remote media
   candidates.
3. A reviewer may edit unresolved identity information.
4. Promotion creates private, pending, or draft Registry records.
5. Publishing is a separate explicit action.

No scraper CSV import automatically publishes a project, contributor,
organization, contribution, proof record, or image.

## Backward compatibility

CSV files without a `contract_version` header are accepted as
`legacy_scraper`. Their parser, aliases, staging defaults, and review behavior
remain compatible with the pre-v1 importer.

If a `contract_version` header is present, its non-empty value must be
`artelier_scraper_csv_v1`. Unsupported or mixed versions are blocked during
preview.
