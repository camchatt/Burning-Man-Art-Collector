from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ENRICHMENT_SCHEMA_VERSION = "web-enrichment-v1"

ENRICHMENT_OUTPUT_FILENAMES = (
    "enriched_artelier_import.csv",
    "enrichment_review.csv",
    "enrichment_manifest.json",
)

FINAL_ENRICHMENT_STATUSES = {"approved", "rejected", "skipped"}


@dataclass(frozen=True)
class ScrapeBatch:
    export_batch_id: int | None
    year: str
    batch_name: str
    batch_directory: Path
    record_count: int

    @property
    def display_label(self) -> str:
        return f"{self.year} / {self.batch_name} / {self.record_count} records"


@dataclass(frozen=True)
class BatchRecord:
    batch_index: int
    project_record_id: str
    project_title: str
    contributor_name: str | None
    source_position: int | None = None
    year: str | None = None
    source_url: str | None = None
    artist_collective: str | None = None
    materials: str | None = None
    project_website: str | None = None
    artist_website: str | None = None
    original_values: dict[str, object] | None = None
    artelier_row: dict[str, object] | None = None


@dataclass(frozen=True)
class EnrichmentSelection:
    records: list[BatchRecord]
    requested_count: int
    previously_enriched: int
    remaining: int
    proposed_start_index: int | None
    proposed_end_index: int | None
    resume_action: str


@dataclass(frozen=True)
class EnrichmentRun:
    enrichment_run_id: int
    export_batch_id: int | None
    source_batch_directory: Path
    requested_count: int
    records_selected: int
    records_completed: int
    records_failed: int
    records_skipped: int
    status: str
    enrichment_schema_version: str


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    provider: str = ""
    published_date: str | None = None
    engine_metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class CandidateSource:
    title: str
    url: str
    source_type: str
    relevance_score: int
    matching_identifiers: list[str]
    excerpt: str


@dataclass(frozen=True)
class ProposedEnrichment:
    artelier_field: str
    original_value: str
    proposed_value: str
    source_url: str
    source_title: str
    source_type: str
    source_excerpt: str
    confidence: float
    evidence_classification: str
    review_required: bool


@dataclass(frozen=True)
class EnrichmentPreview:
    preview_id: str
    batch_record: BatchRecord
    sources: list[CandidateSource]
    proposed_changes: list[ProposedEnrichment]
    unresolved_fields: dict[str, str]
    artelier_row: dict[str, str]
    headers: list[str]
    search_provider: str = ""
    search_report: dict[str, object] | None = None
