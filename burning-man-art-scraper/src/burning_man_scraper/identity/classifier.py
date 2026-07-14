from __future__ import annotations

from dataclasses import dataclass, field
import re


AKA_PATTERN = re.compile(
    r"^(?P<left>.+?)(?P<aka_sep>,?\s+)(?:a\.?k\.?a\.?|also known as)\s+(?P<right>.+)$",
    re.IGNORECASE,
)
PAREN_NICK_PATTERN = re.compile(
    r"^(?P<before>.*?)\((?P<nick>[^)]+)\)(?P<after>.*)$"
)
COLLECTIVE_HINTS = (
    "collective",
    " crew",
    " team",
    "camp ",
    " studio",
    " arts",
    " project",
    " guild",
    " co.",
    " company",
    " monkeys",
    " founders",
)
ROLE_PAREN_TOKENS = {
    "design",
    "build",
    "engineering",
    "engineer",
    "sound",
    "lights",
    "light",
    "fabricator",
    "fabrication",
    "producer",
    "lead",
    "artist",
    "optional",
    "final",
}


@dataclass(frozen=True)
class CreditClassification:
    archive_credit: str
    credit_type: str
    legal_name: str | None = None
    playa_name: str | None = None
    collective_name: str | None = None
    named_people: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    needs_identity_search: bool = False
    playa_name_confidence: str = "none"
    notes: list[str] = field(default_factory=list)


def classify_archive_credit(value: str | None) -> CreditClassification:
    credit = clean_text(value)
    if not credit:
        return CreditClassification(archive_credit="", credit_type="empty")

    aka = _split_aka(credit)
    if aka is not None:
        return aka

    paren = _split_parenthetical_nickname(credit)
    if paren is not None:
        return paren

    credited = _split_credited_people(credit)
    if credited is not None:
        return credited

    lowered = f" {credit.lower()} "
    if any(hint in lowered for hint in COLLECTIVE_HINTS):
        people = _split_people(credit)
        return CreditClassification(
            archive_credit=credit,
            credit_type="collective" if not people or _looks_like_collective(credit) else "hybrid",
            collective_name=credit if _looks_like_collective(credit) else None,
            named_people=people,
            needs_identity_search=True,
            notes=["Collective/group credit; search for members."],
        )

    people = _split_people(credit)
    if len(people) > 1:
        return CreditClassification(
            archive_credit=credit,
            credit_type="multi_person",
            named_people=people,
            legal_name="; ".join(people),
            needs_identity_search=False,
            notes=["Multiple named people already present in archive credit."],
        )

    if _looks_like_legal_name(credit):
        return CreditClassification(
            archive_credit=credit,
            credit_type="person",
            legal_name=credit,
            named_people=[credit],
            needs_identity_search=False,
        )

    return CreditClassification(
        archive_credit=credit,
        credit_type="alias_or_unknown",
        playa_name=credit if _looks_like_playa_name(credit) else None,
        playa_name_confidence="low" if _looks_like_playa_name(credit) else "none",
        named_people=[credit],
        needs_identity_search=True,
        notes=["Archive credit may be a playa name or brand; search for legal identity."],
    )


def _split_credited_people(credit: str) -> CreditClassification | None:
    """Pull legal names out of credits like 'X with engineering by Y' or 'Brand art by A and B'."""
    patterns = [
        # "Benjamin Langholz with engineering by Amihay Gonen"
        (
            re.compile(
                r"^(?P<head>.+?)\s+with\s+(?:engineering|design|fabrication|build|sound|lights?)\s+by\s+(?P<tail>.+)$",
                re.I,
            ),
            "both",
        ),
        # "Something art by Carson West and Tucker Roberts"
        (re.compile(r"^(?P<head>.+?)\s+art by\s+(?P<tail>.+)$", re.I), "tail"),
        # "unbound (christina and anna de quero)" when paren is people, not a nickname
        (re.compile(r"^(?P<head>[^(]+)\(\s*(?P<tail>[^)]+)\s*\)\s*$", re.I), "tail"),
    ]
    for pattern, source in patterns:
        match = pattern.match(credit)
        if not match:
            continue
        head = clean_text(match.group("head"))
        tail = clean_text(match.group("tail"))
        if not tail:
            continue
        # Parenthetical people: require "and"/"&"/"," so we don't steal nicknames.
        if source == "tail" and pattern.pattern.startswith("^(?P<head>[^(]+)") and not re.search(
            r"\b(?:and|&|,|/)\b", tail, re.I
        ):
            continue
        if source == "both":
            raw_parts = _split_people(head) + _split_people(_title_name(tail))
        else:
            raw_parts = _split_people(_title_name(tail))
            if not raw_parts:
                # Single-token first names in lowercase credits.
                raw_parts = [
                    _title_name(part)
                    for part in re.split(r"\s*(?:,|/|&|;|\band\b)\s*", tail, flags=re.I)
                    if clean_text(part)
                ]
        people = ordered_unique([_title_name(part) for part in raw_parts if part])
        legal_people = [
            person
            for person in people
            if _looks_like_legal_name(person)
            or (
                len(person.split()) == 1
                and person[:1].isupper()
                and person[1:].islower()
                and person.isalpha()
            )
        ]
        legal_people = [person for person in legal_people if not _looks_like_collective(person)]
        if not legal_people:
            continue
        brand = head if head and not _looks_like_legal_name(head) else None
        return CreditClassification(
            archive_credit=credit,
            credit_type="multi_person" if len(legal_people) > 1 else "person",
            legal_name="; ".join(legal_people),
            playa_name=brand if brand and _looks_like_playa_name(brand) else None,
            playa_name_confidence="medium" if brand and _looks_like_playa_name(brand) else "none",
            named_people=legal_people,
            aliases=[brand] if brand else [],
            needs_identity_search=False,
            notes=["Extracted named people from credit/role phrasing."],
        )
    return None


