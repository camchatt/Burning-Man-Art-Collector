from __future__ import annotations

from dataclasses import dataclass, field
import re

from burning_man_scraper.identity.classifier import (
    classify_archive_credit,
    clean_text,
    _looks_like_legal_name,
    _looks_like_playa_name,
)


STUDIO_HINTS = (" studio", " studios", " workshop", " works", " productions", " lab", " labs")
ORG_HINTS = (
    " foundation",
    " institute",
    " association",
    " society",
    " nonprofit",
    " non-profit",
    " company",
    " co.",
    " collective",
    " crew",
    " guild",
    " group",
    " team",
    " inc",
    " llc",
    " art department",
    " burners",
    " project",
    " projects",
)
CAMP_HINTS = ("camp ", " camp", "theme camp")


@dataclass
class ContributorNormalization:
    source_artist_credit: str
    contributor_display_name: str = ""
    additional_contributor_credits: str = ""
    contributor_kind: str = "unknown"
    contributor_first_name: str = ""
    contributor_last_name: str = ""
    playa_name: str = ""
    playa_name_confidence: str = "none"
    review_flags: list[str] = field(default_factory=list)


def normalize_contributor(source_credit: str | None) -> ContributorNormalization:
    credit = clean_text(source_credit)
    result = ContributorNormalization(source_artist_credit=credit)
    if not credit:
        result.contributor_kind = "unknown"
        result.review_flags.append("contributor_kind_uncertain")
        return result

    classification = classify_archive_credit(credit)
    result.contributor_kind = _map_contributor_kind(classification.credit_type, credit)
    if result.contributor_kind == "unknown":
        result.review_flags.append("contributor_kind_uncertain")

    playa = classification.playa_name or ""
    confidence = classification.playa_name_confidence or "none"
    if classification.credit_type == "alias_or_unknown" and _looks_like_playa_name(credit):
        playa = credit
        confidence = "medium"
        result.review_flags.append("playa_name_uncertain")
    result.playa_name = playa if confidence in {"high", "medium", "low"} else ""
    result.playa_name_confidence = confidence if result.playa_name else "none"
    if result.playa_name and confidence not in {"high"} and "playa_name_uncertain" not in result.review_flags:
        if confidence in {"medium", "low"}:
            result.review_flags.append("playa_name_uncertain")

    people = list(classification.named_people or [])
    legal = clean_text(classification.legal_name)
    if legal and ";" in legal:
        people = [clean_text(part) for part in legal.split(";") if clean_text(part)]
        legal = people[0] if people else legal

    if classification.credit_type == "multi_person" and people:
        result.contributor_kind = "multiple"
        result.contributor_display_name = people[0]
        result.additional_contributor_credits = "; ".join(people[1:])
    elif classification.credit_type in {"collective", "hybrid"}:
        primary = legal or (people[0] if people else "")
        if primary and _looks_like_legal_name(primary):
            result.contributor_display_name = primary
            collective = classification.collective_name or credit
            extras = []
            if collective and collective.lower() != primary.lower():
                extras.append(collective)
            extras.extend(person for person in people[1:] if person.lower() != primary.lower())
            result.additional_contributor_credits = "; ".join(_unique(extras))
        else:
            result.contributor_display_name = classification.collective_name or credit
            result.additional_contributor_credits = "; ".join(
                person for person in people if person.lower() != result.contributor_display_name.lower()
            )
    elif legal:
        result.contributor_display_name = legal
        extras = [person for person in people if person.lower() != legal.lower() and person.lower() != (playa or "").lower()]
        if playa and classification.credit_type == "person_with_playa_name":
            # playa stays in playa_name, not additional credits
            pass
        result.additional_contributor_credits = "; ".join(_unique(extras))
    elif playa and classification.credit_type == "alias_or_unknown":
        result.contributor_display_name = playa
    else:
        result.contributor_display_name = credit

    # Name split for a single clear legal-looking primary.
    first, last, split_ok = _split_first_last(result.contributor_display_name)
    if split_ok:
        result.contributor_first_name = first
        result.contributor_last_name = last
    elif result.contributor_kind == "individual" and result.contributor_display_name:
        result.review_flags.append("name_split_uncertain")

    result.review_flags = _unique(result.review_flags)
    return result


def _map_contributor_kind(credit_type: str, credit: str) -> str:
    lowered = f" {credit.lower()} "
    if any(hint in lowered for hint in CAMP_HINTS):
        return "theme_camp"
    if any(hint in lowered for hint in STUDIO_HINTS):
        return "studio"
    if any(hint in lowered for hint in ORG_HINTS):
        return "organization"
    if credit_type in {"person", "person_with_playa_name"}:
        return "individual"
    if credit_type in {"collective", "hybrid"}:
        return "collective"
    if credit_type == "organization":
        return "organization"
    if credit_type == "multi_person":
        return "multiple"
    if credit_type in {"alias_or_unknown", "alias_pair"}:
        return "unknown"
    return "unknown"


def _split_first_last(name: str) -> tuple[str, str, bool]:
    text = clean_text(name)
    if not text or not _looks_like_legal_name(text):
        return "", "", False
    if re.search(r"[;/&]| and ", text, re.I):
        return "", "", False
    # Strip simple honorifics.
    text = re.sub(r"^(mr|mrs|ms|dr)\.?\s+", "", text, flags=re.I)
    tokens = [token for token in text.split() if token]
    if len(tokens) == 2:
        return tokens[0], tokens[1], True
    if len(tokens) == 3 and re.fullmatch(r"[A-Z]\.?", tokens[1]):
        return tokens[0], tokens[2], True
    if len(tokens) == 3:
        return tokens[0], " ".join(tokens[1:]), True
    return "", "", False


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
