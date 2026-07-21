"""Source adapters for the Artelier Aggregator."""

from burning_man_scraper.sources.base import (
    FieldValue,
    NormalizedRecord,
    SourceDescriptor,
    SourceInspectResult,
)

__all__ = [
    "FieldValue",
    "NormalizedRecord",
    "SourceDescriptor",
    "SourceInspectResult",
    "get_adapter",
    "list_sources",
]


def get_adapter(source_id: str):
    from burning_man_scraper.sources.registry import get_adapter as _get_adapter

    return _get_adapter(source_id)


def list_sources():
    from burning_man_scraper.sources.registry import list_sources as _list_sources

    return _list_sources()
