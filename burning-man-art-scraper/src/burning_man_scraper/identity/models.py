from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


IDENTITY_SCHEMA_VERSION = "artist-identity-v1"


@dataclass(frozen=True)
class ResolvedPerson:
    name: str
    role: str | None = None
    confidence: float = 0.0
    source_url: str | None = None
    source_snippet: str | None = None


@dataclass
class IdentityResult:
    year: int
    project_title: str
    archive_uid: str | None
    archive_url: str | None
    archive_credit: str
    credit_type: str
    legal_name: str | None = None
    playa_name: str | None = None
    playa_name_confidence: str = "none"
    collective_name: str | None = None
    named_people: list[str] = field(default_factory=list)
    resolved_people: list[ResolvedPerson] = field(default_factory=list)
    identity_status: str = "unresolved"
    artist_website: str | None = None
    evidence_urls: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "project_title": self.project_title,
            "archive_uid": self.archive_uid or "",
            "archive_url": self.archive_url or "",
            "archive_credit": self.archive_credit,
            "credit_type": self.credit_type,
            "legal_name": self.legal_name or "",
            "playa_name": self.playa_name or "",
            "playa_name_confidence": self.playa_name_confidence,
            "collective_name": self.collective_name or "",
            "named_people": " | ".join(self.named_people),
            "resolved_people": " | ".join(
                f"{person.name}"
                + (f" ({person.role})" if person.role else "")
                + (f" [{person.confidence:.2f}]" if person.confidence else "")
                for person in self.resolved_people
            ),
            "identity_status": self.identity_status,
            "artist_website": self.artist_website or "",
            "evidence_urls": " | ".join(self.evidence_urls),
            "notes": " | ".join(self.notes),
            "search_queries": " | ".join(self.search_queries),
        }
