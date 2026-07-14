import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.verification.image_validator import infer_attribution
from burning_man_scraper.verification.processor import _determine_status
from burning_man_scraper.verification.text_match import artist_similarity, similarity_score
from burning_man_scraper.verification.www_loader import (
    ArtCsvYearMismatchError,
    assert_art_csv_matches_year,
    infer_year_from_art_csv_links,
    infer_year_from_filename,
    load_www_records,
)


class VerificationTextMatchTests(unittest.TestCase):
    def test_similarity_score_for_matching_titles(self):
        score = similarity_score("Struggles of the Heart", "Struggle of the Heart")
        self.assertGreaterEqual(score, 0.5)

    def test_artist_similarity_ignores_by_prefix(self):
        score = artist_similarity("Eric Tussey a.k.a. pebble", "by: Eric Tussey a.k.a. pebble")
        self.assertEqual(score, 1.0)


class VerificationStatusTests(unittest.TestCase):
    def test_verified_online_when_images_active(self):
        status = _determine_status(
            title_score=0.95,
            uid_match=True,
            warnings=[],
            active_image_count=1,
            image_count=1,
        )
        self.assertEqual(status, "verified_online")

    def test_broken_link_when_images_inactive(self):
        status = _determine_status(
            title_score=0.95,
            uid_match=True,
            warnings=[],
            active_image_count=0,
            image_count=1,
        )
        self.assertEqual(status, "broken_link")


class ImageAttributionTests(unittest.TestCase):
    def test_widen_images_get_official_credit(self):
        attribution = infer_attribution(
            "https://burningman.widen.net/content/mcyytem3je/jpeg/a2I8X00000h85IfUAI-Final.jpeg"
        )
        self.assertEqual(attribution["source_type"], "burning_man_official")
        self.assertFalse(attribution["review_required"])

    def test_googleusercontent_requires_review(self):
        attribution = infer_attribution("https://lh3.googleusercontent.com/pw/example")
        self.assertTrue(attribution["review_required"])


class WwwLoaderTests(unittest.TestCase):
    def test_load_2022_www_records(self):
        www_dir = Path(__file__).resolve().parents[2] / "What When Where Files"
        if not www_dir.exists():
            self.skipTest("WWW reference directory is unavailable in this environment.")
        records = load_www_records(www_dir, year=2022)
        self.assertGreater(len(records), 300)
        self.assertTrue(any(record.uid for record in records))

    def test_infer_year_from_filename(self):
        self.assertEqual(infer_year_from_filename("PlayaEvents-2025_ART.csv"), 2025)
        self.assertEqual(infer_year_from_filename("PlayaEvents-2022 (1)_ART.csv"), 2022)
        self.assertIsNone(infer_year_from_filename("random.csv"))

    def test_reject_2025_csv_as_2022(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "upload.csv"
            path.write_text(
                "Title,Description,Link,UID\n"
                "Nova,Desc,http://burningman.org/event/brc/2025-art-installations/#Nova,a2IVI0000016gf72AA\n"
                "Other,Desc,http://burningman.org/event/brc/2025-art-installations/#Other,a2IVI0000016gf82AA\n",
                encoding="utf-8",
            )
            self.assertEqual(infer_year_from_art_csv_links(path), 2025)
            with self.assertRaises(ArtCsvYearMismatchError) as ctx:
                assert_art_csv_matches_year(path, 2022, original_filename="PlayaEvents-2025_ART.csv")
            self.assertIn("2025", str(ctx.exception))
            self.assertIn("2022", str(ctx.exception))

            with self.assertRaises(ArtCsvYearMismatchError):
                assert_art_csv_matches_year(path, 2022, original_filename="upload.csv")

    def test_accept_matching_year(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "PlayaEvents-2022_ART.csv"
            path.write_text(
                "Title,Description,Link,UID\n"
                "Rx4u,Desc,http://burningman.org/event/brc/2022-art-installations/?artType=B#Rx4u,a2I8X00000h8T26UAE\n",
                encoding="utf-8",
            )
            assert_art_csv_matches_year(path, 2022)

    def test_resolve_year_from_csv_links(self):
        import tempfile

        from burning_man_scraper.verification.www_loader import resolve_art_csv_year

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "upload.csv"
            path.write_text(
                "Title,Description,Link,UID\n"
                "Nova,Desc,http://burningman.org/event/brc/2025-art-installations/#Nova,a2IVI0000016gf72AA\n",
                encoding="utf-8",
            )
            self.assertEqual(resolve_art_csv_year(path, original_filename="upload.csv"), 2025)


if __name__ == "__main__":
    unittest.main()
