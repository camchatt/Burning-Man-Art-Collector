from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from burning_man_scraper.bm_ingest.cli import main


if __name__ == "__main__":
    raise SystemExit(main(project_root=PROJECT_ROOT))
