from __future__ import annotations

from dataclasses import asdict
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Protocol
from urllib.parse import urldefrag, urlsplit
from urllib.request import Request, urlopen
import urllib.robotparser
from uuid import uuid4

from burning_man_scraper.artelier_schema import ImportSchema, format_row_for_schema
from burning_man_scraper.enrichment.cache import SearchCache
from burning_man_scraper.enrichment.discovery import discover_first_party_results
from burning_man_scraper.enrichment.models import (
    BatchRecord,
    CandidateSource,
    EnrichmentPreview,
    ProposedEnrichment,
    SearchResult,
)
from burning_man_scraper.enrichment.providers import NoOpSearchProvider, SearchProvider


SOURCE_TYPE_PRIORITY = {
    "first_party": 1,
    "burning_man_official": 2,
    "institutional": 3,
    "public_art_archive": 4,
    "press": 5,
    "crowdfunding": 6,
    "social": 7,
    "other": 8,
}

ENRICHABLE_FIELDS = (
    "contributor_website",
    "project_tags",
    "project_materials",
    "project_fabrication_methods",
    "project_context_tags",
    "why_it_mattered",
    "what_they_did",
    "client_name",
    "collaboration_status",
    "contribution_category",
    "phase",
    "public_credit_language",
)


class FetchClient(Protocol):
    def fetch(self, url: str) -> str:
        ...


class CachedFetchClient:
    def __init__(self, cache_dir: Path, user_agent: str, delay_seconds: float = 1.0):
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.last_fetch_at = 0.0

    def fetch(self, url: str) -> str:
        url, _fragment = normalize_fetch_url(url)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.html"
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8", errors="replace")
        if not robots_allowed(url, self.user_agent):
            raise PermissionError(f"robots.txt disallows fetching {url}")
        elapsed = time.time() - self.last_fetch_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        request = Request(url, headers={"User-Agent": self.user_agent})
        with urlopen(request, timeout=20) as response:
            html = response.read().decode("utf-8", errors="replace")
        self.last_fetch_at = time.time()
        cache_path.write_text(html, encoding="utf-8")
        return html


def build_search_queries(record: BatchRecord) -> list[str]:
    title = quote_phrase(record.project_title)
    contributor_plain = primary_contributor_name(record.contributor_name)
    contributor = quote_phrase(contributor_plain)
    year = record.year or ""
    contributor_tokens = contributor_alias_tokens(record.contributor_name)
    domain = external_domain(record.artist_website or record.project_website)
    queries = [
        " ".join(part for part in (title, contributor) if part),
        f"{title} Burning Man",
        " ".join(part for part in (title, "Burning Man", year) if part),
        f"{title} materials",
        f"{title} fabrication",
        " ".join(part for part in (contributor, "Burning Man", year) if part),
    ]
    if contributor_plain:
        for alias in contributor_tokens:
            queries.append(" ".join(part for part in (quote_phrase(contributor_plain), alias, "Burning Man") if part))
        queries.append(f'{quote_phrase(contributor_plain)} kinetic installation')
        location = clean_text((record.original_values or {}).get("artist_location"))
        if location:
            queries.append(f"{quote_phrase(contributor_plain)} artist {location.split(',', 1)[0]}")
    if record.artist_collective:
        queries.append(f"{title} {quote_phrase(record.artist_collective)}")
    if record.materials:
        queries.append(f"{title} {record.materials}")
    if domain:
        queries.append(f"site:{domain} {title}")
    return ordered_unique([query for query in queries if query.strip()])


