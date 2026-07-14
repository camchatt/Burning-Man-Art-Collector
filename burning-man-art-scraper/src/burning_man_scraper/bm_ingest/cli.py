from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from burning_man_scraper.bm_ingest.merge import run_ingest
from burning_man_scraper.bm_ingest.sources import default_www_dir
from burning_man_scraper.config import load_config


OutputFunc = Callable[[str], None]


def run_bm_ingest_cli(
    *,
    project_root: Path,
    year: int,
    www_dir: Path | None = None,
    fetch_missing_heroes: bool = False,
    output_func: OutputFunc = print,
) -> int:
    config = load_config(project_root / "config" / "default.yaml")
    resolved_www = www_dir or default_www_dir(project_root)
    output_func(f"BM ingest year={year}")
    output_func(f"WWW dir: {resolved_www}")
    output_func(f"Network hero fetch: {'ON' if fetch_missing_heroes else 'OFF (default)'}")

    paths = run_ingest(
        project_root=project_root,
        year=year,
        www_dir=resolved_www,
        fetch_missing_heroes=fetch_missing_heroes,
        user_agent=config.user_agent,
    )
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    output_func("")
    output_func("BM INGEST COMPLETE")
    output_func(f"Projects: {summary['project_count']}")
    output_func(f"With hero image: {summary['with_hero_image']}")
    output_func(f"Review queue: {summary['review_queue_count']}")
    for flag, count in (summary.get("review_flag_counts") or {}).items():
        output_func(f"  flag {flag}: {count}")
    output_func("")
    output_func("Outputs:")
    for label, path in paths.items():
        output_func(f"  {label}: {path}")
    if "viewer_view" in paths:
        output_func("")
        output_func("Pre-upload previewer: viewer/aggregator/ (serve repo root, then open /viewer/aggregator/)")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build burningman.artelier upload CSV from WWW + cached verification/exports (offline by default)."
    )
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--www-dir", type=Path, default=None)
    parser.add_argument(
        "--fetch-missing-heroes",
        action="store_true",
        help="Optionally probe proof/artist pages for Open Graph images when cache has no hero.",
    )
    return parser


def main(argv: list[str] | None = None, project_root: Path | None = None) -> int:
    root = project_root or Path(__file__).resolve().parents[3]
    args = build_arg_parser().parse_args(argv)
    return run_bm_ingest_cli(
        project_root=root,
        year=args.year,
        www_dir=args.www_dir,
        fetch_missing_heroes=args.fetch_missing_heroes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
