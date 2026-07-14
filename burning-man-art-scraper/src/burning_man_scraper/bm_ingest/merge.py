from __future__ import annotations

from collections import Counter
from pathlib import Path

from burning_man_scraper.artelier_schema import load_import_schema, slugify
from burning_man_scraper.bm_ingest.contributors import normalize_contributor
from burning_man_scraper.bm_ingest.hero import resolve_hero
from burning_man_scraper.bm_ingest.identity_join import (
    collapse_person_or_org,
    contributor_from_identity,
    identity_is_useful,
)
from burning_man_scraper.bm_ingest.schema import BM_EXTENSION_HEADERS, REVIEW_FLAGS_ALLOWED
from burning_man_scraper.bm_ingest.sources import (
    default_www_dir,
    load_year_sources,
    lookup,
    lookup_identity,
)
from burning_man_scraper.bm_ingest.writer import write_ingest_outputs
from burning_man_scraper.verification.models import WwwReferenceRecord
from burning_man_scraper.verification.www_loader import assert_art_csv_matches_year


def build_ingest_rows(
    *,
    project_root: Path,
    year: int,
    www_dir: Path | None = None,
    www_file: Path | None = None,
    fetch_missing_heroes: bool = False,
    user_agent: str = "BurningManArtBmIngest/0.1",
) -> tuple[list[dict[str, str]], dict]:
    artelier_schema = load_import_schema(project_root / "config" / "artelier_import_schema.yaml")
    sources = load_year_sources(
        project_root=project_root,
        year=year,
        www_dir=www_dir,
        www_file=www_file,
    )
    www_records: list[WwwReferenceRecord] = sources["www"]
    if not www_records:
        raise FileNotFoundError(f"No WWW ART records found for year {year}")

    title_counts = Counter(record.normalized_title for record in www_records)
    rows: list[dict[str, str]] = []
    stats = {
        "source_rows": len(www_records),
        "uid_matches": 0,
        "verification_matches": 0,
        "identity_cache_matches": 0,
        "identity_local_fallbacks": 0,
        "identity_title_fallback_matches": 0,
        "title_fallback_match_count": 0,
        "duplicate_uid_count": 0,
        "resolved_people": 0,
        "resolved_organizations": 0,
        "resolved_multiple_credits": 0,
        "burner_names_found": 0,
        "hero_images_found": 0,
        "proof_links_found": 0,
        "rows_missing_primary_name": 0,
        "rows_missing_hero": 0,
        "network_requests_attempted": 0 if not fetch_missing_heroes else None,
        "processing_mode": "fast_upload" if not fetch_missing_heroes else "fast_upload+optional_hero_fetch",
        "cache_inventory": sources.get("cache_inventory") or {},
    }
    seen_uids: set[str] = set()

    for record in www_records:
        provenance: list[str] = ["www"]
        flags: list[str] = []
        uid = record.uid

        verification = lookup(sources["verification"], uid=uid, year=year, title=record.title)
        archive = lookup(sources["archive"], uid=uid, year=year, title=record.title)
        images = lookup(sources["images"], uid=uid, year=year, title=record.title)
        collector = lookup(sources["collector"], uid=uid, year=year, title=record.title)
        identity, identity_mode = lookup_identity(
            sources["identity"], uid=uid, year=year, title=record.title
        )

        if verification:
            provenance.append("verification")
            stats["verification_matches"] += 1
            uid = uid or (verification.get("archive_uid") or verification.get("www_uid") or None)
        if archive:
            provenance.append("archive_index")
            uid = uid or (archive.get("uid") or None)
        if images:
            provenance.append("image_manifest")
        if collector:
            provenance.append("collector_export")

        if uid:
            stats["uid_matches"] += 1
            if uid in seen_uids:
                stats["duplicate_uid_count"] += 1
                flags.append("duplicate_candidate")
            seen_uids.add(uid)

        if not archive and not verification:
            flags.append("missing_archive_cache")

        if title_counts[record.normalized_title] > 1:
            flags.append("duplicate_candidate")

        source_credit = _first_nonempty(
            (identity or {}).get("archive_credit") if identity else None,
            archive.get("artist_display_text") if archive else None,
            verification.get("archive_artist") if verification else None,
            (collector.get("original") or {}).get("artist_display_text") if collector else None,
            (collector.get("mapped") or {}).get("contributor_name") if collector else None,
        )

        if identity_is_useful(identity):
            contributor = contributor_from_identity(identity or {}, fallback_source_credit=source_credit)
            provenance.append("identity_report")
            stats["identity_cache_matches"] += 1
            if identity_mode == "title":
                stats["identity_title_fallback_matches"] += 1
                stats["title_fallback_match_count"] += 1
        else:
            contributor = normalize_contributor(source_credit)
            provenance.append("identity_local_fallback")
            stats["identity_local_fallbacks"] += 1

        flags.extend(contributor.review_flags)

        honorarium = _first_nonempty(
            (collector.get("original") or {}).get("honoraria_status") if collector else None,
            (collector.get("original") or {}).get("honorarium_status") if collector else None,
        )
        if not honorarium:
            flags.append("honorarium_unknown")

        proof_url = _first_nonempty(
            archive.get("canonical_source_url") if archive else None,
            verification.get("archive_url") if verification else None,
            record.legacy_link,
            record.artist_url,
            (collector.get("mapped") or {}).get("proof_external_url") if collector else None,
        )
        website = _first_nonempty(
            (identity or {}).get("artist_website") if identity else None,
            archive.get("website_url") if archive else None,
            record.artist_url,
            (collector.get("mapped") or {}).get("contributor_website") if collector else None,
        )
        summary = _first_nonempty(
            archive.get("description") if archive else None,
            record.description,
            (collector.get("mapped") or {}).get("project_summary") if collector else None,
        )
        hometown = _first_nonempty(
            archive.get("artist_location") if archive else None,
            (collector.get("mapped") or {}).get("project_location") if collector else None,
            (collector.get("original") or {}).get("artist_location") if collector else None,
        )

        # Standard upload never probes the open web for heroes unless explicitly requested.
        hero = resolve_hero(
            uid=uid,
            title=record.title,
            year=year,
            verification_row=verification,
            archive_record=archive,
            image_entry=images,
            collector_bundle=collector,
            proof_url=proof_url,
            artist_url=website,
            fetch_missing=fetch_missing_heroes,
            user_agent=user_agent,
        )
        if hero.provenance:
            provenance.append(f"hero:{hero.provenance}")
        flags.extend(hero.review_flags or [])

        display_name = contributor.contributor_display_name
        if not display_name:
            stats["rows_missing_primary_name"] += 1
        if hero.hero_image_url:
            stats["hero_images_found"] += 1
        else:
            stats["rows_missing_hero"] += 1
        if proof_url:
            stats["proof_links_found"] += 1
        if contributor.playa_name:
            stats["burner_names_found"] += 1

        person_or_org = collapse_person_or_org(contributor.contributor_kind)
        if person_or_org == "person":
            stats["resolved_people"] += 1
        elif person_or_org == "org":
            stats["resolved_organizations"] += 1
        elif person_or_org == "multiple":
            stats["resolved_multiple_credits"] += 1

        core = {column.name: column.default_value for column in artelier_schema.columns}
        core.update(
            {
                "project_title": record.title,
                "project_slug": slugify(record.title) or f"burning-man-{year}-untitled",
                "project_type": record.installation_type or "",
                "project_year": str(year),
                "project_location": hometown or "",
                "project_summary": summary or "",
                "hero_image_url": hero.hero_image_url,
                "contributor_name": display_name,
                "contributor_slug": slugify(display_name) if display_name else "",
                "role_title": "Artist",
                "contributor_website": website or "",
                "collaboration_status": "Collective project"
                if contributor.contributor_kind in {"collective", "studio", "organization", "theme_camp", "multiple"}
                else "",
                "contribution_title": f"Artist contribution to {record.title}" if record.title else "",
                "what_they_did": summary or "",
                "public_credit_language": hero.hero_image_attribution,
                "verification_status": (verification.get("verification_status") if verification else "")
                or "documented",
                "proof_title": record.title,
                "proof_type": "Installation detail page",
                "proof_external_url": proof_url or "",
                "proof_description": summary or "",
            }
        )

        extensions = {header: "" for header in BM_EXTENSION_HEADERS}
        extensions.update(
            {
                "bm_uid": uid or "",
                "bm_year": str(year),
                "bm_event_name": "Burning Man",
                "playa_address": record.playa_address or "",
                "playa_latitude": "",
                "playa_longitude": "",
                "honorarium_status": honorarium or "",
                "theme_camp": record.theme_camp or "",
                "installation_type": record.installation_type or "",
                "source_artist_credit": contributor.source_artist_credit or source_credit,
                "contributor_display_name": display_name,
                "additional_contributor_credits": contributor.additional_contributor_credits,
                "contributor_kind": contributor.contributor_kind,
                "contributor_first_name": contributor.contributor_first_name,
                "contributor_last_name": contributor.contributor_last_name,
                "playa_name": contributor.playa_name,
                "playa_name_confidence": contributor.playa_name_confidence,
                "bm_hero_image_source_url": hero.hero_image_url,
                "hero_image_source_page": hero.hero_image_source_page,
                "hero_image_attribution": hero.hero_image_attribution,
                "hero_image_confidence": hero.hero_image_confidence,
                "review_flags": _format_flags(flags),
                "source_provenance": "|".join(_unique(provenance)),
            }
        )

        row = {**core, **extensions}
        rows.append(row)

    if stats["network_requests_attempted"] is None:
        stats["network_requests_attempted"] = 0
    stats["export_rows"] = len(rows)
    return rows, stats


