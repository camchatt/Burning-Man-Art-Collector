from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from burning_man_scraper.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
