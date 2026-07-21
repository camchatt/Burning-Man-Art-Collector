"""Registry of Artelier Aggregator source adapters."""

from __future__ import annotations

from burning_man_scraper.sources.artist_website import ArtistWebsiteAdapter
from burning_man_scraper.sources.base import SourceAdapter, SourceDescriptor
from burning_man_scraper.sources.burning_man_csv import BurningManCsvAdapter

_ADAPTERS: dict[str, SourceAdapter] = {
    "artist_website": ArtistWebsiteAdapter(),
    "burning_man_csv": BurningManCsvAdapter(),
}


def list_sources() -> list[SourceDescriptor]:
    return [adapter.descriptor for adapter in _ADAPTERS.values()]


def get_adapter(source_id: str) -> SourceAdapter:
    try:
        return _ADAPTERS[source_id]
    except KeyError as exc:
        raise ValueError(f"Unknown source: {source_id}") from exc
