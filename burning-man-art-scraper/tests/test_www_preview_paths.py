import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.bm_ingest.view_bundle import (
    ensure_www_preview,
    list_prepared_years,
    resolve_preview_path,
)


class WwwPreviewPathTests(unittest.TestCase):
    def test_migrates_bm_ingest_view_into_www_previews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "scraper"
            root.mkdir()
            year = 2019
            ingest = root / "data" / "bm_ingest" / str(year)
            ingest.mkdir(parents=True)
            source = ingest / f"aggregator_view_{year}.json"
            source.write_text(json.dumps({"meta": {"year": year}, "projects": []}), encoding="utf-8")

            path = ensure_www_preview(root, year)
            self.assertIsNotNone(path)
            www = root.parent / "What When Where Files" / "aggregator_previews" / f"aggregator_view_{year}.json"
            self.assertEqual(path.resolve(), www.resolve())
            self.assertTrue(www.exists())
            self.assertEqual(resolve_preview_path(root, year).resolve(), www.resolve())
            self.assertEqual(list_prepared_years(root), [year])


if __name__ == "__main__":
    unittest.main()
