from __future__ import annotations

import csv
import re
from pathlib import Path

from burning_man_scraper.record_parser import normalize_title
from burning_man_scraper.verification.models import WwwReferenceRecord


WWW_FILENAME_PATTERN = re.compile(r"PlayaEvents-(?P<year>\d{4}).*_ART\.csv$", re.IGNORECASE)
LINK_YEAR_PATTERN = re.compile(r"(?:^|/)(?P<year>20\d{2})-art-installations", re.IGNORECASE)


class ArtCsvYearMismatchError(ValueError):
    """Raised when an ART CSV's filename or Link years do not match the ingest year."""


def infer_year_from_filename(name: str | None) -> int | None:
    if not name:
        return None
    match = WWW_FILENAME_PATTERN.match(Path(name).name)
    return int(match.group("year")) if match else None


def infer_year_from_art_csv_links(path: Path, *, sample_limit: int = 200) -> int | None:
    """Infer listing year from a majority of `Link` values such as `/2025-art-installations/`."""
    if not path.exists():
        return None
    counts: dict[int, int] = {}
    sampled = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header_row = next(reader, None)
        if not header_row:
            return None
        link_index = _optional_column_index(header_row, "Link")
        if link_index is None:
            return None
        for row in reader:
            if sampled >= sample_limit:
                break
            if not row or len(row) <= link_index:
                continue
            link = _cell(row, link_index)
            if not link:
                continue
            match = LINK_YEAR_PATTERN.search(link)
            if not match:
                continue
            year = int(match.group("year"))
            counts[year] = counts.get(year, 0) + 1
            sampled += 1
    if not counts:
        return None
    total = sum(counts.values())
    winner, winner_count = max(counts.items(), key=lambda item: item[1])
    if winner_count * 2 > total:
        return winner
    return None


def resolve_art_csv_year(
    path: Path,
    *,
    original_filename: str | None = None,
) -> int:
    """Determine listing year from filename and/or Link majority. Raises if unknown or conflicting."""
    filename_year = infer_year_from_filename(original_filename or path.name)
    link_year = infer_year_from_art_csv_links(path)
    if filename_year is not None and link_year is not None and filename_year != link_year:
        raise ArtCsvYearMismatchError(
            f"ART CSV filename year {filename_year} conflicts with Link year {link_year}"
        )
    year = link_year if link_year is not None else filename_year
    if year is None:
        raise ValueError(
            "Could not determine year from ART CSV. Use a PlayaEvents-YYYY_ART.csv name "
            "or rows whose Link values include /YYYY-art-installations/."
        )
    return year


def assert_art_csv_matches_year(
    path: Path,
    expected_year: int,
    *,
    original_filename: str | None = None,
) -> None:
    """Reject ART CSV files whose filename or link majority year differs from expected_year."""
    resolved = resolve_art_csv_year(path, original_filename=original_filename)
    if resolved != expected_year:
        raise ArtCsvYearMismatchError(
            f"ART CSV looks like year {resolved} but ingest year is {expected_year}"
        )


