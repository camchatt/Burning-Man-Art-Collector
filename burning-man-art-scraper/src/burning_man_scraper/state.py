from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Source:
    source_id: int
    entered_url: str
    normalized_url: str
    source_hash: str
    detected_year: str | None
    detected_collection: str | None
    first_seen_at: str
    last_seen_at: str


@dataclass(frozen=True)
class Checkpoint:
    source_id: int
    last_discovered_position: int
    last_completed_position: int
    last_exported_position: int
    updated_at: str


@dataclass(frozen=True)
class SourceLookup:
    source: Source
    created: bool
    checkpoint: Checkpoint | None

    @property
    def has_previous_state(self) -> bool:
        return not self.created


@dataclass(frozen=True)
class ApprovalContext:
    preview_run_id: str
    source_id: int
    normalized_source_url: str
    proposed_batch_number: int
    requested_count: int
    preview_record_id: str
    schema_version: str
    parser_version: str
    configuration_hash: str
    source_manifest_hash: str


@dataclass(frozen=True)
class PreviewApproval:
    preview_run_id: str
    source_id: int
    normalized_source_url: str
    proposed_batch_number: int
    requested_count: int
    preview_record_id: str
    schema_version: str
    parser_version: str
    configuration_hash: str
    source_manifest_hash: str
    approved_at: str
    approval_status: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def detect_year(normalized_url: str) -> str | None:
    parsed = urlsplit(normalized_url)
    query = parse_qs(parsed.query)
    if "yyyy" in query and query["yyyy"]:
        return query["yyyy"][0]

    match = re.search(r"/(19\d{2}|20\d{2})(?:/|$)", parsed.path)
    if match:
        return match.group(1)
    return None


