from __future__ import annotations

import re

from burning_man_scraper.record_parser import normalize_title


def normalize_artist(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"^(by|artist[s]?):\s*", "", value, flags=re.IGNORECASE)
    return normalize_title(cleaned)


def token_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {token for token in normalize_title(value).split() if token}


def similarity_score(left: str | None, right: str | None) -> float:
    left_tokens = token_set(left)
    right_tokens = token_set(right)
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = left_tokens & right_tokens
    union = left_tokens | right_tokens
    return len(intersection) / len(union)


def artist_similarity(left: str | None, right: str | None) -> float:
    return similarity_score(normalize_artist(left), normalize_artist(right))
