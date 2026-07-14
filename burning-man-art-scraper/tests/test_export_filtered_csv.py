import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.aggregator_hub.services import export_filtered_csv


class ExportFilteredCsvTests(unittest.TestCase):
    def test_filters_rows_by_uid_and_names_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year = 2093
            ingest = root / "data" / "bm_ingest" / str(year)
            ingest.mkdir(parents=True)
            path = ingest / f"artelier_bm_upload_{year}.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["project_title", "project_slug", "bm_uid", "hero_image_url"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "project_title": "Alpha",
                        "project_slug": "alpha",
                        "bm_uid": "uid-a",
                        "hero_image_url": "https://example.com/a.jpg",
                    }
                )
                writer.writerow(
                    {
                        "project_title": "Beta",
                        "project_slug": "beta",
                        "bm_uid": "uid-b",
                        "hero_image_url": "",
                    }
                )
                writer.writerow(
                    {
                        "project_title": "Gamma",
                        "project_slug": "gamma",
                        "bm_uid": "uid-c",
                        "hero_image_url": "https://example.com/c.jpg",
                    }
                )

            result = export_filtered_csv(
                root,
                year=year,
                keys=["uid-a", "uid-c", "alpha", "gamma"],
                kind="upload",
                filter_id="has_image",
                filter_label="Has hero photo",
            )
            self.assertEqual(result["row_count"], 2)
            self.assertEqual(result["filename"], f"artelier_bm_upload_{year}_has_hero_photo.csv")
            text = result["content"].decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))
            titles = [row["project_title"] for row in reader]
            self.assertEqual(titles, ["Alpha", "Gamma"])

    def test_unfiltered_keeps_original_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year = 2092
            ingest = root / "data" / "bm_ingest" / str(year)
            ingest.mkdir(parents=True)
            path = ingest / f"artelier_bm_upload_{year}.csv"
            path.write_text(
                "project_title,project_slug,bm_uid,hero_image_url\n"
                "Solo,solo,uid-s,https://example.com/s.jpg\n",
                encoding="utf-8",
            )
            result = export_filtered_csv(
                root,
                year=year,
                keys=["uid-s"],
                unfiltered=True,
                filter_id="all",
                filter_label="All projects",
            )
            self.assertEqual(result["filename"], f"artelier_bm_upload_{year}.csv")


if __name__ == "__main__":
    unittest.main()
