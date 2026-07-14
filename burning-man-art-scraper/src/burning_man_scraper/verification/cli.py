from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from burning_man_scraper.config import ScraperConfig, load_config
from burning_man_scraper.verification.processor import verify_year
from burning_man_scraper.verification.report import write_verification_report


OutputFunc = Callable[[str], None]


def default_www_dir(project_root: Path) -> Path:
    candidate = project_root.parent / "What When Where Files"
    return candidate


def default_export_path(project_root: Path, year: int) -> Path:
    return (
        project_root
        / "data"
        / "exports"
        / "burning_man"
        / str(year)
        / "consolidated"
        / f"burning_man_{year}_all_completed.json"
    )


def run_verification(
    *,
    config: ScraperConfig,
    year: int,
    www_dir: Path | None,
    export_path: Path | None,
    output_dir: Path,
    scope: str = "export",
    validate_images: bool = True,
    check_legacy_links: bool = False,
    output_func: OutputFunc = print,
) -> int:
    output_func(f"Building archive index for {year}...")
    results, archive_records = verify_year(
        year=year,
        user_agent=config.user_agent,
        www_dir=www_dir,
        export_path=export_path,
        scope=scope,
        validate_images=validate_images,
        check_legacy_links=check_legacy_links,
        request_timeout_seconds=config.request_timeout_seconds,
        image_delay_seconds=min(config.request_delay_seconds, 1.0),
    )

    paths = write_verification_report(
        output_dir,
        year=year,
        results=results,
        archive_records=archive_records,
    )

    output_func("")
    output_func("VERIFICATION COMPLETE")
    output_func(f"Archive records indexed: {len(archive_records)}")
    output_func(f"Projects verified: {len(results)}")
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.verification_status] = status_counts.get(result.verification_status, 0) + 1
    for status, count in sorted(status_counts.items()):
        output_func(f"  {status}: {count}")
    output_func("")
    output_func("Output files:")
    for label, path in paths.items():
        output_func(f"  {label}: {path}")
    return 0


def build_arg_parser(project_root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify Burning Man art projects against the official archive.")
    parser.add_argument("--year", type=int, default=2022, help="Archive year to verify.")
    parser.add_argument(
        "--scope",
        choices=("export", "www", "archive", "all"),
        default="export",
        help="Which project set to verify.",
    )
    parser.add_argument(
        "--www-dir",
        type=Path,
        default=default_www_dir(project_root),
        help="Directory containing PlayaEvents *_ART.csv files.",
    )
    parser.add_argument(
        "--export-path",
        type=Path,
        default=None,
        help="Consolidated export JSON to verify. Defaults to the standard export path for the year.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=project_root / "data" / "verification",
        help="Directory for verification reports.",
    )
    parser.add_argument(
        "--skip-image-validation",
        action="store_true",
        help="Skip live HTTP checks for image URLs.",
    )
    parser.add_argument(
        "--check-legacy-links",
        action="store_true",
        help="Check whether legacy WWW links still respond.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root / "config" / "default.yaml",
        help="Scraper config path.",
    )
    return parser


def main(argv: list[str] | None = None, project_root: Path | None = None) -> int:
    root = project_root or Path(__file__).resolve().parents[2]
    parser = build_arg_parser(root)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    export_path = args.export_path
    if export_path is None and args.scope == "export":
        export_path = default_export_path(root, args.year)

    return run_verification(
        config=config,
        year=args.year,
        www_dir=args.www_dir,
        export_path=export_path,
        output_dir=args.output_dir / str(args.year),
        scope=args.scope,
        validate_images=not args.skip_image_validation,
        check_legacy_links=args.check_legacy_links,
    )


if __name__ == "__main__":
    raise SystemExit(main())