def build_enrichment_preview(
    record: BatchRecord,
    schema: ImportSchema,
    search_client: SearchProvider,
    fetch_client: FetchClient,
    preview_id: str | None = None,
    search_cache: SearchCache | None = None,
    refresh_cache: bool = False,
    provider_failures: list[str] | None = None,
) -> EnrichmentPreview:
    report: dict[str, object] = {
        "selected_search_provider": getattr(search_client, "name", "unknown"),
        "queries_attempted": [],
        "cached_searches": 0,
        "live_searches": 0,
        "known_urls_inspected": [],
        "sitemaps_inspected": [],
        "rss_feeds_inspected": [],
        "first_party_candidate_urls_discovered": [],
        "provider_failures": provider_failures or [],
        "completed_without_broad_web_search": False,
        "result_count": 0,
        "accepted_sources": [],
        "rejected_sources": [],
        "source_fetch_failures": [],
        "robots_blocked_sources": [],
        "source_fragments": {},
        "enrichment_outcome": "searching",
    }
    results = collect_search_results(record, search_client, fetch_client, search_cache, refresh_cache, report)
    if not report["queries_attempted"]:
        report["enrichment_outcome"] = "not_started_no_provider"
    report["result_count"] = len(results)
    sources = rank_and_fetch_sources(record, results, fetch_client, report)
    proposed = extract_proposals(record, sources, schema)
    if proposed:
        report["enrichment_outcome"] = "enriched" if len(proposed) >= len(unresolved_fields({}, proposed, schema)) else "partially_enriched"
    elif sources:
        report["enrichment_outcome"] = "sources_found"
    elif report["enrichment_outcome"] != "not_started_no_provider":
        report["enrichment_outcome"] = "no_credible_sources_found"
    row = build_enriched_row(record, proposed, schema)
    unresolved = unresolved_fields(row, proposed, schema)
    return EnrichmentPreview(
        preview_id=preview_id or str(uuid4()),
        batch_record=record,
        sources=sources,
        proposed_changes=proposed,
        unresolved_fields=unresolved,
        artelier_row=row,
        headers=schema.headers,
        search_provider=getattr(search_client, "name", "unknown"),
        search_report=report,
    )


