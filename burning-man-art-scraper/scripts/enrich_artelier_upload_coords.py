#!/usr/bin/env python3
"""Merge Coordinate Data GIS GPS into Artelier Upload ready CSVs.

Looks under:
  What When Where Files/Coordinate Data/GIS-{year}.json (or art_{year}.json)
  What When Where Files/Artelier Upload/artelier_bm_upload_{year}*.csv

For each CSV row matched by bm_uid (then title), fills:
  - playa_latitude / playa_longitude from GIS gps_*
  - playa_address from GIS location_string when present
  - project_year / bm_year to the year inferred from the CSV filename
    (fixes mis-stamped years like a 2023 file labeled 2024)

Does not talk to Artelier. Run before admin upload.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.bm_ingest.sources import (  # noqa: E402
    load_gis_coordinates_by_key,
    lookup,
    resolve_gis_coordinate_path,
)

YEAR_RE = re.compile(r"(?:^|[_\-])((?:19|20)\d{2})(?:[_\-]|$|\.)")


def infer_year(path: Path) -> int | None:
    match = YEAR_RE.search(path.name)
    return int(match.group(1)) if match else None


def enrich_csv(csv_path: Path, year: int, gis_index: dict, *, dry_run: bool) -> dict:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    required = {"bm_uid", "project_title", "playa_latitude", "playa_longitude"}
    missing = required - set(fieldnames)
    if missing:
        raise ValueError(f"{csv_path.name}: missing columns {sorted(missing)}")

    matched = 0
    filled_coords = 0
    updated_address = 0
    fixed_year = 0

    for row in rows:
        uid = (row.get("bm_uid") or "").strip()
        title = (row.get("project_title") or "").strip()
        gis = lookup(gis_index, uid=uid or None, year=year, title=title or None)
        if not gis:
            # Still correct year stamps when filename year is authoritative.
            for col in ("project_year", "bm_year"):
                if col in row and (row.get(col) or "").strip() and (row.get(col) or "").strip() != str(year):
                    if not dry_run:
                        row[col] = str(year)
                    fixed_year += 1
            continue

        matched += 1
        lat = gis.get("gps_latitude")
        lng = gis.get("gps_longitude")
        if lat is not None and lng is not None and str(lat).strip() != "" and str(lng).strip() != "":
            if (row.get("playa_latitude") or "").strip() != str(lat) or (
                row.get("playa_longitude") or ""
            ).strip() != str(lng):
                filled_coords += 1
            if not dry_run:
                row["playa_latitude"] = str(lat)
                row["playa_longitude"] = str(lng)

        gis_address = (gis.get("location_string") or "").strip()
        if gis_address and (row.get("playa_address") or "").strip() != gis_address:
            updated_address += 1
            if not dry_run:
                row["playa_address"] = gis_address

        for col in ("project_year", "bm_year"):
            if col in row and (row.get(col) or "").strip() != str(year):
                fixed_year += 1
                if not dry_run:
                    row[col] = str(year)

        provenance = (row.get("source_provenance") or "").strip()
        if provenance and "gis_coordinates" not in provenance.split("|"):
            if not dry_run:
                row["source_provenance"] = f"{provenance}|gis_coordinates"
        elif not provenance and not dry_run:
            row["source_provenance"] = "gis_coordinates"

        # Soft-fix archive year query params when they contradict the file year.
        for col in ("proof_external_url", "hero_image_source_page"):
            url = (row.get(col) or "").strip()
            if not url:
                continue
            rewritten = re.sub(r"([?&]yyyy=)\d{4}", rf"\g<1>{year}", url)
            if rewritten != url and not dry_run:
                row[col] = rewritten

    if not dry_run:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    return {
        "file": str(csv_path),
        "year": year,
        "rows": len(rows),
        "matched": matched,
        "filled_coords": filled_coords,
        "updated_address": updated_address,
        "fixed_year_fields": fixed_year,
        "unmatched": len(rows) - matched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="burning-man-art-scraper project root",
    )
    parser.add_argument(
        "--upload-dir",
        type=Path,
        default=None,
        help="Artelier Upload directory (default: ../What When Where Files/Artelier Upload)",
    )
    parser.add_argument("--year", type=int, action="append", help="Limit to one or more years")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    project_root: Path = args.project_root
    upload_dir = args.upload_dir or (
        project_root.parent / "What When Where Files" / "Artelier Upload"
    )
    if not upload_dir.exists():
        print(f"Upload dir not found: {upload_dir}", file=sys.stderr)
        return 1

    years_filter = set(args.year or [])
    summaries = []
    for csv_path in sorted(upload_dir.glob("artelier_bm_upload_*.csv")):
        year = infer_year(csv_path)
        if year is None:
            print(f"skip (no year in name): {csv_path.name}")
            continue
        if years_filter and year not in years_filter:
            continue
        gis_path = resolve_gis_coordinate_path(project_root, year)
        if gis_path is None:
            print(f"skip {csv_path.name}: no GIS/art JSON for {year}")
            continue
        gis_index = load_gis_coordinates_by_key(gis_path)
        summary = enrich_csv(csv_path, year, gis_index, dry_run=args.dry_run)
        summary["gis"] = str(gis_path)
        summaries.append(summary)
        print(
            f"{'[dry-run] ' if args.dry_run else ''}"
            f"{csv_path.name}: matched {summary['matched']}/{summary['rows']}, "
            f"coords {summary['filled_coords']}, address {summary['updated_address']}, "
            f"year fixes {summary['fixed_year_fields']}, unmatched {summary['unmatched']}"
        )

    if not summaries:
        print("No CSVs enriched.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
