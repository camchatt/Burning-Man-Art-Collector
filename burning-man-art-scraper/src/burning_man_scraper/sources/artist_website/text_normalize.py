"""Display / match / identity normalization for artist-website extraction.

Three layers (do not mix):
1. normalize_display_text — human-facing titles and captions
2. normalize_match_key — dedup / identity keys only (never write into title fields)
3. normalize_identity_url — single crawl/discover/merge URL policy
"""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

# Tracking / session / commerce-noise params stripped for identity. Semantic
# params (keep, id, yyyy, page, sku, etc.) are preserved unless listed here.
# `variant` is Shopify storefront noise for the same product identity.
ARTIST_SITE_TRACKING_PARAMETERS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "ref",
        "source",
        "sessionid",
        "itemid",
        "msclkid",
        "twclid",
        "igshid",
        "yclid",
        "variant",
    }
)

_SMART_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u201a": "'",  # ‚
        "\u201b": "'",  # ‛
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
        "\u201e": '"',  # „
        "\u2032": "'",  # ′
        "\u2033": '"',  # ″
        "\u00ab": '"',  # «
        "\u00bb": '"',  # »
        "\u2013": "-",  # –
        "\u2014": "—",  # — keep em dash as meaning (display); match key folds later
    }
)

# Common UTF-8→Latin-1 mojibake repairs (apply before NFKC where safe).
_MOJIBAKE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Ã—", "×"),
    ("â€œ", '"'),
    ("â€", '"'),
    ("â€˜", "'"),
    ("â€™", "'"),
    ("â€”", "—"),
    ("â€“", "–"),
    ("Ã©", "é"),
    ("Ã¨", "è"),
    ("Ã¡", "á"),
    ("Ã±", "ñ"),
    ("Ã¼", "ü"),
    ("Ã¶", "ö"),
    ("Ã¤", "ä"),
)


def _repair_mojibake(text: str) -> str:
    for bad, good in _MOJIBAKE_REPLACEMENTS:
        if bad in text:
            text = text.replace(bad, good)
    return text


def normalize_display_text(value: str | None) -> str:
    """Whitespace, entities, smart quotes / mojibake; preserve case, accents, punctuation.

    Uses NFC (not NFKC) so compatibility characters like Nº stay intact.
    """
    if not value:
        return ""
    text = html.unescape(str(value))
    text = _repair_mojibake(text)
    text = text.translate(_SMART_QUOTE_MAP)
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_match_key(value: str | None) -> str:
    """Casefold + accent-fold key for dedup only. Never export as project_title."""
    text = normalize_display_text(value)
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", ascii_folded.casefold()).strip()


def normalize_dimension_text(value: str | None) -> str:
    """Normalize dimension strings for parsing (× / mojibake) without inventing units."""
    text = normalize_display_text(value)
    if not text:
        return ""
    # Unify multiplication signs after mojibake repair.
    text = text.replace("Ã—", "×")
    text = re.sub(r"\s*[xX×]\s*", " × ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def title_from_slug_words(slug_words: str) -> str:
    """Slug fallback for titles: keep words as-is; do not invent Title Case."""
    text = normalize_display_text(slug_words)
    if not text:
        return ""
    # Only apply .title() when the slug is ALLCAPS-looking single token already mixed;
    # otherwise leave lowercase slug words unchanged (flagged by caller).
    if text.isupper() and " " in text:
        return text
    if text.islower():
        return text  # visible casing unknown — do not invent
    return text


def normalize_identity_url(url: str, base_url: str | None = None) -> str:
    """Artist-website URL identity: scheme/host case, trailing slash, fragment, tracking strip.

    Preserves path and semantic query params. www vs bare host are left as-is
    (different hosts) except registrable comparison is handled elsewhere.
    """
    absolute = urljoin(base_url or url, url or "")
    parsed = urlparse(absolute)
    scheme = (parsed.scheme or "https").lower()
    if scheme not in {"http", "https"}:
        return absolute
    host = (parsed.hostname or "").lower()
    if not host:
        return absolute
    if parsed.port and parsed.port not in (80, 443):
        netloc = f"{host}:{parsed.port}"
    else:
        netloc = host
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    filtered = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_"):
            continue
        if lower in ARTIST_SITE_TRACKING_PARAMETERS:
            continue
        filtered.append((key, value))
    query = urlencode(sorted(filtered))
    # Drop fragment always for identity.
    return urlunparse((scheme, netloc, path, "", query, ""))