def collect_search_results(
    record: BatchRecord,
    search_client: SearchProvider,
    fetch_client: FetchClient,
    search_cache: SearchCache | None = None,
    refresh_cache: bool = False,
    report: dict[str, object] | None = None,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    seen: set[str] = set()
    discovered, discovery_report = discover_first_party_results(record, fetch_client)
    if report is not None:
        report["known_urls_inspected"] = discovery_report.known_urls_inspected
        report["sitemaps_inspected"] = discovery_report.sitemaps_inspected
        report["rss_feeds_inspected"] = discovery_report.rss_feeds_inspected
        report["first_party_candidate_urls_discovered"] = discovery_report.first_party_candidate_urls_discovered
        report["provider_failures"] = list(report.get("provider_failures", [])) + discovery_report.failures
        report["base_sources_skipped"] = discovery_report.base_sources_skipped
        report["source_fragments"] = dict(report.get("source_fragments", {})) | discovery_report.source_fragments
    for result in discovered:
        fetch_url, fragment = normalize_fetch_url(result.url)
        if report is not None and fragment:
            fragments = dict(report.get("source_fragments", {}))
            fragments[fetch_url] = fragment
            report["source_fragments"] = fragments
        if fetch_url and fetch_url not in seen:
            seen.add(fetch_url)
            results.append(
                SearchResult(
                    title=result.title,
                    url=fetch_url,
                    snippet=result.snippet,
                    provider=result.provider,
                    published_date=result.published_date,
                    engine_metadata=result.engine_metadata,
                )
            )

    if isinstance(search_client, NoOpSearchProvider):
        if report is not None:
            report["completed_without_broad_web_search"] = True
        return results[:10]

    for query in build_search_queries(record):
        if report is not None:
            report["queries_attempted"] = list(report.get("queries_attempted", [])) + [query]
        if search_cache is not None:
            provider_results = search_cache.search(search_client, query, limit=10, refresh=refresh_cache)
            if report is not None:
                key = "cached_searches" if search_cache.last_cache_hit else "live_searches"
                report[key] = int(report.get(key, 0)) + 1
                if search_cache.last_error:
                    report["provider_failures"] = list(report.get("provider_failures", [])) + [search_cache.last_error]
        else:
            try:
                provider_results = search_client.search(query, limit=10)
                if report is not None:
                    report["live_searches"] = int(report.get("live_searches", 0)) + 1
            except Exception as exc:
                provider_results = []
                if report is not None:
                    report["provider_failures"] = list(report.get("provider_failures", [])) + [str(exc)]
        for result in provider_results:
            fetch_url, fragment = normalize_fetch_url(result.url)
            if report is not None and fragment:
                fragments = dict(report.get("source_fragments", {}))
                fragments[fetch_url] = fragment
                report["source_fragments"] = fragments
            if fetch_url and fetch_url not in seen:
                seen.add(fetch_url)
                results.append(
                    SearchResult(
                        title=result.title,
                        url=fetch_url,
                        snippet=result.snippet,
                        provider=result.provider,
                        published_date=result.published_date,
                        engine_metadata=result.engine_metadata,
                    )
                )
            if len(results) >= 10:
                return results
    return results[:10]


def rank_and_fetch_sources(
    record: BatchRecord,
    results: list[SearchResult],
    fetch_client: FetchClient,
    report: dict[str, object] | None = None,
) -> list[CandidateSource]:
    ranked_results = sorted(
        results,
        key=lambda result: (
            SOURCE_TYPE_PRIORITY[classify_source_type(result.url, record)],
            -snippet_score(result, record),
        ),
    )
    accepted: list[CandidateSource] = []
    for result in ranked_results:
        if len(accepted) >= 5:
            break
        try:
            html = fetch_client.fetch(result.url)
        except PermissionError as exc:
            if report is not None:
                report["robots_blocked_sources"] = list(report.get("robots_blocked_sources", [])) + [result.url]
                report["source_fetch_failures"] = list(report.get("source_fetch_failures", [])) + [f"{result.url}: {exc}"]
            continue
        except Exception as exc:
            if report is not None:
                report["source_fetch_failures"] = list(report.get("source_fetch_failures", [])) + [f"{result.url}: {exc}"]
            continue
        text = strip_html(html)
        identifiers = matching_identifiers(record, result.title, result.snippet, text)
        if len(identifiers) < 2:
            if report is not None:
                report["rejected_sources"] = list(report.get("rejected_sources", [])) + [
                    {"url": result.url, "reason": "fewer than two matching identifiers", "matching_identifiers": identifiers}
                ]
            continue
        source_type = classify_source_type(result.url, record)
        accepted.append(
            CandidateSource(
                title=clean_text(result.title) or extract_title(html) or result.url,
                url=result.url,
                source_type=source_type,
                relevance_score=source_relevance_score(source_type, identifiers),
                matching_identifiers=identifiers,
                excerpt=best_excerpt(text, record),
            )
        )
        if report is not None:
            report["accepted_sources"] = list(report.get("accepted_sources", [])) + [result.url]
    return sorted(accepted, key=lambda source: (-source.relevance_score, SOURCE_TYPE_PRIORITY[source.source_type]))


def extract_proposals(
    record: BatchRecord,
    sources: list[CandidateSource],
    schema: ImportSchema,
) -> list[ProposedEnrichment]:
    schema_fields = set(schema.headers)
    proposals: list[ProposedEnrichment] = []
    current = record.artelier_row or {}
    for source in sources:
        if "contributor_website" in schema_fields and source.source_type == "first_party":
            proposals.append(
                proposal(
                    "contributor_website",
                    current.get("contributor_website"),
                    source.url,
                    source,
                    "directly_stated",
                    0.9,
                    review_required=False,
                )
            )
        text = source.excerpt.lower()
        if "project_materials" in schema_fields and not current.get("project_materials"):
            material = material_from_text(source.excerpt)
            if material:
                proposals.append(proposal("project_materials", "", material, source, "directly_stated", 0.8, True))
        if "project_fabrication_methods" in schema_fields and not current.get("project_fabrication_methods"):
            fabrication = fabrication_from_text(source.excerpt)
            if fabrication:
                proposals.append(
                    proposal("project_fabrication_methods", "", fabrication, source, "directly_stated", 0.75, True)
                )
        if "project_tags" in schema_fields and not current.get("project_tags"):
            tags = tags_from_text(text)
            if tags:
                proposals.append(
                    proposal("project_tags", "", "||".join(tags), source, "strongly_inferred", 0.62, True)
                )
        if "what_they_did" in schema_fields and not current.get("what_they_did") and source.excerpt:
            proposals.append(proposal("what_they_did", "", source.excerpt, source, "directly_stated", 0.7, True))
        if "why_it_mattered" in schema_fields and not current.get("why_it_mattered") and any(
            word in text for word in ("community", "memorial", "interactive", "particip")
        ):
            proposals.append(proposal("why_it_mattered", "", source.excerpt, source, "strongly_inferred", 0.58, True))
    return dedupe_proposals(proposals)


def build_enriched_row(
    record: BatchRecord,
    proposals: list[ProposedEnrichment],
    schema: ImportSchema,
) -> dict[str, str]:
    row = {header: "" for header in schema.headers}
    for key, value in (record.artelier_row or {}).items():
        if key in row:
            row[key] = "" if value is None else str(value)
    for proposal_item in proposals:
        if proposal_item.artelier_field in row and proposal_item.proposed_value:
            row[proposal_item.artelier_field] = proposal_item.proposed_value
    return format_row_for_schema(row, schema)


def unresolved_fields(
    row: dict[str, str],
    proposals: list[ProposedEnrichment],
    schema: ImportSchema,
) -> dict[str, str]:
    proposed_fields = {item.artelier_field for item in proposals}
    unresolved: dict[str, str] = {}
    for field in ENRICHABLE_FIELDS:
        if field not in schema.headers:
            unresolved[field] = "unsupported Artelier field"
        elif not row.get(field) and field not in proposed_fields:
            unresolved[field] = "no credible source found"
    return unresolved


def proposal(
    field: str,
    original_value: object,
    proposed_value: str,
    source: CandidateSource,
    classification: str,
    confidence: float,
    review_required: bool,
) -> ProposedEnrichment:
    return ProposedEnrichment(
        artelier_field=field,
        original_value="" if original_value is None else str(original_value),
        proposed_value=proposed_value,
        source_url=source.url,
        source_title=source.title,
        source_type=source.source_type,
        source_excerpt=source.excerpt,
        confidence=confidence,
        evidence_classification=classification,
        review_required=review_required,
    )


def classify_source_type(url: str, record: BatchRecord) -> str:
    host = (urlsplit(url).hostname or "").lower()
    known_hosts = {urlsplit(url).hostname for url in (record.artist_website, record.project_website) if url}
    if host and host in known_hosts:
        return "first_party"
    if host.endswith("burningman.org"):
        return "burning_man_official"
    if "publicartarchive.org" in host:
        return "public_art_archive"
    if any(token in host for token in ("museum", "university", ".edu", ".org")):
        return "institutional"
    if any(token in host for token in ("kickstarter", "indiegogo", "gofundme")):
        return "crowdfunding"
    if any(token in host for token in ("instagram", "facebook", "twitter", "x.com", "youtube")):
        return "social"
    if any(token in host for token in ("news", "magazine", "journal", "blog")):
        return "press"
    return "other"


def matching_identifiers(record: BatchRecord, title: str, snippet: str, text: str) -> list[str]:
    haystack = f"{title} {snippet} {text}".lower()
    matches: list[str] = []
    if near_contains(haystack, record.project_title):
        matches.append("project title")
    if record.contributor_name and near_contains(haystack, record.contributor_name):
        matches.append("contributor name")
    if record.artist_collective and near_contains(haystack, record.artist_collective):
        matches.append("collective name")
    if record.year and record.year in haystack:
        matches.append("event year")
    if "burning man" in haystack or "black rock city" in haystack:
        matches.append("Burning Man reference")
    if record.materials and near_contains(haystack, record.materials):
        matches.append("materials")
    return ordered_unique(matches)


def near_contains(haystack: str, needle: str | None) -> bool:
    if not needle:
        return False
    normalized_needle = re.sub(r"[^a-z0-9]+", " ", needle.lower()).strip()
    normalized_haystack = re.sub(r"[^a-z0-9]+", " ", haystack.lower())
    if normalized_needle and normalized_needle in normalized_haystack:
        return True
    words = [word for word in normalized_needle.split() if len(word) > 2]
    if len(words) <= 1:
        return False
    return sum(1 for word in words if word in normalized_haystack) >= max(2, len(words) - 1)


def source_relevance_score(source_type: str, identifiers: list[str]) -> int:
    return (10 - SOURCE_TYPE_PRIORITY[source_type]) * 10 + len(identifiers) * 5


def snippet_score(result: SearchResult, record: BatchRecord) -> int:
    return len(matching_identifiers(record, result.title, result.snippet, ""))


def material_from_text(text: str) -> str | None:
    match = re.search(r"(?:materials?|made (?:from|of|with)|built (?:from|of|with))[:\s]+([^.;\n]{3,120})", text, re.I)
    return clean_text(match.group(1)) if match else None


def fabrication_from_text(text: str) -> str | None:
    match = re.search(r"(?:fabricat(?:ed|ion)|constructed|built)[:\s]+([^.;\n]{3,120})", text, re.I)
    return clean_text(match.group(1)) if match else None


def tags_from_text(text: str) -> list[str]:
    tags = []
    for word in ("interactive", "light", "sound", "temple", "kinetic", "community", "solar"):
        if word in text:
            tags.append(word)
    return tags[:5]


def dedupe_proposals(proposals: list[ProposedEnrichment]) -> list[ProposedEnrichment]:
    seen: set[str] = set()
    result: list[ProposedEnrichment] = []
    for item in proposals:
        if item.artelier_field in seen:
            continue
        seen.add(item.artelier_field)
        result.append(item)
    return result


def write_preview_files(preview: EnrichmentPreview, batch_dir: Path) -> tuple[Path, Path, Path, Path]:
    preview_dir = batch_dir / "enrichment_previews" / preview.preview_id
    preview_dir.mkdir(parents=True, exist_ok=True)
    json_path = preview_dir / "enrichment_preview.json"
    review_csv = preview_dir / "enrichment_review_preview.csv"
    row_csv = preview_dir / "enriched_artelier_row_preview.csv"
    report_md = preview_dir / "source_report.md"

    json_path.write_text(json.dumps(asdict(preview), indent=2, sort_keys=True), encoding="utf-8")
    write_review_csv(review_csv, preview)
    write_row_csv(row_csv, preview)
    report_md.write_text(source_report_markdown(preview), encoding="utf-8")
    return json_path, review_csv, row_csv, report_md


def write_review_csv(path: Path, preview: EnrichmentPreview) -> None:
    import csv

    fieldnames = [
        "artelier_field",
        "original_value",
        "proposed_value",
        "evidence_classification",
        "confidence",
        "source_url",
        "source_excerpt",
        "review_required",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in preview.proposed_changes:
            writer.writerow({field: getattr(item, field) for field in fieldnames})


def write_row_csv(path: Path, preview: EnrichmentPreview) -> None:
    import csv

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=preview.headers)
        writer.writeheader()
        writer.writerow(preview.artelier_row)


def source_report_markdown(preview: EnrichmentPreview) -> str:
    lines = [f"# Enrichment Source Report: {preview.batch_record.project_title}", ""]
    report = preview.search_report or {}
    lines.extend(
        [
            "## Search Summary",
            f"- Selected search provider: {report.get('selected_search_provider', preview.search_provider)}",
            f"- Queries attempted: {', '.join(report.get('queries_attempted', []) or []) or 'none'}",
            f"- Cached searches: {report.get('cached_searches', 0)}",
            f"- Live searches: {report.get('live_searches', 0)}",
            f"- Result count: {report.get('result_count', 0)}",
            f"- Accepted sources: {len(report.get('accepted_sources', []) or [])}",
            f"- Rejected sources: {len(report.get('rejected_sources', []) or [])}",
            f"- Known URLs inspected: {len(report.get('known_urls_inspected', []) or [])}",
            f"- Sitemaps inspected: {len(report.get('sitemaps_inspected', []) or [])}",
            f"- RSS feeds inspected: {len(report.get('rss_feeds_inspected', []) or [])}",
            f"- First-party candidate URLs discovered: {len(report.get('first_party_candidate_urls_discovered', []) or [])}",
            f"- Source fetch failures: {len(report.get('source_fetch_failures', []) or [])}",
            f"- Robots-blocked sources: {len(report.get('robots_blocked_sources', []) or [])}",
            f"- Enrichment outcome: {report.get('enrichment_outcome', '')}",
            f"- Completed without broad web search: {bool(report.get('completed_without_broad_web_search', False))}",
            "",
        ]
    )
    if report.get("rejected_sources"):
        lines.append("Rejected sources:")
        for item in report.get("rejected_sources", []):
            lines.append(f"- {item}")
        lines.append("")
    if report.get("source_fetch_failures"):
        lines.append("Source fetch failures:")
        for failure in report.get("source_fetch_failures", []):
            lines.append(f"- {failure}")
        lines.append("")
    if report.get("robots_blocked_sources"):
        lines.append("Robots-blocked sources:")
        for url in report.get("robots_blocked_sources", []):
            lines.append(f"- {url}")
        lines.append("")
    failures = report.get("provider_failures", []) or []
    if failures:
        lines.append("Provider failures or rate limiting:")
        for failure in failures:
            lines.append(f"- {failure}")
        lines.append("")
    for source in preview.sources:
        lines.extend(
            [
                f"## {source.title}",
                f"- URL: {source.url}",
                f"- Source type: {source.source_type}",
                f"- Relevance score: {source.relevance_score}",
                f"- Matching identifiers: {', '.join(source.matching_identifiers)}",
                "",
                source.excerpt,
                "",
            ]
        )
    return "\n".join(lines)


def best_excerpt(text: str, record: BatchRecord) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", clean_text(text))
    evidence_sentences = [
        sentence
        for sentence in sentences
        if re.search(r"\b(materials?|made from|made of|made with|fabricat|constructed|built)\b", sentence, re.I)
    ]
    for sentence in sentences:
        if matching_identifiers(record, "", "", sentence):
            excerpt = " ".join([sentence] + evidence_sentences)
            return excerpt[:600]
    return clean_text(text)[:600]


def extract_title(html: str) -> str | None:
    parser = _TitleParser()
    parser.feed(html)
    return clean_text(" ".join(parser.parts)) or None


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str):
        if self.in_title:
            self.parts.append(data)


