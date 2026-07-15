import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.bm_ingest.contributors import normalize_contributor
from burning_man_scraper.bm_ingest.hero import resolve_hero
from burning_man_scraper.bm_ingest.merge import build_ingest_rows
from burning_man_scraper.bm_ingest.schema import BM_EXTENSION_HEADERS, REVIEW_FLAGS_ALLOWED
from burning_man_scraper.bm_ingest.view_bundle import build_aggregator_view
from burning_man_scraper.bm_ingest.writer import write_ingest_outputs
from burning_man_scraper.artelier_schema import load_import_schema


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ContributorNormalizationTests(unittest.TestCase):
    def test_aka_split(self):
        result = normalize_contributor("Eric Tussey a.k.a. pebble")
        self.assertEqual(result.contributor_display_name, "Eric Tussey")
        self.assertEqual(result.playa_name, "pebble")
        self.assertEqual(result.contributor_first_name, "Eric")
        self.assertEqual(result.contributor_last_name, "Tussey")
        self.assertEqual(result.source_artist_credit, "Eric Tussey a.k.a. pebble")
        self.assertNotIn("pebble", result.additional_contributor_credits)

    def test_multi_person_additional_credits(self):
        result = normalize_contributor("Alice Smith and Bob Jones")
        self.assertEqual(result.contributor_display_name, "Alice Smith")
        self.assertIn("Bob Jones", result.additional_contributor_credits)
        self.assertTrue(all(flag in REVIEW_FLAGS_ALLOWED for flag in result.review_flags))


class HeroResolverTests(unittest.TestCase):
    def test_prefers_verification_cache(self):
        hero = resolve_hero(
            uid="abc",
            title="Test",
            year=2022,
            verification_row={
                "hero_image_url": "https://burningman.widen.net/content/x/jpeg/y.jpeg",
                "hero_image_active": "True",
                "public_credit_language": "Photo courtesy of Burning Man Project History Archive",
                "archive_url": "https://history.burningman.org/art-history/archive/?yyyy=2022#abc",
            },
            archive_record=None,
            image_entry=None,
            collector_bundle=None,
            fetch_missing=False,
        )
        self.assertEqual(hero.hero_image_url, "https://burningman.widen.net/content/x/jpeg/y.jpeg")
        self.assertEqual(hero.provenance, "verification")
        self.assertNotIn("hero_missing", hero.review_flags or [])

    def test_missing_without_network(self):
        hero = resolve_hero(
            uid=None,
            title="Test",
            year=2022,
            verification_row=None,
            archive_record=None,
            image_entry=None,
            collector_bundle=None,
            fetch_missing=False,
        )
        self.assertEqual(hero.hero_image_url, "")
        self.assertIn("hero_missing", hero.review_flags or [])


class IngestIntegrationTests(unittest.TestCase):
    def test_2022_cache_build_headers(self):
        www_dir = PROJECT_ROOT.parent / "What When Where Files"
        if not www_dir.exists():
            self.skipTest("WWW files not present")
        verification = PROJECT_ROOT / "data" / "verification" / "2022" / "verification_report_2022.csv"
        if not verification.exists():
            self.skipTest("2022 verification cache not present")

        rows, stats = build_ingest_rows(
            project_root=PROJECT_ROOT,
            year=2022,
            www_dir=www_dir,
            fetch_missing_heroes=False,
        )
        self.assertGreater(len(rows), 100)
        self.assertEqual(stats.get("network_requests_attempted"), 0)
        self.assertGreater(stats.get("identity_cache_matches", 0), 0)
        sample = rows[0]
        for header in BM_EXTENSION_HEADERS:
            self.assertIn(header, sample)
        self.assertIn("project_title", sample)
        self.assertIn("hero_image_url", sample)
        # review_flags must only contain allowed codes
        for row in rows:
            for flag in (row.get("review_flags") or "").split("|"):
                if flag:
                    self.assertIn(flag, REVIEW_FLAGS_ALLOWED)
            # contributor names must not be stuffed into review_flags
            for flag in (row.get("review_flags") or "").split("|"):
                self.assertFalse(" " in flag and flag not in REVIEW_FLAGS_ALLOWED)

        schema = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_ingest_outputs(
                output_dir=Path(tmp),
                year=2022,
                rows=rows[:5],
                artelier_headers=schema.headers,
                fetch_missing_heroes=False,
                project_root=Path(tmp) / "root",
            )
            summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
            self.assertEqual(summary["project_count"], 5)
            view = json.loads(paths["view"].read_text(encoding="utf-8"))
            self.assertEqual(view["upload_checklist"]["project_count"], 5)
            self.assertEqual(len(view["projects"]), 5)
            self.assertIn("needs_attention", view["projects"][0])
            self.assertTrue((Path(tmp) / "root" / "viewer" / "aggregator" / "data" / "aggregator_view.json").exists())
            www_preview = Path(tmp) / "What When Where Files" / "aggregator_previews" / "aggregator_view_2022.json"
            self.assertTrue(www_preview.exists())
            self.assertTrue(paths["www_preview"].exists())


class ViewBundleTests(unittest.TestCase):
    def test_attention_ignores_honorarium_only(self):
        rows = [
            {
                "project_title": "Ready Piece",
                "project_slug": "ready-piece",
                "project_year": "2022",
                "proof_external_url": "https://example.com/proof",
                "hero_image_url": "https://burningman.widen.net/x.jpg",
                "contributor_display_name": "Ada Lovelace",
                "source_artist_credit": "Ada Lovelace",
                "review_flags": "honorarium_unknown",
                "playa_address": "3:00 & Esplanade",
                "bm_uid": "uid1",
            },
            {
                "project_title": "Missing Hero",
                "project_slug": "missing-hero",
                "project_year": "2022",
                "proof_external_url": "https://example.com/proof2",
                "hero_image_url": "",
                "contributor_display_name": "Ada Lovelace",
                "source_artist_credit": "Ada Lovelace",
                "review_flags": "hero_missing|honorarium_unknown",
                "playa_address": "",
                "bm_uid": "uid2",
            },
        ]
        view = build_aggregator_view(year=2022, rows=rows)
        self.assertEqual(view["upload_checklist"]["upload_ready_count"], 1)
        self.assertEqual(view["upload_checklist"]["needs_attention_count"], 1)


if __name__ == "__main__":
    unittest.main()
