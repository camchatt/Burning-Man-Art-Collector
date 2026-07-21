"""Paired image evidence extraction for artist websites."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from burning_man_scraper.sources.artist_website.models import ImageEvidence

LAZY_ATTRS = (
    "data-src",
    "data-image",
    "data-original",
    "data-lazy-src",
    "data-srcset",
)

TRACKING_PIXEL_RE = re.compile(r"(?:1x1|pixel|spacer|tracking|beacon)", re.I)
LOGO_HINT_RE = re.compile(r"\b(?:logo|icon|favicon|sprite|avatar|badge|brand)\b", re.I)
DATA_URI_RE = re.compile(r"^data:", re.I)
FORMAT_WIDTH_RE = re.compile(r"^\d+w$", re.I)


def _normalize_url(url: str, base_url: str) -> str:
    from burning_man_scraper.sources.artist_website.ingest import normalize_url

    return normalize_url(url, base_url)


def image_identity_key(url: str) -> str:
    """Identity that ignores CDN resize params such as Squarespace ?format=750w."""
    if not url:
        return ""
    parsed = urlparse(url)
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() != "format" and not FORMAT_WIDTH_RE.match(value or "")
        )
    )
    path = (parsed.path or "/").rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), (parsed.netloc or "").lower(), path, "", query, ""))


def prefers_original_over_resized(url: str) -> bool:
    """Prefer originals without Squarespace-style format width query params."""
    parsed = urlparse(url)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() == "format" and re.search(r"\d+w", value or "", re.I):
            return False
    return True


def _largest_from_srcset(srcset: str, base_url: str) -> str:
    best_url = ""
    best_score = -1
    for part in srcset.split(","):
        token = part.strip().split()
        if not token:
            continue
        url = token[0]
        score = 0
        if len(token) > 1:
            descriptor = token[1]
            match = re.match(r"(\d+)(w|x)", descriptor)
            if match:
                score = int(match.group(1)) * (1000 if match.group(2) == "x" else 1)
        if score >= best_score and not DATA_URI_RE.match(url):
            best_score = score
            best_url = url
    return _normalize_url(best_url, base_url) if best_url else ""


def _from_responsive_src(raw: str, base_url: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        normalized = raw.replace("'", '"')
        data = json.loads(normalized)
        if isinstance(data, dict) and data:
            best_key = max(data.keys(), key=lambda key: int(re.sub(r"\D", "", str(key)) or 0))
            value = data[best_key]
            if isinstance(value, str) and not DATA_URI_RE.match(value):
                return _normalize_url(value, base_url)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    if raw.startswith("http") and not DATA_URI_RE.match(raw):
        return _normalize_url(raw, base_url)
    return ""


def is_usable_image_url(url: str) -> bool:
    if not url or DATA_URI_RE.match(url):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.casefold()
    if TRACKING_PIXEL_RE.search(path) or LOGO_HINT_RE.search(path):
        return False
    return True


def _looks_like_logo_tag(image: Tag, alt: str) -> bool:
    classes = image.get("class") or []
    class_blob = classes if isinstance(classes, str) else " ".join(classes)
    if LOGO_HINT_RE.search(class_blob):
        return True
    if LOGO_HINT_RE.search(alt or ""):
        return True
    return False


def has_lazy_placeholders(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    for image in soup.find_all("img"):
        src = (image.get("src") or "").strip()
        if DATA_URI_RE.match(src) or src in {"", "about:blank"}:
            if any(image.get(attr) for attr in (*LAZY_ATTRS, "data-responsive-src", "srcset")):
                return True
    return False


def _pick_best_candidate(candidates: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Prefer data-src originals, then srcset, then usable src; skip logos/data URIs."""
    usable = [(url, kind) for url, kind in candidates if is_usable_image_url(url)]
    if not usable:
        return None
    # Prefer originals without resize query, and lazy/data-src over plain src.
    kind_rank = {
        "data-src": 0,
        "data-image": 1,
        "data-original": 2,
        "data-lazy-src": 3,
        "data-srcset": 4,
        "data-responsive-src": 5,
        "srcset": 6,
        "src": 7,
    }

    def sort_key(item: tuple[str, str]) -> tuple[int, int, int]:
        url, kind = item
        return (
            0 if prefers_original_over_resized(url) else 1,
            kind_rank.get(kind, 9),
            -len(url),
        )

    usable.sort(key=sort_key)
    return usable[0]


