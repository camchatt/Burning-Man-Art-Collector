from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from burning_man_scraper.verification.archive_index import (
    build_archive_index,
    extract_uid,
    index_archive_by_title,
    index_archive_by_uid,
    resolve_archive_record,
)
from burning_man_scraper.verification.image_validator import ImageValidator
from burning_man_scraper.verification.models import (
    ArchiveIndexRecord,
    ImageAsset,
    VerificationResult,
    WwwReferenceRecord,
)
from burning_man_scraper.verification.text_match import artist_similarity, similarity_score
from burning_man_scraper.verification.www_loader import (
    index_www_by_title,
    index_www_by_uid,
    load_www_records,
)


TITLE_MATCH_THRESHOLD = 0.75
DESCRIPTION_MATCH_THRESHOLD = 0.35


def load_export_records(export_path: Path) -> list[dict]:
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON array in {export_path}")
    return payload


def verify_year(
    *,
    year: int,
    user_agent: str,
    www_dir: Path | None = None,
    export_path: Path | None = None,
    scope: str = "export",
    validate_images: bool = True,
    check_legacy_links: bool = False,
    request_timeout_seconds: float = 30.0,
    image_delay_seconds: float = 0.5,
) -> tuple[list[VerificationResult], list[ArchiveIndexRecord]]:
    archive_records = build_archive_index(
        year,
        user_agent=user_agent,
        timeout_seconds=request_timeout_seconds,
    )
    archive_by_uid = index_archive_by_uid(archive_records)
    archive_by_title = index_archive_by_title(archive_records)

    www_records: list[WwwReferenceRecord] = []
    if www_dir:
        www_records = load_www_records(www_dir, year=year)
    www_by_uid = index_www_by_uid(www_records)
    www_by_title = index_www_by_title(www_records)

    targets = _select_targets(
        scope=scope,
        year=year,
        export_path=export_path,
        archive_records=archive_records,
        www_records=www_records,
    )

    image_validator = ImageValidator(
        user_agent=user_agent,
        timeout_seconds=request_timeout_seconds,
        delay_seconds=image_delay_seconds,
    )

    results: list[VerificationResult] = []
    for target in targets:
        archive = resolve_archive_record(
            uid=target.get("uid"),
            title=target.get("title"),
            by_uid=archive_by_uid,
            by_title=archive_by_title,
        )
        www = None
        if target.get("uid") and target["uid"] in www_by_uid:
            www = www_by_uid[target["uid"]]
        elif target.get("title"):
            from burning_man_scraper.record_parser import normalize_title

            normalized = normalize_title(target["title"])
            www = www_by_title.get(normalized)

        result = _verify_target(
            year=year,
            target=target,
            archive=archive,
            www=www,
            image_validator=image_validator if validate_images else None,
            check_legacy_links=check_legacy_links,
            user_agent=user_agent,
            timeout_seconds=request_timeout_seconds,
        )
        results.append(result)

    results.sort(key=lambda item: item.project_title.lower())
    return results, archive_records


def _select_targets(
    *,
    scope: str,
    year: int,
    export_path: Path | None,
    archive_records: list[ArchiveIndexRecord],
    www_records: list[WwwReferenceRecord],
) -> list[dict]:
    if scope == "export":
        if export_path is None:
            raise ValueError("export_path is required when scope=export")
        return [_target_from_export(item) for item in load_export_records(export_path)]
    if scope == "www":
        return [_target_from_www(item) for item in www_records]
    if scope == "archive":
        return [_target_from_archive(item) for item in archive_records]
    if scope == "all":
        merged: dict[str, dict] = {}
        for record in archive_records:
            key = record.uid or record.normalized_title
            merged[key] = _target_from_archive(record)
        for record in www_records:
            key = record.uid or record.normalized_title
            merged.setdefault(key, _target_from_www(record))
        return list(merged.values())
    raise ValueError(f"Unsupported scope: {scope}")


def _target_from_export(item: dict) -> dict:
    mapped = item.get("mapped_artelier_values") or {}
    original = item.get("original_scraped_values") or {}
    proof_url = mapped.get("proof_external_url") or original.get("canonical_source_url")
    return {
        "source": "export",
        "title": mapped.get("project_title") or original.get("title"),
        "artist": mapped.get("contributor_name") or original.get("artist_display_text"),
        "description": mapped.get("project_summary") or original.get("description"),
        "uid": extract_uid(proof_url),
        "archive_url": proof_url,
        "image_urls": list(original.get("image_urls") or []),
        "image_alt_text": original.get("image_alt_text"),
    }


def _target_from_www(record: WwwReferenceRecord) -> dict:
    return {
        "source": "www",
        "title": record.title,
        "artist": None,
        "description": record.description,
        "uid": record.uid,
        "legacy_link": record.legacy_link,
        "artist_url": record.artist_url,
    }


def _target_from_archive(record: ArchiveIndexRecord) -> dict:
    return {
        "source": "archive",
        "title": record.title,
        "artist": record.artist_display_text,
        "description": record.description,
        "uid": record.uid,
        "archive_url": record.canonical_source_url,
        "image_urls": list(record.image_urls),
        "image_alt_text": record.image_alt_text,
    }


