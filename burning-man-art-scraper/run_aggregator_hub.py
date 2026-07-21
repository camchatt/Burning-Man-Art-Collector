from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from burning_man_scraper.aggregator_hub.config import load_deploy_config
from burning_man_scraper.aggregator_hub.server import serve
from burning_man_scraper.aggregator_hub.services import cleanup_temps, disk_footprint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Artelier Aggregator hub (source → prepare → review → export).")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--cleanup", action="store_true", help="Clean temp uploads and old preview HTML, then exit.")
    args = parser.parse_args(argv)

    deploy_cfg = load_deploy_config(PROJECT_ROOT)
    if args.cleanup:
        result = cleanup_temps(
            PROJECT_ROOT,
            preview_max_age_days=int(deploy_cfg.get("preview_html_max_age_days") or 14),
        )
        print(json_dumps(result))
        print("Disk:", disk_footprint(PROJECT_ROOT))
        return 0

    port = int(args.port or deploy_cfg.get("hub_port") or 8765)
    url = f"http://127.0.0.1:{port}/"
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    serve(PROJECT_ROOT, port=port)
    return 0


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