def assert_playaevents_art_csv(path: Path) -> None:
    """Reject non-PlayaEvents templates (e.g. Artelier exports) before any pipeline work."""
    if not path.exists():
        raise FileNotFoundError(f"ART CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header_row = next(reader, None)
    if not header_row:
        raise ValueError("ART CSV is empty — upload a PlayaEvents-YYYY_ART.csv template.")
    lowered = {cell.strip().lower() for cell in header_row if cell and cell.strip()}
    if "project_title" in lowered or "bm_uid" in lowered:
        raise ValueError(
            "This looks like an Artelier / Aggregator export, not a PlayaEvents ART template. "
            "Upload a PlayaEvents-YYYY_ART.csv (columns Title, Description, Link, UID)."
        )
    missing = [name for name in ("Title", "Description") if name.lower() not in lowered]
    if missing:
        raise ValueError(
            "Not a PlayaEvents ART template — missing column(s) "
            + ", ".join(repr(name) for name in missing)
            + ". Upload PlayaEvents-YYYY_ART.csv (Title, Description, Link, UID)."
        )


def load_www_records(www_dir: Path, year: int | None = None) -> list[WwwReferenceRecord]:
    if not www_dir.exists():
        raise FileNotFoundError(f"WWW reference directory not found: {www_dir}")

    records: list[WwwReferenceRecord] = []
    for path in sorted(www_dir.glob("PlayaEvents-*_ART.csv")):
        match = WWW_FILENAME_PATTERN.match(path.name)
        if not match:
            continue
        file_year = int(match.group("year"))
        if year is not None and file_year != year:
            continue
        records.extend(_load_www_file(path, file_year))
    return _dedupe_www_records(records)


def load_www_art_csv(path: Path, year: int) -> list[WwwReferenceRecord]:
    """Load a single PlayaEvents ART CSV for an explicit year."""
    if not path.exists():
        raise FileNotFoundError(f"WWW ART CSV not found: {path}")
    return _dedupe_www_records(_load_www_file(path, year))



def _dedupe_www_records(records: list[WwwReferenceRecord]) -> list[WwwReferenceRecord]:
    seen: set[str] = set()
    deduped: list[WwwReferenceRecord] = []
    for record in records:
        key = record.uid or record.normalized_title
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _load_www_file(path: Path, file_year: int) -> list[WwwReferenceRecord]:
    records: list[WwwReferenceRecord] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header_row = next(reader, None)
        if not header_row:
            return records
        title_index = _column_index(header_row, "Title")
        description_index = _column_index(header_row, "Description")
        extra_index = _optional_column_index(header_row, "Extra")
        link_index = _optional_column_index(header_row, "Link")
        uid_index = _optional_column_index(header_row, "UID")
        camp_index = _optional_column_index(header_row, "Camp")
        where_index = _optional_column_index(header_row, "Where")
        type_index = _optional_column_index(header_row, "Type")

        for row in reader:
            if not row or len(row) <= title_index:
                continue
            title = _cell(row, title_index)
            if not title or title.lower() in {"title", "camp", "xxxx"}:
                continue
            description = _cell(row, description_index)
            extra = _clean_url(_cell(row, extra_index) if extra_index is not None else "")
            link = _clean_url(_cell(row, link_index) if link_index is not None else "")
            uid = (_cell(row, uid_index) if uid_index is not None else "") or None
            camp = _clean_text_field(_cell(row, camp_index) if camp_index is not None else "")
            where = _clean_text_field(_cell(row, where_index) if where_index is not None else "")
            install_type = _clean_text_field(_cell(row, type_index) if type_index is not None else "")
            records.append(
                WwwReferenceRecord(
                    year=file_year,
                    title=title,
                    normalized_title=normalize_title(title),
                    description=description,
                    artist_url=extra,
                    legacy_link=link,
                    uid=uid,
                    theme_camp=camp,
                    playa_address=where,
                    installation_type=install_type,
                )
            )
    return records


def index_www_by_uid(records: list[WwwReferenceRecord]) -> dict[str, WwwReferenceRecord]:
    index: dict[str, WwwReferenceRecord] = {}
    for record in records:
        if record.uid:
            index[record.uid] = record
    return index


def index_www_by_title(records: list[WwwReferenceRecord]) -> dict[str, WwwReferenceRecord]:
    index: dict[str, WwwReferenceRecord] = {}
    for record in records:
        index.setdefault(record.normalized_title, record)
    return index


def _column_index(header_row: list[str], name: str) -> int:
    lowered = [cell.strip().lower() for cell in header_row]
    try:
        return lowered.index(name.lower())
    except ValueError as exc:
        raise ValueError(f"Expected column {name!r} in {header_row}") from exc


def _optional_column_index(header_row: list[str], name: str) -> int | None:
    lowered = [cell.strip().lower() for cell in header_row]
    try:
        return lowered.index(name.lower())
    except ValueError:
        return None


def _cell(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return row[index].strip()


def _clean_url(value: str) -> str | None:
    if not value or value == "-":
        return None
    return value


def _clean_text_field(value: str) -> str | None:
    text = (value or "").strip()
    if not text or text == "-":
        return None
    return text
