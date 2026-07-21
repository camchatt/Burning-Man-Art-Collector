"""Evidence-based artwork extraction models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PageType = Literal[
    "artwork_collection",
    "artwork_detail",
    "editorial_project_detail",
    "navigation",
    "commerce_utility",
    "irrelevant",
    "unknown",
]


@dataclass
class ArtworkEvidence:
    field: str
    value: str
    source_url: str = ""
    source_kind: str = ""
    confidence: float = 0.0
    selector_or_signal: str = ""


@dataclass
class ImageEvidence:
    url: str
    alt: str = ""
    source_kind: str = "img"


@dataclass
class ArtworkCandidate:
    title: str = ""
    year: str = ""
    detail_url: str = ""
    collection_url: str = ""
    images: list[ImageEvidence] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    evidence: list[ArtworkEvidence] = field(default_factory=list)
    confidence: float = 0.0
    review_flags: list[str] = field(default_factory=list)
    excerpt: str = ""
    source_granularity: str = "Gallery caption"
    page_text: str = ""
    page_url: str = ""

    @property
    def image_urls(self) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []
        for image in self.images:
            key = image.url.casefold()
            if image.url and key not in seen:
                seen.add(key)
                urls.append(image.url)
        return urls


@dataclass
class PageInterpretation:
    page_type: PageType
    confidence: str
    reasons: list[str] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)
    candidates: list[ArtworkCandidate] = field(default_factory=list)
    discovered_detail_urls: list[str] = field(default_factory=list)
    render_recommended: bool = False
    render_reasons: list[str] = field(default_factory=list)
