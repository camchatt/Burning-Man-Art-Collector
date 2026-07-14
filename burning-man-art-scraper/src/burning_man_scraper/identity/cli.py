from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from burning_man_scraper.config import load_config
from burning_man_scraper.enrichment.providers import select_search_provider
from burning_man_scraper.identity.processor import (
    load_identity_results,
    resolve_identities_from_verification,
    write_identity_report,
)
from burning_man_scraper.verification.cli import default_www_dir


OutputFunc = Callable[[str], None]


def run_identity_resolution(
    *,
    project_root: Path,
    year: int,
    enable_search: bool = True,
    enable_page_fetch: bool = True,
    limit: int | None = None,
    only_needing_search: bool = False,
    aliases_only: bool = False,
    www_dir: Path | None = None,
    output_func: OutputFunc = print,
) -> int:
    config = load_config(project_root / "config" / "default.yaml")
    verification_csv = project_root / "data" / "verification" / str(year) / f"verification_report_{year}.csv"
    archive_index = project_root / "data" / "verification" / str(year) / f"archive_index_{year}.json"
    if not verification_csv.exists():
        output_func(f"Verification report not found: {verification_csv}")
        output_func("Run `python run_verify.py --year {year} --scope www` first.")
        return 1

    search_client = None
    if enable_search:
        search_client, provider_log = select_search_provider(user_agent=config.user_agent)
        output_func(f"Search provider: {search_client.name}")
        for failure in provider_log.failures:
            output_func(f"  provider note: {failure}")
    else:
        output_func("Search provider: disabled")

    output_func(f"Resolving identities for {year}...")
    existing_path = project_root / "data" / "verification" / str(year) / f"identity_report_{year}.json"
    existing = load_identity_results(existing_path) if (only_needing_search or aliases_only) else None
    results = resolve_identities_from_verification(
        year=year,
        verification_csv=verification_csv,
        archive_index_json=archive_index,
        www_dir=www_dir or default_www_dir(project_root),
        search_client=search_client,
        enable_search=enable_search,
        enable_page_fetch=enable_page_fetch,
        only_needing_search=only_needing_search,
        aliases_only=aliases_only,
        limit=limit,
        user_agent=config.user_agent,
        progress_func=output_func,
        existing_results=existing,
    )
    output_dir = project_root / "data" / "verification" / str(year)
    paths = write_identity_report(output_dir, year=year, results=results)

    playa_count = sum(1 for result in results if result.playa_name)
    output_func("")
    output_func("IDENTITY RESOLUTION COMPLETE")
    output_func(f"Projects processed: {len(results)}")
    output_func(f"Playa names separated: {playa_count}")
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.identity_status] = status_counts.get(result.identity_status, 0) + 1
    for status, count in sorted(status_counts.items()):
        output_func(f"  {status}: {count}")
    output_func("")
    output_func("Output files:")
    for label, path in paths.items():
        output_func(f"  {label}: {path}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve artist identities and separate playa names when reliable.")
    parser.add_argument("--year", type=int, default=2022)
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on projects processed.")
    parser.add_argument("--skip-search", action="store_true", help="Classify only; do not search the web.")
    parser.add_argument("--skip-page-fetch", action="store_true", help="Do not fetch evidence pages.")
    parser.add_argument(
        "--only-needing-search",
        action="store_true",
        help="Only process credits that need identity search (collectives/aliases).",
    )
    parser.add_argument(
        "--aliases-only",
        action="store_true",
        help="Only resolve playa-name/alias credits to real names.",
    )
    return parser


def main(argv: list[str] | None = None, project_root: Path | None = None) -> int:
    root = project_root or Path(__file__).resolve().parents[2]
    args = build_arg_parser().parse_args(argv)
    return run_identity_resolution(
        project_root=root,
        year=args.year,
        enable_search=not args.skip_search,
        enable_page_fetch=(not args.skip_page_fetch) and (not args.skip_search),
        limit=args.limit,
        only_needing_search=args.only_needing_search,
        aliases_only=args.aliases_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
