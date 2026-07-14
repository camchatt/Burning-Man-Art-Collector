from __future__ import annotations

from burning_man_scraper.bm_ingest.contributors import (
    ContributorNormalization,
    _map_contributor_kind,
    _split_first_last,
    _unique,
)
from burning_man_scraper.identity.classifier import clean_text, _looks_like_legal_name


USEFUL_IDENTITY_FIELDS = (
    "legal_name",
    "playa_name",
    "collective_name",
    "credit_type",
    "named_people",
)


def identity_is_useful(identity: dict | None) -> bool:
    if not identity:
        return False
    status = (identity.get("identity_status") or "").strip().lower()
    if status in {"resolved", "partial"}:
        return True
    return any((identity.get(field) or "").strip() for field in USEFUL_IDENTITY_FIELDS)


def contributor_from_identity(
    identity: dict,
    *,
    fallback_source_credit: str = "",
) -> ContributorNormalization:
    """Map a cached identity_report row into contributor fields (no network)."""
    source_credit = clean_text(identity.get("archive_credit") or fallback_source_credit)
    result = ContributorNormalization(source_artist_credit=source_credit)
    credit_type = (identity.get("credit_type") or "").strip()
    legal_raw = clean_text(identity.get("legal_name"))
    collective = clean_text(identity.get("collective_name"))
    playa = clean_text(identity.get("playa_name"))
    confidence = (identity.get("playa_name_confidence") or "none").strip() or "none"
    named = _split_named_people(identity.get("named_people"))
    entities = _split_multi_entities(legal_raw) or list(named)

    # Never keep a semicolon-joined blob as the primary person name.
    primary = entities[0] if entities else ""
    extras = list(entities[1:]) if len(entities) > 1 else []

    result.contributor_kind = _map_credit_type(credit_type, source_credit, entities)
    if result.contributor_kind == "unknown":
        result.review_flags.append("contributor_kind_uncertain")

    if credit_type == "multi_person" or (len(entities) > 1 and credit_type not in {"person", "person_with_playa_name"}):
        result.contributor_kind = "multiple"
        result.contributor_display_name = primary or source_credit
        if collective and collective.lower() not in {e.lower() for e in extras}:
            # Prefer keeping collective as an additional credit, not primary, when a person leads.
            if primary and _looks_like_legal_name(primary):
                extras.append(collective)
            elif not primary:
                result.contributor_display_name = collective
        result.additional_contributor_credits = "; ".join(_unique(extras))
    elif credit_type in {"collective", "hybrid"} or result.contributor_kind in {"collective", "organization", "theme_camp", "studio"}:
        if primary and _looks_like_legal_name(primary) and collective and collective.lower() != primary.lower():
            result.contributor_display_name = primary
            more = extras[1:] if extras else []
            result.additional_contributor_credits = "; ".join(_unique([collective, *more]))
        else:
            result.contributor_display_name = collective or primary or source_credit
            result.additional_contributor_credits = "; ".join(
                _unique([e for e in extras if e.lower() != result.contributor_display_name.lower()])
            )
    elif primary:
        result.contributor_display_name = primary
        if collective and collective.lower() != primary.lower():
            extras.append(collective)
        result.additional_contributor_credits = "; ".join(
            _unique([e for e in extras if e.lower() != primary.lower() and e.lower() != (playa or "").lower()])
        )
    elif collective:
        result.contributor_display_name = collective
    else:
        result.contributor_display_name = source_credit

    if playa:
        # Prefer untouched source credit as Name when identity only found a Burner/alias.
        if (
            not primary
            and source_credit
            and source_credit.lower() != playa.lower()
            and (not result.contributor_display_name or result.contributor_display_name.lower() == playa.lower())
        ):
            result.contributor_display_name = source_credit
        result.playa_name = playa
        result.playa_name_confidence = confidence if confidence in {"high", "medium", "low"} else "low"
        if result.playa_name_confidence != "high":
            result.review_flags.append("playa_name_uncertain")
    else:
        result.playa_name = ""
        result.playa_name_confidence = "none"

    first, last, split_ok = _split_first_last(result.contributor_display_name)
    if split_ok and result.contributor_kind in {"individual", "multiple"}:
        result.contributor_first_name = first
        result.contributor_last_name = last
    elif result.contributor_kind == "individual" and result.contributor_display_name:
        result.review_flags.append("name_split_uncertain")

    status = (identity.get("identity_status") or "").strip().lower()
    if status == "needs_review" or credit_type in {"alias_or_unknown", "alias_pair"}:
        result.review_flags.append("identity_needs_review")

    result.review_flags = _unique(result.review_flags)
    return result


def collapse_person_or_org(contributor_kind: str) -> str:
    kind = (contributor_kind or "unknown").strip().lower()
    if kind == "individual":
        return "person"
    if kind in {"organization", "collective", "studio", "theme_camp"}:
        return "org"
    if kind == "multiple":
        return "multiple"
    return "unknown"


def _map_credit_type(credit_type: str, credit: str, entities: list[str]) -> str:
    mapped = _map_contributor_kind(credit_type, credit)
    if credit_type == "multi_person" or (len(entities) > 1 and credit_type not in {"person", "person_with_playa_name", "collective", "hybrid"}):
        return "multiple"
    if credit_type == "organization":
        return "organization"
    if mapped == "individual" and credit_type == "multi_person":
        return "multiple"
    return mapped


def _split_named_people(value: str | None) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    parts = [clean_text(part) for part in text.replace("|", ";").split(";")]
    return [part for part in parts if part]


def _split_multi_entities(legal_name: str) -> list[str]:
    text = clean_text(legal_name)
    if not text:
        return []
    if ";" in text:
        return [clean_text(part) for part in text.split(";") if clean_text(part)]
    return [text]
