from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


PREFERRED_HOST_FRAGMENTS = (
    "burningman.org",
    "history.burningman.org",
    "burningman.widen.net",
    "widen.net",
)

REJECT_URL_FRAGMENTS = (
    "logo",
    "favicon",
    "avatar",
    "profile",
    "sprite",
    "icon-",
    "/icon",
)


@dataclass
class HeroResolution:
    hero_image_url: str = ""
    hero_image_source_page: str = ""
    hero_image_attribution: str = ""
    hero_image_confidence: str = ""
    review_flags: list[str] | None = None
    provenance: str = ""


def resolve_hero(
    *,
    uid: str | None,
    title: str | None,
    year: int | str | None,
    verification_row: dict | None,
    archive_record: dict | None,
    image_entry: dict | None,
    collector_bundle: dict | None,
    proof_url: str | None = None,
    artist_url: str | None = None,
    fetch_missing: bool = False,
    user_agent: str = "BurningManArtBmIngest/0.1",
) -> HeroResolution:
    # 1) Verification / image manifest
    if verification_row:
        url = (verification_row.get("hero_image_url") or "").strip()
        active = str(verification_row.get("hero_image_active") or "").lower() in {"true", "1", "yes"}
        if url and (active or not verification_row.get("hero_image_active")):
            attr = (verification_row.get("public_credit_language") or "").strip()
            source = (verification_row.get("archive_url") or "").strip()
            confidence = "high" if active and _preferred_host(url) else "medium"
            flags = []
            if not active or not attr:
                flags.append("hero_needs_review")
                confidence = "needs_review" if not active else confidence
            return HeroResolution(
                hero_image_url=url,
                hero_image_source_page=source,
                hero_image_attribution=attr,
                hero_image_confidence=confidence,
                review_flags=flags,
                provenance="verification",
            )

    if image_entry:
        images = image_entry.get("images") or []
        for image in images:
            url = (image.get("image_url") or "").strip()
            if not url or _rejected_url(url):
                continue
            if not image.get("link_active", True):
                continue
            attr = (image.get("credit_text") or image.get("photographer_credit") or "").strip()
            source = (image.get("source_page_url") or image_entry.get("archive_url") or "").strip()
            review = bool(image.get("review_required"))
            confidence = "high" if _preferred_host(url) and not review else "medium"
            flags = ["hero_needs_review"] if review or not attr else []
            if flags:
                confidence = "needs_review"
            return HeroResolution(
                hero_image_url=url,
                hero_image_source_page=source,
                hero_image_attribution=attr,
                hero_image_confidence=confidence,
                review_flags=flags,
                provenance="image_manifest",
            )

    # 2) Archive images
    if archive_record:
        for url in archive_record.get("image_urls") or []:
            url = (url or "").strip()
            if not url or _rejected_url(url):
                continue
            source = (archive_record.get("canonical_source_url") or "").strip()
            confidence = "medium" if _preferred_host(url) else "low"
            flags = ["hero_needs_review"] if confidence == "low" else []
            return HeroResolution(
                hero_image_url=url,
                hero_image_source_page=source,
                hero_image_attribution="Photo courtesy of Burning Man Project History Archive"
                if _preferred_host(url)
                else "",
                hero_image_confidence="needs_review" if flags else confidence,
                review_flags=flags,
                provenance="archive_index",
            )

    # 3) Collector export
    if collector_bundle:
        mapped = collector_bundle.get("mapped") or {}
        original = collector_bundle.get("original") or {}
        url = (mapped.get("hero_image_url") or original.get("primary_image_url") or "").strip()
        if url and not _rejected_url(url):
            attr = (mapped.get("public_credit_language") or original.get("image_credit_text") or "").strip()
            source = (original.get("canonical_source_url") or mapped.get("proof_external_url") or "").strip()
            return HeroResolution(
                hero_image_url=url,
                hero_image_source_page=source,
                hero_image_attribution=attr,
                hero_image_confidence="medium" if attr else "needs_review",
                review_flags=[] if attr else ["hero_needs_review"],
                provenance="collector_export",
            )

    # 4) Optional remote probe
    if fetch_missing:
        for page in (proof_url, artist_url):
            if not page:
                continue
            probed = _probe_og_image(page, user_agent=user_agent)
            if probed:
                return HeroResolution(
                    hero_image_url=probed,
                    hero_image_source_page=page,
                    hero_image_attribution="",
                    hero_image_confidence="needs_review",
                    review_flags=["hero_needs_review"],
                    provenance="og_fetch",
                )

    return HeroResolution(review_flags=["hero_missing"])


def _preferred_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(fragment in host for fragment in PREFERRED_HOST_FRAGMENTS)


def _rejected_url(url: str) -> bool:
    lowered = url.lower()
    return any(fragment in lowered for fragment in REJECT_URL_FRAGMENTS)


class _OgImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.image_url = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        if self.image_url or tag.lower() != "meta":
            return
        mapping = {key.lower(): (value or "") for key, value in attrs}
        prop = mapping.get("property") or mapping.get("name")
        if prop.lower() in {"og:image", "twitter:image", "twitter:image:src"}:
            self.image_url = mapping.get("content", "").strip()


def _probe_og_image(url: str, *, user_agent: str, timeout_seconds: float = 15.0) -> str:
    try:
        request = Request(url, headers={"User-Agent": user_agent})
        with urlopen(request, timeout=timeout_seconds) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "html" not in content_type and "text" not in content_type:
                return ""
            html = response.read(250_000).decode("utf-8", errors="replace")
        parser = _OgImageParser()
        parser.feed(html)
        candidate = parser.image_url
        if candidate and not _rejected_url(candidate) and urlsplit(candidate).scheme in {"http", "https"}:
            return candidate
    except Exception:
        return ""
    return ""
