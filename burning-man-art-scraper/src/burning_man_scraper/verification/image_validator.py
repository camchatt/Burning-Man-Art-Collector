from __future__ import annotations

import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from burning_man_scraper.verification.models import ImageAsset


MIN_IMAGE_BYTES = 5120
BURNING_MAN_OFFICIAL_HOSTS = {
    "burningman.widen.net",
    "history.burningman.org",
    "burningman.org",
}
DEFAULT_OFFICIAL_CREDIT = "Photo courtesy of Burning Man Project History Archive"


class ImageValidator:
    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float = 20.0,
        delay_seconds: float = 0.5,
        min_image_bytes: int = MIN_IMAGE_BYTES,
    ):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.delay_seconds = delay_seconds
        self.min_image_bytes = min_image_bytes
        self._last_request_at = 0.0

    def validate(
        self,
        image_url: str,
        *,
        source_page_url: str | None = None,
        alt_text: str | None = None,
    ) -> ImageAsset:
        attribution = infer_attribution(image_url, alt_text)
        try:
            self._respect_delay()
            request = Request(image_url, method="HEAD", headers={"User-Agent": self.user_agent})
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = response.status
                final_url = response.geturl()
                content_type = response.headers.get("Content-Type")
                content_length = _parse_int_header(response.headers.get("Content-Length"))
                active = _is_active_image(status, content_type, content_length, self.min_image_bytes)
                return ImageAsset(
                    image_url=image_url,
                    final_url=final_url,
                    http_status=status,
                    content_type=content_type,
                    content_length=content_length,
                    link_active=active,
                    alt_text=alt_text,
                    photographer_credit=attribution["photographer_credit"],
                    credit_text=attribution["credit_text"],
                    source_page_url=source_page_url,
                    source_type=attribution["source_type"],
                    attribution_confidence=attribution["attribution_confidence"],
                    review_required=attribution["review_required"],
                )
        except HTTPError as exc:
            if exc.code in {405, 501}:
                return self._validate_with_get(
                    image_url,
                    source_page_url=source_page_url,
                    alt_text=alt_text,
                    attribution=attribution,
                )
            return ImageAsset(
                image_url=image_url,
                http_status=exc.code,
                alt_text=alt_text,
                source_page_url=source_page_url,
                source_type=attribution["source_type"],
                attribution_confidence="missing",
                review_required=True,
                validation_error=str(exc),
            )
        except URLError as exc:
            return ImageAsset(
                image_url=image_url,
                alt_text=alt_text,
                source_page_url=source_page_url,
                source_type=attribution["source_type"],
                attribution_confidence="missing",
                review_required=True,
                validation_error=str(exc),
            )

    def _validate_with_get(
        self,
        image_url: str,
        *,
        source_page_url: str | None,
        alt_text: str | None,
        attribution: dict[str, str | bool | None],
    ) -> ImageAsset:
        try:
            self._respect_delay()
            request = Request(
                image_url,
                headers={"User-Agent": self.user_agent, "Range": "bytes=0-8191"},
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = response.status
                final_url = response.geturl()
                content_type = response.headers.get("Content-Type")
                content_length = _parse_int_header(response.headers.get("Content-Length"))
                active = _is_active_image(status, content_type, content_length, self.min_image_bytes)
                return ImageAsset(
                    image_url=image_url,
                    final_url=final_url,
                    http_status=status,
                    content_type=content_type,
                    content_length=content_length,
                    link_active=active,
                    alt_text=alt_text,
                    photographer_credit=attribution["photographer_credit"],
                    credit_text=attribution["credit_text"],
                    source_page_url=source_page_url,
                    source_type=str(attribution["source_type"]),
                    attribution_confidence=str(attribution["attribution_confidence"]),
                    review_required=bool(attribution["review_required"]),
                )
        except (HTTPError, URLError) as exc:
            status = exc.code if isinstance(exc, HTTPError) else None
            return ImageAsset(
                image_url=image_url,
                http_status=status,
                alt_text=alt_text,
                source_page_url=source_page_url,
                source_type=str(attribution["source_type"]),
                attribution_confidence="missing",
                review_required=True,
                validation_error=str(exc),
            )

    def _respect_delay(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_request_at = time.time()


def infer_attribution(image_url: str, alt_text: str | None = None) -> dict[str, str | bool | None]:
    host = (urlsplit(image_url).hostname or "").lower()
    photographer_credit = None
    credit_text = None
    attribution_confidence = "missing"
    review_required = True
    source_type = "other"

    if host in BURNING_MAN_OFFICIAL_HOSTS or host.endswith(".burningman.org"):
        source_type = "burning_man_official"
        credit_text = DEFAULT_OFFICIAL_CREDIT
        attribution_confidence = "inferred"
        review_required = False
    elif "googleusercontent.com" in host:
        source_type = "third_party_hosted"
        attribution_confidence = "missing"
        review_required = True
    elif alt_text:
        credit_text = alt_text.strip()
        attribution_confidence = "directly_stated"
        review_required = False

    if alt_text and _looks_like_photo_credit(alt_text):
        photographer_credit = alt_text.strip()
        credit_text = alt_text.strip()
        attribution_confidence = "directly_stated"
        review_required = False

    return {
        "photographer_credit": photographer_credit,
        "credit_text": credit_text,
        "source_type": source_type,
        "attribution_confidence": attribution_confidence,
        "review_required": review_required,
    }


def _looks_like_photo_credit(value: str) -> bool:
    lowered = value.lower()
    return any(token in lowered for token in ("photo", "photograph", "credit", "©", "copyright", "by "))


def _is_active_image(
    status: int,
    content_type: str | None,
    content_length: int | None,
    min_image_bytes: int,
) -> bool:
    if status not in {200, 206}:
        return False
    if content_type and not content_type.lower().startswith("image/"):
        return False
    if content_length is not None and content_length < min_image_bytes:
        return False
    return True


def _parse_int_header(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None