def _title_name(value: str) -> str:
    text = clean_text(value)
    if not text:
        return ""
    # Preserve mixed/all-caps brands; title-case lowercase name phrases.
    if text != text.lower():
        return text
    particles = {"de", "da", "di", "von", "van", "la", "le"}
    parts = []
    for part in text.split():
        if part in particles:
            parts.append(part)
        else:
            parts.append(part.capitalize())
    return " ".join(parts)


def _split_aka(credit: str) -> CreditClassification | None:
    match = AKA_PATTERN.match(credit)
    if not match:
        return None
    left = clean_text(match.group("left").rstrip(","))
    right = clean_text(match.group("right"))
    aka_sep = match.group("aka_sep") or ""
    if not left or not right:
        return None

    # Only split into playa/legal when one side clearly looks legal and the other does not.
    left_legal = _looks_like_legal_name(left)
    right_legal = _looks_like_legal_name(right)
    left_playa = _looks_like_playa_name(left)
    right_playa = _looks_like_playa_name(right)
    comma_aka = "," in aka_sep

    if left_legal and not right_legal:
        legal, playa = left, right
    elif right_legal and not left_legal:
        legal, playa = right, left
    elif comma_aka and left_legal:
        # "Sarah Gonsalves, aka Sassy Galaxy" — comma+aka is a reliable legal→playa cue.
        legal, playa = left, right
    elif left_legal and right_playa:
        legal, playa = left, right
    elif right_legal and left_playa:
        legal, playa = right, left
    else:
        # Ambiguous aka pair — keep as aliases, do not invent a playa column value.
        return CreditClassification(
            archive_credit=credit,
            credit_type="alias_pair",
            aliases=[left, right],
            named_people=_split_people(f"{left}; {right}"),
            needs_identity_search=True,
            playa_name_confidence="none",
            notes=["aka present but sides were ambiguous; playa_name left blank."],
        )

    people = _split_people(legal)
    return CreditClassification(
        archive_credit=credit,
        credit_type="person_with_playa_name",
        legal_name=legal,
        playa_name=playa,
        named_people=people or [legal],
        aliases=[playa],
        needs_identity_search=_looks_like_collective(legal) or len(people) > 3,
        playa_name_confidence="high",
        notes=["Separated via explicit aka/a.k.a. pattern."],
    )


def _split_parenthetical_nickname(credit: str) -> CreditClassification | None:
    match = PAREN_NICK_PATTERN.match(credit)
    if not match:
        return None
    nick = clean_text(match.group("nick"))
    before = clean_text(match.group("before"))
    after = clean_text(match.group("after"))
    if not nick or not _is_reliable_parenthetical_nickname(nick):
        return None
    legal = clean_text(f"{before} {after}")
    if not legal or not _looks_like_legal_name(legal):
        return None
    return CreditClassification(
        archive_credit=credit,
        credit_type="person_with_playa_name",
        legal_name=legal,
        playa_name=nick,
        named_people=[legal],
        aliases=[nick],
        needs_identity_search=False,
        playa_name_confidence="high",
        notes=["Separated via parenthetical nickname."],
    )


def _is_reliable_parenthetical_nickname(value: str) -> bool:
    lowered = value.lower()
    if any(token in lowered for token in ("+", "/", "&", " and ")):
        return False
    tokens = [token for token in re.split(r"\s+", lowered) if token]
    if not tokens or len(tokens) > 3:
        return False
    if any(token in ROLE_PAREN_TOKENS for token in tokens):
        return False
    if re.search(r"\d", value):
        return False
    return True


def _looks_like_legal_name(value: str) -> bool:
    text = clean_text(value)
    if not text:
        return False
    if _looks_like_collective(text):
        return False
    # Strip honorifics for token count.
    stripped = re.sub(r"^(mr|mrs|ms|dr)\.?\s+", "", text, flags=re.I)
    tokens = [token for token in re.split(r"\s+", stripped) if token]
    if len(tokens) < 2:
        return False
    # Reject if every token is lowercase stylized single brand-like.
    alpha_tokens = [re.sub(r"[^A-Za-z]", "", token) for token in tokens]
    alpha_tokens = [token for token in alpha_tokens if token]
    if not alpha_tokens:
        return False
    capitalish = sum(1 for token in alpha_tokens if token[:1].isupper())
    return capitalish >= max(1, len(alpha_tokens) // 2) and len(alpha_tokens) <= 6


def _looks_like_playa_name(value: str) -> bool:
    text = clean_text(value)
    if not text or _looks_like_collective(text):
        return False
    tokens = [token for token in re.split(r"\s+", text) if token]
    if len(tokens) == 1:
        return True
    if len(tokens) <= 3 and not _looks_like_legal_name(text):
        # Stylized multi-word playa names like "Sassy Galaxy".
        return True
    return False


def _looks_like_collective(value: str) -> bool:
    lowered = f" {value.lower()} "
    return any(hint in lowered for hint in COLLECTIVE_HINTS)


def _split_people(value: str) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    # Keep "and the X Crew/Collective" as a unit when present.
    text = re.sub(r"\s+and the\s+", " & the ", text, flags=re.I)
    parts = re.split(r"\s*(?:,|/|&|;|\band\b)\s*", text, flags=re.I)
    people: list[str] = []
    for part in parts:
        cleaned = clean_text(part)
        if not cleaned:
            continue
        if cleaned.lower().startswith("the ") and _looks_like_collective(cleaned):
            continue
        if _looks_like_legal_name(cleaned) or (len(cleaned.split()) == 1 and cleaned[:1].isupper()):
            people.append(cleaned)
    return ordered_unique(people)


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()
