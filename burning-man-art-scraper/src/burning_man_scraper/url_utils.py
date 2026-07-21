from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


ALLOWED_HOSTNAME = "history.burningman.org"
TRACKING_PARAMETERS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}

# Keep path separators and existing percent-escapes so encoding is idempotent.
_PATH_SAFE = "/%"
_FRAGMENT_SAFE = "/%"


def encode_http_url(url: str) -> str:
    """Percent-encode unsafe characters in an http(s) URL path/query/fragment.

    Leaves scheme and host untouched. Safe to run repeatedly: existing ``%xx``
    sequences are preserved (``safe`` includes ``%``), so this will not
    double-encode. Spaces and other control chars that break Python's
    ``http.client`` become ``%20`` / ``%xx``.
    """
    text = (url or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return text
    path = quote(parsed.path, safe=_PATH_SAFE)
    query = urlencode(parse_qsl(parsed.query, keep_blank_values=True))
    fragment = quote(parsed.fragment, safe=_FRAGMENT_SAFE) if parsed.fragment else ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, query, fragment))


def normalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in TRACKING_PARAMETERS
    ]
    sorted_query = urlencode(sorted(filtered_query))
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            sorted_query,
            "",
        )
    )


def validate_archive_url(url: str) -> str:
    if not url.strip():
        raise ValueError("URL cannot be empty.")

    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("URL must start with http:// or https://.")

    if parsed.hostname != ALLOWED_HOSTNAME:
        raise ValueError(f"URL hostname must be {ALLOWED_HOSTNAME}.")

    return normalize_url(url)
