from __future__ import annotations

import argparse
import json
from pathlib import Path

from burning_man_scraper.exporters.artelier_scraper_csv_v1.write import export_year_to_scraper_v1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export collector BM upload CSV rows to Artelier scraper CSV v1. "
            "Does not write to Artelier databases."
        )
    )
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument(
        "--source-csv",
        type=Path,
        default=None,
        help="Optional path to artelier_bm_upload_YEAR.csv (defaults to data/bm_ingest/<year>/).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory (defaults to timestamped data/exports/artelier_scraper_csv_v1/<year>/).",
    )
    return parser


def main(argv: list[str] | None = None, *, project_root: Path | None = None) -> int:
    # parents: .../artelier_scraper_csv_v1 -> exporters -> burning_man_scraper -> src -> project root
    root = project_root or Path(__file__).resolve().parents[4]
    args = build_arg_parser().parse_args(argv)
    summary = export_year_to_scraper_v1(
        root,
        year=args.year,
        source_csv=args.source_csv,
        output_root=args.output_dir,
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "compatibility"}, indent=2))
    print(f"\nWrote Artelier scraper v1 export to: {summary['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
