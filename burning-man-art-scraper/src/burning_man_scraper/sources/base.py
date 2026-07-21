"""Shared contracts for Artelier Aggregator source adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

ValueStatus = Literal["sourced", "inferred", "missing", "conflicting", "corrected"]


@dataclass
class FieldValue:
    value: str = ""
    status: ValueStatus = "missing"
    confidence: str = "none"
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedRecord:
    """Internal review record shared by every source adapter."""

    record_id: str
    source_id: str
    source_record_id: str
    source_record_url: str
    project_title: FieldValue
    contributor_name: FieldValue
    project_year: FieldValue
    project_location: FieldValue
    project_type: FieldValue
    collection: FieldValue
    hero_image_url: FieldValue
    proof_external_url: FieldValue
    project_summary: FieldValue
    project_tags: FieldValue
    project_materials: FieldValue
    project_fabrication_methods: FieldValue
    project_context_tags: FieldValue
    collaboration_status: FieldValue
    collaborators: FieldValue
    client_name: FieldValue
    approval_status: str = "draft"
    verification_status: str = "documented"
    permission_status: str = "pending_permission"
    review_flags: list[str] = field(default_factory=list)
    export_blockers: list[str] = field(default_factory=list)
    relationships: dict[str, Any] = field(default_factory=dict)
    raw_evidence: dict[str, Any] = field(default_factory=dict)
    artelier_row: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


@dataclass(frozen=True)
class SourceDescriptor:
    id: str
    label: str
    description: str
    input_kind: Literal["file", "url", "form"]
    fields: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SourceInspectResult:
    ok: bool
    source_id: str
    detected_label: str
    message: str
    summary: dict[str, Any] = field(default_factory=dict)
    already_processed: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SourceAdapter(Protocol):
    descriptor: SourceDescriptor

    def inspect(self, **kwargs: Any) -> SourceInspectResult: ...

    def prepare(self, **kwargs: Any) -> dict[str, Any]: ...
