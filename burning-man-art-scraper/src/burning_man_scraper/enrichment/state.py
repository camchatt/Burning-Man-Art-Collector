from __future__ import annotations

from pathlib import Path
import sqlite3

from burning_man_scraper.enrichment.models import (
    ENRICHMENT_SCHEMA_VERSION,
    FINAL_ENRICHMENT_STATUSES,
    BatchRecord,
    EnrichmentRun,
    ProposedEnrichment,
)
from burning_man_scraper.state import ScraperState, utc_now


RETRYABLE_STATUSES = {"failed", "no_sources_found"}


class EnrichmentState:
    def __init__(self, state_store: ScraperState):
        self.state_store = state_store

    def initialize(self) -> None:
        self.state_store.initialize()
        with self.state_store.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS enrichment_runs (
                    enrichment_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    export_batch_id INTEGER,
                    source_batch_directory TEXT NOT NULL,
                    requested_count INTEGER NOT NULL,
                    records_selected INTEGER NOT NULL DEFAULT 0,
                    records_completed INTEGER NOT NULL DEFAULT 0,
                    records_failed INTEGER NOT NULL DEFAULT 0,
                    records_skipped INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    enrichment_schema_version TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS enrichment_records (
                    enrichment_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enrichment_run_id INTEGER NOT NULL,
                    project_record_id TEXT NOT NULL,
                    project_title TEXT NOT NULL,
                    contributor_name TEXT,
                    enrichment_status TEXT NOT NULL,
                    search_status TEXT,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    proposed_change_count INTEGER NOT NULL DEFAULT 0,
                    reviewed_at TEXT,
                    completed_at TEXT,
                    last_error TEXT,
                    FOREIGN KEY (enrichment_run_id) REFERENCES enrichment_runs(enrichment_run_id)
                );

                CREATE TABLE IF NOT EXISTS enrichment_changes (
                    enrichment_change_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enrichment_run_id INTEGER NOT NULL,
                    project_record_id TEXT NOT NULL,
                    project_title TEXT NOT NULL,
                    contributor_name TEXT,
                    artelier_field TEXT NOT NULL,
                    original_value TEXT,
                    proposed_value TEXT,
                    final_value TEXT,
                    evidence_classification TEXT NOT NULL,
                    confidence REAL,
                    source_url TEXT,
                    source_title TEXT,
                    source_excerpt TEXT,
                    review_status TEXT NOT NULL,
                    review_notes TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (enrichment_run_id) REFERENCES enrichment_runs(enrichment_run_id)
                );
                """
            )

    def latest_status_by_project(
        self,
        export_batch_id: int | None,
        source_batch_directory: Path,
    ) -> dict[str, str]:
        self.initialize()
        with self.state_store.connection() as connection:
            rows = connection.execute(
                """
                SELECT er.project_record_id, er.enrichment_status
                FROM enrichment_records er
                JOIN enrichment_runs run ON run.enrichment_run_id = er.enrichment_run_id
                WHERE COALESCE(run.export_batch_id, -1) = COALESCE(?, -1)
                  AND run.source_batch_directory = ?
                ORDER BY er.enrichment_record_id
                """,
                (export_batch_id, str(source_batch_directory)),
            ).fetchall()
        statuses: dict[str, str] = {}
        for row in rows:
            statuses[row["project_record_id"]] = row["enrichment_status"]
        return statuses

    def count_previously_enriched(
        self,
        export_batch_id: int | None,
        source_batch_directory: Path,
    ) -> int:
        statuses = self.latest_status_by_project(export_batch_id, source_batch_directory)
        return sum(1 for status in statuses.values() if status in FINAL_ENRICHMENT_STATUSES)

    def select_records(
        self,
        records: list[BatchRecord],
        export_batch_id: int | None,
        source_batch_directory: Path,
        requested_count: int,
        resume_action: str = "continue",
    ) -> list[BatchRecord]:
        statuses = self.latest_status_by_project(export_batch_id, source_batch_directory)
        selected: list[BatchRecord] = []
        for record in records:
            status = statuses.get(record.project_record_id)
            if should_select_status(status, resume_action):
                selected.append(record)
            if len(selected) >= requested_count:
                break
        return selected

    def create_run(
        self,
        export_batch_id: int | None,
        source_batch_directory: Path,
        requested_count: int,
        selected_records: list[BatchRecord],
        status: str = "planned",
        enrichment_schema_version: str = ENRICHMENT_SCHEMA_VERSION,
    ) -> EnrichmentRun:
        self.initialize()
        with self.state_store.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO enrichment_runs (
                    export_batch_id,
                    source_batch_directory,
                    requested_count,
                    records_selected,
                    records_completed,
                    records_failed,
                    records_skipped,
                    status,
                    started_at,
                    completed_at,
                    enrichment_schema_version
                )
                VALUES (?, ?, ?, ?, 0, 0, 0, ?, ?, NULL, ?)
                """,
                (
                    export_batch_id,
                    str(source_batch_directory),
                    requested_count,
                    len(selected_records),
                    status,
                    utc_now(),
                    enrichment_schema_version,
                ),
            )
            run_id = int(cursor.lastrowid)
            for record in selected_records:
                connection.execute(
                    """
                    INSERT INTO enrichment_records (
                        enrichment_run_id,
                        project_record_id,
                        project_title,
                        contributor_name,
                        enrichment_status,
                        search_status,
                        source_count,
                        proposed_change_count,
                        reviewed_at,
                        completed_at,
                        last_error
                    )
                    VALUES (?, ?, ?, ?, 'pending', NULL, 0, 0, NULL, NULL, NULL)
                    """,
                    (
                        run_id,
                        record.project_record_id,
                        record.project_title,
                        record.contributor_name,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM enrichment_runs WHERE enrichment_run_id = ?",
                (run_id,),
            ).fetchone()
        return enrichment_run_from_row(row)

    def records_for_run(self, enrichment_run_id: int) -> list[dict[str, object]]:
        self.initialize()
        with self.state_store.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM enrichment_records
                WHERE enrichment_run_id = ?
                ORDER BY enrichment_record_id
                """,
                (enrichment_run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_record_result(
        self,
        enrichment_run_id: int,
        project_record_id: str,
        enrichment_status: str,
        search_status: str | None = None,
        source_count: int = 0,
        proposed_change_count: int = 0,
        last_error: str | None = None,
    ) -> None:
        self.initialize()
        with self.state_store.connection() as connection:
            connection.execute(
                """
                UPDATE enrichment_records
                SET enrichment_status = ?,
                    search_status = ?,
                    source_count = ?,
                    proposed_change_count = ?,
                    completed_at = ?,
                    last_error = ?
                WHERE enrichment_run_id = ? AND project_record_id = ?
                """,
                (
                    enrichment_status,
                    search_status,
                    source_count,
                    proposed_change_count,
                    utc_now(),
                    last_error,
                    enrichment_run_id,
                    project_record_id,
                ),
            )

    def save_proposed_changes(
        self,
        enrichment_run_id: int,
        record: BatchRecord,
        changes: list[ProposedEnrichment],
        approval_mode: str = "manual_review_required",
        confidence_threshold: float = 0.85,
    ) -> None:
        self.initialize()
        with self.state_store.connection() as connection:
            for change in changes:
                review_status = review_status_for_change(change, approval_mode, confidence_threshold)
                final_value = change.proposed_value if review_status in {"approved", "edited"} else change.original_value
                existing = connection.execute(
                    """
                    SELECT 1
                    FROM enrichment_changes change
                    JOIN enrichment_runs run ON run.enrichment_run_id = change.enrichment_run_id
                    WHERE COALESCE(run.export_batch_id, -1) = (
                        SELECT COALESCE(export_batch_id, -1)
                        FROM enrichment_runs
                        WHERE enrichment_run_id = ?
                    )
                      AND run.source_batch_directory = (
                        SELECT source_batch_directory
                        FROM enrichment_runs
                        WHERE enrichment_run_id = ?
                    )
                      AND change.project_record_id = ?
                      AND change.artelier_field = ?
                      AND COALESCE(change.proposed_value, '') = COALESCE(?, '')
                      AND COALESCE(change.source_url, '') = COALESCE(?, '')
                    LIMIT 1
                    """,
                    (
                        enrichment_run_id,
                        enrichment_run_id,
                        record.project_record_id,
                        change.artelier_field,
                        change.proposed_value,
                        change.source_url,
                    ),
                ).fetchone()
                if existing:
                    continue
                connection.execute(
                    """
                    INSERT INTO enrichment_changes (
                        enrichment_run_id,
                        project_record_id,
                        project_title,
                        contributor_name,
                        artelier_field,
                        original_value,
                        proposed_value,
                        final_value,
                        evidence_classification,
                        confidence,
                        source_url,
                        source_title,
                        source_excerpt,
                        review_status,
                        review_notes,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        enrichment_run_id,
                        record.project_record_id,
                        record.project_title,
                        record.contributor_name,
                        change.artelier_field,
                        change.original_value,
                        change.proposed_value,
                        final_value,
                        change.evidence_classification,
                        change.confidence,
                        change.source_url,
                        change.source_title,
                        change.source_excerpt,
                        review_status,
                        "",
                        utc_now(),
                    ),
                )

    def changes_for_batch(
        self,
        export_batch_id: int | None,
        source_batch_directory: Path,
    ) -> list[dict[str, object]]:
        self.initialize()
        with self.state_store.connection() as connection:
            rows = connection.execute(
                """
                SELECT change.*, run.enrichment_schema_version
                FROM enrichment_changes change
                JOIN enrichment_runs run ON run.enrichment_run_id = change.enrichment_run_id
                WHERE COALESCE(run.export_batch_id, -1) = COALESCE(?, -1)
                  AND run.source_batch_directory = ?
                ORDER BY change.enrichment_change_id
                """,
                (export_batch_id, str(source_batch_directory)),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_run_counts(
        self,
        enrichment_run_id: int,
        completed: int,
        failed: int,
        skipped: int,
        status: str,
    ) -> None:
        self.initialize()
        with self.state_store.connection() as connection:
            connection.execute(
                """
                UPDATE enrichment_runs
                SET records_completed = ?,
                    records_failed = ?,
                    records_skipped = ?,
                    completed_at = ?,
                    status = ?
                WHERE enrichment_run_id = ?
                """,
                (completed, failed, skipped, utc_now(), status, enrichment_run_id),
            )

    def run_row(self, enrichment_run_id: int) -> dict[str, object]:
        self.initialize()
        with self.state_store.connection() as connection:
            row = connection.execute(
                "SELECT * FROM enrichment_runs WHERE enrichment_run_id = ?",
                (enrichment_run_id,),
            ).fetchone()
        return dict(row)

    def mark_project_status(
        self,
        export_batch_id: int | None,
        source_batch_directory: Path,
        project_record_id: str,
        project_title: str,
        contributor_name: str | None,
        enrichment_status: str,
    ) -> None:
        run = self.create_run(
            export_batch_id=export_batch_id,
            source_batch_directory=source_batch_directory,
            requested_count=1,
            selected_records=[
                BatchRecord(
                    batch_index=1,
                    project_record_id=project_record_id,
                    project_title=project_title,
                    contributor_name=contributor_name,
                )
            ],
            status="test_seed",
        )
        with self.state_store.connection() as connection:
            connection.execute(
                """
                UPDATE enrichment_records
                SET enrichment_status = ?
                WHERE enrichment_run_id = ?
                """,
                (enrichment_status, run.enrichment_run_id),
            )


def should_select_status(status: str | None, resume_action: str) -> bool:
    if resume_action == "retry_failed":
        return status in RETRYABLE_STATUSES
    if resume_action == "reprocess_changed_rules":
        return status not in {"approved"}
    if resume_action == "re_export_approved":
        return status == "approved"
    return status is None


PROTECTED_FIELDS = {
    "contributor_name",
    "contributor_email",
    "client_name",
    "public_credit_language",
    "role_title",
    "permission_status",
    "contributor_slug",
}


def review_status_for_change(
    change: ProposedEnrichment,
    approval_mode: str,
    confidence_threshold: float,
) -> str:
    if change.artelier_field in PROTECTED_FIELDS:
        return "unresolved"
    if approval_mode != "auto_apply_high_confidence_direct_statements":
        return "unresolved"
    if (
        change.evidence_classification == "directly_stated"
        and change.source_type in {"first_party", "institutional"}
        and change.confidence >= confidence_threshold
    ):
        return "approved"
    return "unresolved"


def enrichment_run_from_row(row: sqlite3.Row) -> EnrichmentRun:
    return EnrichmentRun(
        enrichment_run_id=row["enrichment_run_id"],
        export_batch_id=row["export_batch_id"],
        source_batch_directory=Path(row["source_batch_directory"]),
        requested_count=row["requested_count"],
        records_selected=row["records_selected"],
        records_completed=row["records_completed"],
        records_failed=row["records_failed"],
        records_skipped=row["records_skipped"],
        status=row["status"],
        enrichment_schema_version=row["enrichment_schema_version"],
    )