def detect_collection(normalized_url: str) -> str | None:
    parsed = urlsplit(normalized_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "art-history":
        return parts[1]
    return parts[0] if parts else None


class ScraperState:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entered_url TEXT NOT NULL,
                    normalized_url TEXT NOT NULL UNIQUE,
                    source_hash TEXT NOT NULL UNIQUE,
                    detected_year TEXT,
                    detected_collection TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_records (
                    source_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    source_position INTEGER NOT NULL,
                    installation_url TEXT,
                    canonical_installation_url TEXT,
                    record_id TEXT,
                    record_status TEXT NOT NULL DEFAULT 'discovered',
                    first_discovered_at TEXT NOT NULL,
                    last_processed_at TEXT,
                    content_hash TEXT,
                    exported_at TEXT,
                    export_batch_id INTEGER,
                    record_json TEXT,
                    artelier_row_json TEXT,
                    UNIQUE (source_id, source_position),
                    FOREIGN KEY (source_id) REFERENCES sources(source_id),
                    FOREIGN KEY (export_batch_id) REFERENCES export_batches(export_batch_id)
                );

                CREATE TABLE IF NOT EXISTS checkpoints (
                    source_id INTEGER PRIMARY KEY,
                    last_discovered_position INTEGER NOT NULL DEFAULT 0,
                    last_completed_position INTEGER NOT NULL DEFAULT 0,
                    last_exported_position INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (source_id) REFERENCES sources(source_id)
                );

                CREATE TABLE IF NOT EXISTS export_batches (
                    export_batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    run_id TEXT,
                    preview_run_id TEXT,
                    requested_count INTEGER NOT NULL,
                    start_position INTEGER,
                    end_position INTEGER,
                    records_attempted INTEGER NOT NULL DEFAULT 0,
                    records_succeeded INTEGER NOT NULL DEFAULT 0,
                    records_failed INTEGER NOT NULL DEFAULT 0,
                    records_skipped INTEGER NOT NULL DEFAULT 0,
                    export_file TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    configuration_hash TEXT NOT NULL,
                    FOREIGN KEY (source_id) REFERENCES sources(source_id)
                );

                CREATE TABLE IF NOT EXISTS preview_approvals (
                    preview_run_id TEXT PRIMARY KEY,
                    source_id INTEGER NOT NULL,
                    normalized_source_url TEXT NOT NULL,
                    proposed_batch_number INTEGER NOT NULL,
                    requested_count INTEGER NOT NULL,
                    preview_record_id TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    configuration_hash TEXT NOT NULL,
                    source_manifest_hash TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    approval_status TEXT NOT NULL,
                    FOREIGN KEY (source_id) REFERENCES sources(source_id)
                );
                """
            )
            existing_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(source_records)").fetchall()
            }
            if "record_json" not in existing_columns:
                connection.execute("ALTER TABLE source_records ADD COLUMN record_json TEXT")
            if "artelier_row_json" not in existing_columns:
                connection.execute("ALTER TABLE source_records ADD COLUMN artelier_row_json TEXT")

    def get_or_create_source(self, entered_url: str, normalized_url: str) -> SourceLookup:
        self.initialize()
        source_hash = hash_value(normalized_url)
        now = utc_now()

        with self.connection() as connection:
            existing_row = connection.execute(
                "SELECT * FROM sources WHERE normalized_url = ?",
                (normalized_url,),
            ).fetchone()

            if existing_row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO sources (
                        entered_url,
                        normalized_url,
                        source_hash,
                        detected_year,
                        detected_collection,
                        first_seen_at,
                        last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entered_url,
                        normalized_url,
                        source_hash,
                        detect_year(normalized_url),
                        detect_collection(normalized_url),
                        now,
                        now,
                    ),
                )
                source_id = cursor.lastrowid
                source_row = connection.execute(
                    "SELECT * FROM sources WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
                return SourceLookup(
                    source=_source_from_row(source_row),
                    created=True,
                    checkpoint=None,
                )

            connection.execute(
                """
                UPDATE sources
                SET entered_url = ?, last_seen_at = ?
                WHERE source_id = ?
                """,
                (entered_url, now, existing_row["source_id"]),
            )
            source_row = connection.execute(
                "SELECT * FROM sources WHERE source_id = ?",
                (existing_row["source_id"],),
            ).fetchone()
            checkpoint = self.get_checkpoint(existing_row["source_id"], connection)
            return SourceLookup(
                source=_source_from_row(source_row),
                created=False,
                checkpoint=checkpoint,
            )

    def next_proposed_batch_number(self, source_id: int) -> int:
        self.initialize()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS batch_count FROM export_batches WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            return int(row["batch_count"]) + 1

    def save_preview_approval(
        self,
        context: ApprovalContext,
        approval_status: str,
    ) -> PreviewApproval:
        self.initialize()
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO preview_approvals (
                    preview_run_id,
                    source_id,
                    normalized_source_url,
                    proposed_batch_number,
                    requested_count,
                    preview_record_id,
                    schema_version,
                    parser_version,
                    configuration_hash,
                    source_manifest_hash,
                    approved_at,
                    approval_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(preview_run_id) DO UPDATE SET
                    source_id = excluded.source_id,
                    normalized_source_url = excluded.normalized_source_url,
                    proposed_batch_number = excluded.proposed_batch_number,
                    requested_count = excluded.requested_count,
                    preview_record_id = excluded.preview_record_id,
                    schema_version = excluded.schema_version,
                    parser_version = excluded.parser_version,
                    configuration_hash = excluded.configuration_hash,
                    source_manifest_hash = excluded.source_manifest_hash,
                    approved_at = excluded.approved_at,
                    approval_status = excluded.approval_status
                """,
                (
                    context.preview_run_id,
                    context.source_id,
                    context.normalized_source_url,
                    context.proposed_batch_number,
                    context.requested_count,
                    context.preview_record_id,
                    context.schema_version,
                    context.parser_version,
                    context.configuration_hash,
                    context.source_manifest_hash,
                    now,
                    approval_status,
                ),
            )
            row = connection.execute(
                "SELECT * FROM preview_approvals WHERE preview_run_id = ?",
                (context.preview_run_id,),
            ).fetchone()
            return _preview_approval_from_row(row)

    def preview_approval_matches(self, context: ApprovalContext) -> bool:
        self.initialize()
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM preview_approvals
                WHERE preview_run_id = ? AND approval_status = 'approved'
                """,
                (context.preview_run_id,),
            ).fetchone()
        if row is None:
            return False
        approval = _preview_approval_from_row(row)
        return approval == PreviewApproval(
            preview_run_id=context.preview_run_id,
            source_id=context.source_id,
            normalized_source_url=context.normalized_source_url,
            proposed_batch_number=context.proposed_batch_number,
            requested_count=context.requested_count,
            preview_record_id=context.preview_record_id,
            schema_version=context.schema_version,
            parser_version=context.parser_version,
            configuration_hash=context.configuration_hash,
            source_manifest_hash=context.source_manifest_hash,
            approved_at=approval.approved_at,
            approval_status="approved",
        )

    def create_pending_export_batch(self, context: ApprovalContext) -> int:
        if not self.preview_approval_matches(context):
            raise ValueError("Cannot create pending export batch without matching preview approval.")
        self.initialize()
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO export_batches (
                    source_id,
                    run_id,
                    preview_run_id,
                    requested_count,
                    start_position,
                    end_position,
                    records_attempted,
                    records_succeeded,
                    records_failed,
                    records_skipped,
                    export_file,
                    started_at,
                    completed_at,
                    status,
                    schema_version,
                    parser_version,
                    configuration_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    context.source_id,
                    f"pending-batch-{context.proposed_batch_number}",
                    context.preview_run_id,
                    context.requested_count,
                    1,
                    context.requested_count,
                    "",
                    utc_now(),
                    "pending_approval_batch",
                    context.schema_version,
                    context.parser_version,
                    context.configuration_hash,
                ),
            )
            return int(cursor.lastrowid)

    def get_checkpoint(
        self,
        source_id: int,
        connection: sqlite3.Connection | None = None,
    ) -> Checkpoint | None:
        if connection is None:
            with self.connection() as owned_connection:
                row = owned_connection.execute(
                    "SELECT * FROM checkpoints WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE source_id = ?",
                (source_id,),
            ).fetchone()

        return _checkpoint_from_row(row) if row else None

    def save_checkpoint(
        self,
        source_id: int,
        last_discovered_position: int = 0,
        last_completed_position: int = 0,
        last_exported_position: int = 0,
    ) -> Checkpoint:
        self.initialize()
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO checkpoints (
                    source_id,
                    last_discovered_position,
                    last_completed_position,
                    last_exported_position,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_discovered_position = excluded.last_discovered_position,
                    last_completed_position = excluded.last_completed_position,
                    last_exported_position = excluded.last_exported_position,
                    updated_at = excluded.updated_at
                """,
                (
                    source_id,
                    last_discovered_position,
                    last_completed_position,
                    last_exported_position,
                    now,
                ),
            )
            checkpoint = self.get_checkpoint(source_id, connection)
            if checkpoint is None:
                raise RuntimeError("Failed to save checkpoint.")
            return checkpoint

    def source_record_status(self, source_id: int, source_position: int) -> str | None:
        self.initialize()
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT record_status
                FROM source_records
                WHERE source_id = ? AND source_position = ?
                """,
                (source_id, source_position),
            ).fetchone()
            return row["record_status"] if row else None

    def mark_source_record(
        self,
        source_id: int,
        source_position: int,
        installation_url: str,
        canonical_installation_url: str | None,
        record_id: str | None,
        record_status: str,
        content_hash: str | None = None,
    ) -> None:
        self.initialize()
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO source_records (
                    source_id,
                    source_position,
                    installation_url,
                    canonical_installation_url,
                    record_id,
                    record_status,
                    first_discovered_at,
                    last_processed_at,
                    content_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, source_position) DO UPDATE SET
                    installation_url = excluded.installation_url,
                    canonical_installation_url = excluded.canonical_installation_url,
                    record_id = excluded.record_id,
                    record_status = excluded.record_status,
                    last_processed_at = excluded.last_processed_at,
                    content_hash = excluded.content_hash
                """,
                (
                    source_id,
                    source_position,
                    installation_url,
                    canonical_installation_url,
                    record_id,
                    record_status,
                    now,
                    now,
                    content_hash,
                ),
            )

    def source_records_by_canonical(self, source_id: int) -> dict[str, dict[str, object]]:
        self.initialize()
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM source_records
                WHERE source_id = ? AND canonical_installation_url IS NOT NULL
                ORDER BY source_position
                """,
                (source_id,),
            ).fetchall()
        records: dict[str, dict[str, object]] = {}
        for row in rows:
            canonical_url = row["canonical_installation_url"]
            if canonical_url and canonical_url not in records:
                records[canonical_url] = dict(row)
        return records

    def completed_record_exists(self, source_id: int, canonical_installation_url: str) -> bool:
        self.initialize()
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM source_records
                WHERE source_id = ?
                  AND canonical_installation_url = ?
                  AND record_status = 'completed'
                LIMIT 1
                """,
                (source_id, canonical_installation_url),
            ).fetchone()
        return row is not None

    def mark_source_record_by_canonical(
        self,
        source_id: int,
        source_position: int,
        installation_url: str,
        canonical_installation_url: str | None,
        record_id: str | None,
        record_status: str,
        content_hash: str | None = None,
        export_batch_id: int | None = None,
        record_json: str | None = None,
        artelier_row_json: str | None = None,
    ) -> None:
        self.initialize()
        now = utc_now()
        with self.connection() as connection:
            existing_by_canonical = None
            if canonical_installation_url:
                existing_by_canonical = connection.execute(
                    """
                    SELECT source_record_id
                    FROM source_records
                    WHERE source_id = ? AND canonical_installation_url = ?
                    """,
                    (source_id, canonical_installation_url),
                ).fetchone()
            if existing_by_canonical:
                existing_by_position = connection.execute(
                    """
                    SELECT source_record_id
                    FROM source_records
                    WHERE source_id = ? AND source_position = ?
                    """,
                    (source_id, source_position),
                ).fetchone()
                if (
                    existing_by_position
                    and existing_by_position["source_record_id"] != existing_by_canonical["source_record_id"]
                ):
                    free_source_position(
                        connection=connection,
                        source_id=source_id,
                        source_record_id=existing_by_position["source_record_id"],
                    )
                connection.execute(
                    """
                    UPDATE source_records
                    SET source_position = ?,
                        installation_url = ?,
                        canonical_installation_url = ?,
                        record_id = ?,
                        record_status = ?,
                        last_processed_at = ?,
                        content_hash = ?,
                        export_batch_id = ?,
                        record_json = COALESCE(?, record_json),
                        artelier_row_json = COALESCE(?, artelier_row_json)
                    WHERE source_record_id = ?
                    """,
                    (
                        source_position,
                        installation_url,
                        canonical_installation_url,
                        record_id,
                        record_status,
                        now,
                        content_hash,
                        export_batch_id,
                        record_json,
                        artelier_row_json,
                        existing_by_canonical["source_record_id"],
                    ),
                )
                return
            existing_by_position = connection.execute(
                """
                SELECT source_record_id, canonical_installation_url
                FROM source_records
                WHERE source_id = ? AND source_position = ?
                """,
                (source_id, source_position),
            ).fetchone()
            if (
                existing_by_position
                and existing_by_position["canonical_installation_url"] != canonical_installation_url
            ):
                free_source_position(
                    connection=connection,
                    source_id=source_id,
                    source_record_id=existing_by_position["source_record_id"],
                )
            connection.execute(
                """
                INSERT INTO source_records (
                    source_id,
                    source_position,
                    installation_url,
                    canonical_installation_url,
                    record_id,
                    record_status,
                    first_discovered_at,
                    last_processed_at,
                    content_hash,
                    export_batch_id,
                    record_json,
                    artelier_row_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, source_position) DO UPDATE SET
                    installation_url = excluded.installation_url,
                    canonical_installation_url = excluded.canonical_installation_url,
                    record_id = excluded.record_id,
                    record_status = excluded.record_status,
                    last_processed_at = excluded.last_processed_at,
                    content_hash = excluded.content_hash,
                    export_batch_id = excluded.export_batch_id,
                    record_json = COALESCE(excluded.record_json, source_records.record_json),
                    artelier_row_json = COALESCE(excluded.artelier_row_json, source_records.artelier_row_json)
                """,
                (
                    source_id,
                    source_position,
                    installation_url,
                    canonical_installation_url,
                    record_id,
                    record_status,
                    now,
                    now,
                    content_hash,
                    export_batch_id,
                    record_json,
                    artelier_row_json,
                ),
            )

    def records_for_export_batch(self, export_batch_id: int) -> list[dict[str, object]]:
        self.initialize()
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM source_records
                WHERE export_batch_id = ?
                ORDER BY source_position, source_record_id
                """,
                (export_batch_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def completed_records_for_source(self, source_id: int) -> list[dict[str, object]]:
        self.initialize()
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM source_records
                WHERE source_id = ? AND record_status = 'completed'
                ORDER BY
                    CASE WHEN source_position > 0 THEN 0 ELSE 1 END,
                    ABS(source_position),
                    source_record_id
                """,
                (source_id,),
            ).fetchall()
        seen: set[str] = set()
        records: list[dict[str, object]] = []
        for row in rows:
            record = dict(row)
            key = str(record.get("canonical_installation_url") or record.get("record_id"))
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
        return records

    def export_batch_row(self, export_batch_id: int) -> dict[str, object] | None:
        self.initialize()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM export_batches WHERE export_batch_id = ?",
                (export_batch_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_export_batch_file(self, export_batch_id: int, export_file: str) -> None:
        self.initialize()
        with self.connection() as connection:
            connection.execute(
                "UPDATE export_batches SET export_file = ? WHERE export_batch_id = ?",
                (export_file, export_batch_id),
            )

    def next_unprocessed_position(self, source_id: int, candidate_urls: list[str]) -> int | None:
        from urllib.parse import urldefrag

        from burning_man_scraper.url_utils import normalize_url

        records = self.source_records_by_canonical(source_id)
        for index, candidate_url in enumerate(candidate_urls, start=1):
            base_url, fragment = urldefrag(candidate_url)
            canonical_url = normalize_url(base_url)
            if fragment:
                canonical_url = f"{canonical_url}#{fragment}"
            record = records.get(canonical_url)
            if not record or record["record_status"] not in {"completed", "skipped", "excluded"}:
                return index
        return None

    def update_export_batch_counts(
        self,
        export_batch_id: int,
        attempted: int,
        succeeded: int,
        failed: int,
        skipped: int,
        status: str,
    ) -> None:
        self.initialize()
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE export_batches
                SET records_attempted = ?,
                    records_succeeded = ?,
                    records_failed = ?,
                    records_skipped = ?,
                    completed_at = ?,
                    status = ?
                WHERE export_batch_id = ?
                """,
                (attempted, succeeded, failed, skipped, utc_now(), status, export_batch_id),
            )


def _source_from_row(row: sqlite3.Row) -> Source:
    return Source(
        source_id=row["source_id"],
        entered_url=row["entered_url"],
        normalized_url=row["normalized_url"],
        source_hash=row["source_hash"],
        detected_year=row["detected_year"],
        detected_collection=row["detected_collection"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
    )


def free_source_position(
    connection: sqlite3.Connection,
    source_id: int,
    source_record_id: int,
) -> None:
    temporary_position = -abs(source_record_id)
    while connection.execute(
        """
        SELECT 1
        FROM source_records
        WHERE source_id = ? AND source_position = ? AND source_record_id != ?
        """,
        (source_id, temporary_position, source_record_id),
    ).fetchone():
        temporary_position -= 1
    connection.execute(
        """
        UPDATE source_records
        SET source_position = ?
        WHERE source_record_id = ?
        """,
        (temporary_position, source_record_id),
    )


def _checkpoint_from_row(row: sqlite3.Row) -> Checkpoint:
    return Checkpoint(
        source_id=row["source_id"],
        last_discovered_position=row["last_discovered_position"],
        last_completed_position=row["last_completed_position"],
        last_exported_position=row["last_exported_position"],
        updated_at=row["updated_at"],
    )


def _preview_approval_from_row(row: sqlite3.Row) -> PreviewApproval:
    return PreviewApproval(
        preview_run_id=row["preview_run_id"],
        source_id=row["source_id"],
        normalized_source_url=row["normalized_source_url"],
        proposed_batch_number=row["proposed_batch_number"],
        requested_count=row["requested_count"],
        preview_record_id=row["preview_record_id"],
        schema_version=row["schema_version"],
        parser_version=row["parser_version"],
        configuration_hash=row["configuration_hash"],
        source_manifest_hash=row["source_manifest_hash"],
        approved_at=row["approved_at"],
        approval_status=row["approval_status"],
    )
