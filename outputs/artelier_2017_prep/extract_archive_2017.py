from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(r"C:\Users\camch\OneDrive\Documents\Burning Man Art Collector")
SCRAPER_SRC = ROOT / "burning-man-art-scraper" / "src"
sys.path.insert(0, str(SCRAPER_SRC))

from burning_man_scraper.inline_archive import extract_inline_archive_records


ARCHIVE_URL = "https://history.burningman.org/art-history/archive/?yyyy=2017"
HTML_PATH = ROOT / "outputs" / "artelier_2017_prep" / "archive_2017_live.html"
OUT_PATH = ROOT / "outputs" / "artelier_2017_prep" / "archive_2017_records.json"


records = extract_inline_archive_records(
    HTML_PATH.read_text(encoding="utf-8", errors="replace"),
    archive_url=ARCHIVE_URL,
    final_url=ARCHIVE_URL,
)

OUT_PATH.write_text(
    json.dumps([record.__dict__ for record in records], indent=2, ensure_ascii=False),
    encoding="utf-8",
)
print(json.dumps({"records": len(records), "output": str(OUT_PATH)}, indent=2))
