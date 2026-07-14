from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from burning_man_scraper.enrichment.models import SearchResult


class SearchCache:
    def __init__(self, cache_dir: Path, ttl_days: int = 30):
        self.cache_dir = cache_dir
        self.ttl = timedelta(days=ttl_days)
        self.last_cache_hit = False
        self.last_error: str | None = None

    def search(
        self,
        provider,
        query: str,
        limit: int = 10,
        refresh: bool = False,
    ) -> list[SearchResult]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_path(provider.name, query, limit, provider_settings(provider))
        if not refresh and path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            retrieved_at = datetime.fromisoformat(payload["retrieved_at"])
            if datetime.now(timezone.utc) - retrieved_at <= self.ttl:
                self.last_cache_hit = True
                self.last_error = payload.get("error")
                return [SearchResult(**item) for item in payload.get("results", [])]

        self.last_cache_hit = False
        try:
            results = provider.search(query, limit=limit)
            self.last_error = None
            self.write(path, query, provider.name, results, None)
            return results
        except Exception as exc:
            self.last_error = str(exc)
            self.write(path, query, provider.name, [], self.last_error)
            return []

    def write(
        self,
        path: Path,
        query: str,
        provider_name: str,
        results: list[SearchResult],
        error: str | None,
    ) -> None:
        payload = {
            "query": query,
            "provider": provider_name,
            "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "results": [asdict(result) for result in results],
            "error": error,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def cache_path(self, provider_name: str, query: str, limit: int, settings: dict[str, object]) -> Path:
        key = json.dumps(
            {
                "provider": provider_name,
                "query": normalize_query(query),
                "limit": limit,
                "settings": settings,
            },
            sort_keys=True,
        )
        return self.cache_dir / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"


def normalize_query(query: str) -> str:
    return " ".join(query.lower().split())


def provider_settings(provider) -> dict[str, object]:
    settings: dict[str, object] = {}
    if hasattr(provider, "base_url"):
        settings["base_url"] = getattr(provider, "base_url")
    return settings

