from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


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