def extract_image_from_tag(image: Tag, base_url: str) -> ImageEvidence | None:
    alt = (image.get("alt") or "").strip()
    if _looks_like_logo_tag(image, alt):
        return None

    candidates: list[tuple[str, str]] = []

    # Prefer lazy originals before resized src variants.
    for attr in (*LAZY_ATTRS, "src"):
        value = (image.get(attr) or "").strip()
        if not value:
            continue
        if attr.endswith("srcset"):
            url = _largest_from_srcset(value, base_url)
        else:
            url = "" if DATA_URI_RE.match(value) else _normalize_url(value, base_url)
        if url:
            candidates.append((url, attr))

    responsive = _from_responsive_src(image.get("data-responsive-src") or "", base_url)
    if responsive:
        candidates.append((responsive, "data-responsive-src"))

    srcset = (image.get("srcset") or "").strip()
    if srcset:
        url = _largest_from_srcset(srcset, base_url)
        if url:
            candidates.append((url, "srcset"))

    picked = _pick_best_candidate(candidates)
    if not picked:
        return None
    url, kind = picked
    return ImageEvidence(url=url, alt=alt, source_kind=kind)


def extract_images_from_soup(soup: BeautifulSoup, base_url: str) -> list[ImageEvidence]:
    images: list[ImageEvidence] = []
    seen: set[str] = set()
    for image in soup.find_all("img"):
        evidence = extract_image_from_tag(image, base_url)
        if not evidence:
            continue
        key = image_identity_key(evidence.url).casefold()
        if key in seen:
            continue
        seen.add(key)
        images.append(evidence)
    return images


def extract_meta_images(soup: BeautifulSoup, base_url: str) -> list[ImageEvidence]:
    images: list[ImageEvidence] = []
    for prop, kind in (
        ("og:image", "og:image"),
        ("twitter:image", "twitter:image"),
    ):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if not tag:
            continue
        content = (tag.get("content") or "").strip()
        if content and is_usable_image_url(content):
            images.append(
                ImageEvidence(url=_normalize_url(content, base_url), alt="", source_kind=kind)
            )
    return images


def prefer_artwork_images(
    images: list[ImageEvidence],
    *,
    allow_logo_fallback: bool = False,
    artist_name: str = "",
) -> list[ImageEvidence]:
    usable = [image for image in images if is_usable_image_url(image.url)]
    artist = (artist_name or "").strip().casefold()
    non_logo: list[ImageEvidence] = []
    for image in usable:
        alt = image.alt or ""
        if LOGO_HINT_RE.search(alt) or LOGO_HINT_RE.search(image.url):
            continue
        if artist and alt.strip().casefold() == artist:
            continue
        non_logo.append(image)
    if non_logo:
        # Dedupe CDN resize variants; prefer originals without ?format=
        by_key: dict[str, ImageEvidence] = {}
        order: list[str] = []
        for image in non_logo:
            key = image_identity_key(image.url).casefold()
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = image
                order.append(key)
                continue
            if (not prefers_original_over_resized(existing.url)) and prefers_original_over_resized(
                image.url
            ):
                by_key[key] = image
        return [by_key[key] for key in order]
    return usable if allow_logo_fallback else []


def preferred_hero_url(
    image_urls: list[str] | list[ImageEvidence],
    *,
    artist_name: str = "",
) -> str:
    """Pick the first non-logo artwork URL for Artelier hero_image_url."""
    evidence: list[ImageEvidence] = []
    for item in image_urls or []:
        if isinstance(item, ImageEvidence):
            evidence.append(item)
        elif item:
            evidence.append(ImageEvidence(url=str(item), alt="", source_kind="row"))
    preferred = prefer_artwork_images(evidence, artist_name=artist_name)
    if preferred:
        return preferred[0].url
    return ""


def page_has_client_gallery_markers(html: str) -> bool:
    markers = (
        "sqs-gallery",
        "artwork-grid",
        "product-grid",
        "portfolio-grid",
        "shopify-section",
        "artlogic",
        "lazyload",
        "data-src=",
        "data-responsive-src",
        "index-item",
        "project-slide-image",
    )
    lowered = html.casefold()
    return any(marker.casefold() in lowered for marker in markers)
