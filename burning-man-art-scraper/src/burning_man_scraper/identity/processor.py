from __future__ import annotations

import csv
import json
from pathlib import Path

from burning_man_scraper.enrichment.providers import NoOpSearchProvider, SearchProvider, select_search_provider
from burning_man_scraper.identity.models import IDENTITY_SCHEMA_VERSION, IdentityResult
from burning_man_scraper.identity.resolver import resolve_identity
from burning_man_scraper.verification.www_loader import load_www_records


def load_verification_rows(report_csv: Path) -> list[dict[str, str]]:
    with report_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_archive_websites(index_json: Path) -> dict[str, str]:
    if not index_json.exists():
        return {}
    payload = json.loads(index_json.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for record in payload.get("records", []):
        uid = record.get("uid")
        website = record.get("website_url")
        if uid and website:
            mapping[uid] = website
    return mapping


def load_www_artist_urls(www_dir: Path, year: int) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for record in load_www_records(www_dir, year=year):
        if record.uid and record.artist_url:
            mapping[record.uid] = record.artist_url
    return mapping


def resolve_identities_from_verification(
    *,
    year: int,
    verification_csv: Path,
    archive_index_json: Path | None = None,
    www_dir: Path | None = None,
    statuses: set[str] | None = None,
    search_client: SearchProvider | None = None,
    enable_search: bool = True,
    enable_page_fetch: bool = True,
    fetch_search_pages: bool = False,
    only_needing_search: bool = False,
    aliases_only: bool = False,
    limit: int | None = None,
    user_agent: str = "BurningManArtArchiveScraper/0.4 identity",
    progress_func=None,
    existing_results: list[IdentityResult] | None = None,
) -> list[IdentityResult]:
    rows = load_verification_rows(verification_csv)
    allowed = statuses or {"verified_online", "verified"}
    archive_sites = load_archive_websites(archive_index_json) if archive_index_json else {}
    www_sites = load_www_artist_urls(www_dir, year) if www_dir else {}

    if search_client is None and enable_search:
        search_client, _log = select_search_provider(user_agent=user_agent)
    if search_client is None:
        search_client = NoOpSearchProvider()

    from burning_man_scraper.identity.classifier import classify_archive_credit

    candidates = [row for row in rows if row.get("verification_status") in allowed]
    if aliases_only or only_needing_search:
        filtered = []
        for row in candidates:
            classification = classify_archive_credit(row.get("archive_artist"))
            if aliases_only and classification.credit_type not in {"alias_or_unknown", "alias_pair"}:
                continue
            if only_needing_search and not classification.needs_identity_search:
                continue
            filtered.append(row)
        candidates = filtered

    results_by_key: dict[str, IdentityResult] = {}
    for result in existing_results or []:
        results_by_key[_result_key(result.archive_uid, result.project_title)] = result

    total = len(candidates)
    processed = 0
    updated = 0
    for row in candidates:
        processed += 1
        uid = row.get("archive_uid") or None
        website = None
        if uid:
            website = archive_sites.get(uid) or www_sites.get(uid)
        if progress_func and (processed == 1 or processed % 5 == 0 or processed == total):
            progress_func(f"  identity {processed}/{total}: {row.get('project_title') or ''}")
        result = resolve_identity(
            year=year,
            project_title=row.get("project_title") or "",
            archive_credit=row.get("archive_artist") or "",
            archive_uid=uid,
            archive_url=row.get("archive_url") or None,
            artist_website=website,
            search_client=search_client,
            enable_search=enable_search,
            enable_page_fetch=enable_page_fetch,
            fetch_search_pages=fetch_search_pages,
            user_agent=user_agent,
            search_limit=5,
            max_queries=2,
        )
        results_by_key[_result_key(result.archive_uid, result.project_title)] = result
        updated += 1
        if limit is not None and updated >= limit:
            break

    # If we only updated a subset, ensure every verification candidate still appears.
    if existing_results is None and not only_needing_search:
        return sorted(results_by_key.values(), key=lambda item: item.project_title.lower())

    if existing_results is None and only_needing_search:
        # Build a full baseline for rows we did not search.
        for row in rows:
            if row.get("verification_status") not in allowed:
                continue
            key = _result_key(row.get("archive_uid"), row.get("project_title"))
            if key in results_by_key:
                continue
            baseline = resolve_identity(
                year=year,
                project_title=row.get("project_title") or "",
                archive_credit=row.get("archive_artist") or "",
                archive_uid=row.get("archive_uid") or None,
                archive_url=row.get("archive_url") or None,
                artist_website=(archive_sites.get(row.get("archive_uid") or "") or www_sites.get(row.get("archive_uid") or "")),
                search_client=NoOpSearchProvider(),
                enable_search=False,
                enable_page_fetch=False,
                user_agent=user_agent,
            )
            results_by_key[key] = baseline

    return sorted(results_by_key.values(), key=lambda item: item.project_title.lower())


def _result_key(archive_uid: str | None, project_title: str | None) -> str:
    if archive_uid:
        return f"uid:{archive_uid}"
    return f"title:{(project_title or '').strip().lower()}"


def load_identity_results(json_path: Path) -> list[IdentityResult]:
    if not json_path.exists():
        return []
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    results: list[IdentityResult] = []
    for row in payload.get("results", []):
        results.append(
            IdentityResult(
                year=int(row.get("year") or 0),
                project_title=row.get("project_title") or "",
                archive_uid=row.get("archive_uid") or None,
                archive_url=row.get("archive_url") or None,
                archive_credit=row.get("archive_credit") or "",
                credit_type=row.get("credit_type") or "",
                legal_name=row.get("legal_name") or None,
                playa_name=row.get("playa_name") or None,
                playa_name_confidence=row.get("playa_name_confidence") or "none",
                collective_name=row.get("collective_name") or None,
                named_people=[part for part in (row.get("named_people") or "").split(" | ") if part],
                identity_status=row.get("identity_status") or "unresolved",
                artist_website=row.get("artist_website") or None,
                evidence_urls=[part for part in (row.get("evidence_urls") or "").split(" | ") if part],
                notes=[part for part in (row.get("notes") or "").split(" | ") if part],
                search_queries=[part for part in (row.get("search_queries") or "").split(" | ") if part],
            )
        )
    return results


def write_identity_report(output_dir: Path, *, year: int, results: list[IdentityResult]) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"identity_report_{year}.csv"
    json_path = output_dir / f"identity_report_{year}.json"
    summary_path = output_dir / f"identity_summary_{year}.json"

    fieldnames = list(results[0].to_row().keys()) if results else [
        "year",
        "project_title",
        "archive_uid",
        "archive_url",
        "archive_credit",
        "credit_type",
        "legal_name",
        "playa_name",
        "playa_name_confidence",
        "collective_name",
        "named_people",
        "resolved_people",
        "identity_status",
        "artist_website",
        "evidence_urls",
        "notes",
        "search_queries",
    ]

    json_path.write_text(
        json.dumps(
            {
                "schema_version": IDENTITY_SCHEMA_VERSION,
                "year": year,
                "results": [result.to_row() for result in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    try:
        _write_csv(csv_path, fieldnames, results)
    except PermissionError:
        csv_path = output_dir / f"identity_report_{year}_write.csv"
        _write_csv(csv_path, fieldnames, results)

    status_counts: dict[str, int] = {}
    credit_counts: dict[str, int] = {}
    playa_count = 0
    for result in results:
        status_counts[result.identity_status] = status_counts.get(result.identity_status, 0) + 1
        credit_counts[result.credit_type] = credit_counts.get(result.credit_type, 0) + 1
        if result.playa_name:
            playa_count += 1
    summary = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "year": year,
        "project_count": len(results),
        "playa_name_separated": playa_count,
        "status_counts": status_counts,
        "credit_type_counts": credit_counts,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "summary": summary_path}


def _write_csv(csv_path: Path, fieldnames: list[str], results: list[IdentityResult]) -> None:
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(result.to_row())