def strip_html(value: str) -> str:
    return clean_text(re.sub(r"<[^>]+>", " ", value))


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def quote_phrase(value: str | None) -> str:
    value = clean_text(value)
    return f'"{value}"' if value else ""


def quote_query(value: str) -> str:
    from urllib.parse import quote

    return quote(value)


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def robots_allowed(url: str, user_agent: str) -> bool:
    fetch_url, _fragment = normalize_fetch_url(url)
    parsed = urlsplit(fetch_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
    except Exception:
        return True
    return parser.can_fetch(user_agent, fetch_url)


def normalize_fetch_url(url: str) -> tuple[str, str | None]:
    base_url, fragment = urldefrag(url)
    return base_url, fragment or None


def contributor_alias_tokens(value: str | None) -> list[str]:
    text = clean_text(value)
    aliases: list[str] = []
    aka_match = re.search(r"\ba\.?k\.?a\.?\s+([^,;()]+)", text, re.I)
    if aka_match:
        aliases.append(clean_text(aka_match.group(1)))
    return aliases


def primary_contributor_name(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    text = re.split(r"\ba\.?k\.?a\.?\b", text, maxsplit=1, flags=re.I)[0]
    text = text.split(" - ", 1)[0]
    return clean_text(text)


def external_domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlsplit(url).hostname
    if not host or host.endswith("history.burningman.org"):
        return None
    return host[4:] if host.startswith("www.") else host
