from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    fetched_timestamp: str
    content_type: str | None
    response_hash: str
    etag: str | None
    last_modified: str | None
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def robots_url_for(source_url: str) -> str:
    parsed = urlsplit(source_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))


class BoundedFetcher:
    def __init__(
        self,
        user_agent: str,
        delay_seconds: float,
        timeout_seconds: float,
        max_retries: int,
        sleep_func=time.sleep,
    ):
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.sleep_func = sleep_func
        self.requested_urls: list[str] = []

    def fetch_source_and_robots(self, source_url: str) -> tuple[FetchResult, FetchResult | None]:
        robots_result = self.fetch(robots_url_for(source_url), allowed_urls={robots_url_for(source_url)})
        source_result = self.fetch(source_url, allowed_urls={source_url})
        return source_result, robots_result

    def fetch(self, url: str, allowed_urls: set[str]) -> FetchResult:
        if url not in allowed_urls:
            raise ValueError(f"Refusing to fetch outside crawl boundary: {url}")

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self.delay_seconds > 0:
                self.sleep_func(self.delay_seconds if attempt == 0 else self.delay_seconds * (2**attempt))
            self.requested_urls.append(url)
            request = Request(url, headers={"User-Agent": self.user_agent})
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read()
                    headers = response.headers
                    return FetchResult(
                        requested_url=url,
                        final_url=response.geturl(),
                        status_code=response.status,
                        fetched_timestamp=_utc_now(),
                        content_type=headers.get("Content-Type"),
                        response_hash=hashlib.sha256(body).hexdigest(),
                        etag=headers.get("ETag"),
                        last_modified=headers.get("Last-Modified"),
                        body=body,
                    )
            except HTTPError as exc:
                body = exc.read()
                return FetchResult(
                    requested_url=url,
                    final_url=exc.url,
                    status_code=exc.code,
                    fetched_timestamp=_utc_now(),
                    content_type=exc.headers.get("Content-Type"),
                    response_hash=hashlib.sha256(body).hexdigest(),
                    etag=exc.headers.get("ETag"),
                    last_modified=exc.headers.get("Last-Modified"),
                    body=body,
                )
            except URLError as exc:
                last_error = exc

        raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