def run_ingest(
    *,
    project_root: Path,
    year: int,
    www_dir: Path | None = None,
    www_file: Path | None = None,
    fetch_missing_heroes: bool = False,
    output_dir: Path | None = None,
    user_agent: str = "BurningManArtBmIngest/0.1",
    original_filename: str | None = None,
) -> dict[str, Path]:
    if www_file is not None:
        assert_art_csv_matches_year(www_file, year, original_filename=original_filename)
    else:
        resolved_www = www_dir or default_www_dir(project_root)
        candidate = resolved_www / f"PlayaEvents-{year}_ART.csv"
        if candidate.exists():
            assert_art_csv_matches_year(candidate, year)

    rows, stats = build_ingest_rows(
        project_root=project_root,
        year=year,
        www_dir=www_dir,
        www_file=www_file,
        fetch_missing_heroes=fetch_missing_heroes,
        user_agent=user_agent,
    )
    artelier_schema = load_import_schema(project_root / "config" / "artelier_import_schema.yaml")
    target = output_dir or (project_root / "data" / "bm_ingest" / str(year))
    return write_ingest_outputs(
        output_dir=target,
        year=year,
        rows=rows,
        artelier_headers=artelier_schema.headers,
        fetch_missing_heroes=fetch_missing_heroes,
        project_root=project_root,
        stats=stats,
    )


def _first_nonempty(*values: str | None) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


def _format_flags(flags: list[str]) -> str:
    allowed = set(REVIEW_FLAGS_ALLOWED)
    cleaned = [flag for flag in _unique(flags) if flag in allowed]
    return "|".join(cleaned)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