def _verify_target(
    *,
    year: int,
    target: dict,
    archive: ArchiveIndexRecord | None,
    www: WwwReferenceRecord | None,
    image_validator: ImageValidator | None,
    check_legacy_links: bool,
    user_agent: str,
    timeout_seconds: float,
) -> VerificationResult:
    title = target.get("title") or ""
    warnings: list[str] = []
    if archive is None:
        return VerificationResult(
            year=year,
            project_title=title,
            normalized_title=title,
            verification_status="unresolved",
            www_title=www.title if www else None,
            www_uid=www.uid if www else None,
            export_artist=target.get("artist"),
            warnings=["No matching archive record was found."],
            source=str(target.get("source") or "unknown"),
        )

    title_score = similarity_score(title, archive.title)
    artist_score = artist_similarity(target.get("artist"), archive.artist_display_text)
    description_score = similarity_score(target.get("description"), archive.description)
    uid_match = None
    if target.get("uid") or archive.uid:
        uid_match = bool(target.get("uid") and archive.uid and target.get("uid") == archive.uid)

    if title_score < TITLE_MATCH_THRESHOLD:
        warnings.append(f"Title match score below threshold ({title_score:.2f}).")
    if target.get("artist") and artist_score < 0.4:
        warnings.append(f"Artist match score is low ({artist_score:.2f}).")
    if target.get("description") and description_score < DESCRIPTION_MATCH_THRESHOLD:
        warnings.append(f"Description match score is low ({description_score:.2f}).")
    if uid_match is False:
        warnings.append("UID mismatch between target and archive record.")

    legacy_status = None
    if check_legacy_links and www and www.legacy_link:
        legacy_status = check_url_status(www.legacy_link, user_agent, timeout_seconds)
        if legacy_status != "active":
            warnings.append(f"Legacy WWW link status: {legacy_status}.")

    image_urls = _collect_image_urls(target, archive)
    images: list[ImageAsset] = []
    if image_validator is not None:
        for image_url in image_urls:
            images.append(
                image_validator.validate(
                    image_url,
                    source_page_url=archive.canonical_source_url,
                    alt_text=archive.image_alt_text or target.get("image_alt_text"),
                )
            )
    else:
        for image_url in image_urls:
            images.append(ImageAsset(image_url=image_url, source_page_url=archive.canonical_source_url))

    active_images = [image for image in images if image.link_active]
    hero = active_images[0] if active_images else (images[0] if images else None)
    if hero and hero.review_required:
        warnings.append("Hero image attribution requires review.")
    status = _determine_status(
        title_score=title_score,
        uid_match=uid_match,
        warnings=warnings,
        active_image_count=len(active_images),
        image_count=len(images),
        hero_requires_review=bool(hero and hero.review_required),
    )

    return VerificationResult(
        year=year,
        project_title=archive.title,
        normalized_title=archive.normalized_title,
        verification_status=status,
        archive_uid=archive.uid,
        archive_url=archive.canonical_source_url,
        www_title=www.title if www else None,
        www_uid=www.uid if www else None,
        legacy_link_status=legacy_status,
        title_match_score=title_score,
        artist_match_score=artist_score if target.get("artist") else None,
        description_match_score=description_score if target.get("description") else None,
        uid_match=uid_match,
        archive_artist=archive.artist_display_text,
        export_artist=target.get("artist"),
        image_count=len(images),
        active_image_count=len(active_images),
        hero_image_url=hero.image_url if hero else None,
        hero_image_active=hero.link_active if hero else None,
        public_credit_language=hero.credit_text if hero else None,
        warnings=warnings,
        images=images,
        source=str(target.get("source") or "unknown"),
    )


def _collect_image_urls(target: dict, archive: ArchiveIndexRecord) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in [*archive.image_urls, *target.get("image_urls", [])]:
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _determine_status(
    *,
    title_score: float,
    uid_match: bool | None,
    warnings: list[str],
    active_image_count: int,
    image_count: int,
    hero_requires_review: bool = False,
) -> str:
    if title_score < 0.5 and uid_match is not True:
        return "conflict"
    if active_image_count == 0 and image_count > 0:
        return "broken_link"
    if warnings and (uid_match is False or title_score < TITLE_MATCH_THRESHOLD):
        return "likely_match"
    if active_image_count > 0 and title_score >= TITLE_MATCH_THRESHOLD:
        if hero_requires_review:
            return "verified"
        return "verified_online"
    if title_score >= TITLE_MATCH_THRESHOLD:
        return "verified"
    return "likely_match"


def check_url_status(url: str, user_agent: str, timeout_seconds: float) -> str:
    try:
        request = Request(url, method="HEAD", headers={"User-Agent": user_agent})
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status < 400:
                return "active"
            return f"http_{response.status}"
    except HTTPError as exc:
        if exc.code in {405, 501}:
            return check_url_status_get(url, user_agent, timeout_seconds)
        return f"http_{exc.code}"
    except URLError:
        return "unreachable"


def check_url_status_get(url: str, user_agent: str, timeout_seconds: float) -> str:
    try:
        request = Request(url, headers={"User-Agent": user_agent})
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status < 400:
                return "active"
            return f"http_{response.status}"
    except HTTPError as exc:
        return f"http_{exc.code}"
    except URLError:
        return "unreachable"


def serialize_image_assets(images: list[ImageAsset]) -> list[dict]:
    return [asdict(image) for image in images]
