import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.artelier_schema import load_import_schema
from burning_man_scraper.bm_ingest.contributors import normalize_contributor
from burning_man_scraper.bm_ingest.identity_join import (
    collapse_person_or_org,
    contributor_from_identity,
)
from burning_man_scraper.bm_ingest.merge import build_ingest_rows, run_ingest
from burning_man_scraper.bm_ingest.schema import BM_EXTENSION_HEADERS
from burning_man_scraper.bm_ingest.sources import load_identity_by_key, lookup_identity
from burning_man_scraper.bm_ingest.writer import write_ingest_outputs
from burning_man_scraper.verification.models import WwwReferenceRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class IdentityJoinUnitTests(unittest.TestCase):
    def test_eric_tussey_pebble(self):
        result = contributor_from_identity(
            {
                "archive_credit": "Eric Tussey a.k.a. pebble",
                "credit_type": "person_with_playa_name",
                "legal_name": "Eric Tussey",
                "playa_name": "pebble",
                "playa_name_confidence": "high",
                "identity_status": "resolved",
                "named_people": "Eric Tussey",
            }
        )
        self.assertEqual(collapse_person_or_org(result.contributor_kind), "person")
        self.assertEqual(result.contributor_display_name, "Eric Tussey")
        self.assertEqual(result.playa_name, "pebble")
        self.assertNotIn("pebble", result.contributor_display_name.lower())

    def test_multi_entity_legal_name_not_flattened(self):
        result = contributor_from_identity(
            {
                "archive_credit": "Jeff Tangen, Disciples of the Dust",
                "credit_type": "multi_person",
                "legal_name": "Jeff Tangen; Disciples of the Dust",
                "identity_status": "resolved",
                "named_people": "Jeff Tangen | Disciples of the Dust",
            }
        )
        self.assertEqual(result.contributor_kind, "multiple")
        self.assertEqual(result.contributor_display_name, "Jeff Tangen")
        self.assertIn("Disciples of the Dust", result.additional_contributor_credits)
        self.assertNotIn(";", result.contributor_display_name)

    def test_person_plus_collective(self):
        result = contributor_from_identity(
            {
                "archive_credit": "Anna Mok & The Love's Huggers",
                "credit_type": "multi_person",
                "legal_name": "Anna Mok; The Love's Huggers",
                "identity_status": "resolved",
                "named_people": "Anna Mok | The Love's Huggers",
            }
        )
        self.assertEqual(result.contributor_display_name, "Anna Mok")
        self.assertIn("Love's Huggers", result.additional_contributor_credits)

    def test_collective_kind(self):
        result = contributor_from_identity(
            {
                "archive_credit": "Dusty Collective Studio",
                "credit_type": "collective",
                "collective_name": "Dusty Collective Studio",
                "identity_status": "resolved",
            }
        )
        self.assertIn(result.contributor_kind, {"collective", "organization", "studio"})
        self.assertEqual(collapse_person_or_org(result.contributor_kind), "org")

    def test_local_fallback_no_network(self):
        result = normalize_contributor("Eric Tussey a.k.a. pebble")
        self.assertEqual(result.contributor_display_name, "Eric Tussey")
        self.assertEqual(result.playa_name, "pebble")


class IdentityLookupTests(unittest.TestCase):
    def test_uid_before_title_and_conflict_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity_report_2099.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "year",
                        "project_title",
                        "archive_uid",
                        "legal_name",
                        "identity_status",
                        "credit_type",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "year": "2099",
                        "project_title": "Same Title",
                        "archive_uid": "uid-a",
                        "legal_name": "Alice",
                        "identity_status": "resolved",
                        "credit_type": "person",
                    }
                )
                writer.writerow(
                    {
                        "year": "2099",
                        "project_title": "Same Title",
                        "archive_uid": "uid-b",
                        "legal_name": "Bob",
                        "identity_status": "resolved",
                        "credit_type": "person",
                    }
                )
            index = load_identity_by_key(path)
            hit, mode = lookup_identity(index, uid="uid-a", year=2099, title="Same Title")
            self.assertEqual(mode, "uid")
            self.assertEqual(hit["legal_name"], "Alice")
            conflict, mode2 = lookup_identity(index, uid=None, year=2099, title="Same Title")
            self.assertEqual(mode2, "")
            self.assertIsNone(conflict)


class FullSchemaAndCacheTests(unittest.TestCase):
    def test_headers_no_duplicate_hero_and_extensions_present(self):
        schema = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")
        headers = list(schema.headers) + list(BM_EXTENSION_HEADERS)
        self.assertEqual(len(headers), len(set(headers)))
        self.assertIn("hero_image_url", schema.headers)
        self.assertNotIn("hero_image_url", BM_EXTENSION_HEADERS)
        self.assertIn("bm_hero_image_source_url", BM_EXTENSION_HEADERS)
        self.assertEqual(len(schema.headers), 36)
        self.assertEqual(len(BM_EXTENSION_HEADERS), 23)

    def test_writer_rejects_duplicate_headers(self):
        schema = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_ingest_outputs(
                    output_dir=Path(tmp),
                    year=2099,
                    rows=[{"project_title": "X"}],
                    artelier_headers=list(schema.headers) + ["hero_image_url"],
                    fetch_missing_heroes=False,
                    stats={"network_requests_attempted": 0},
                )

    def test_2022_cache_first_identity_and_heroes(self):
        www_dir = PROJECT_ROOT.parent / "What When Where Files"
        identity = PROJECT_ROOT / "data" / "verification" / "2022" / "identity_report_2022.csv"
        verification = PROJECT_ROOT / "data" / "verification" / "2022" / "verification_report_2022.csv"
        if not www_dir.exists() or not identity.exists() or not verification.exists():
            self.skipTest("2022 fixtures unavailable")

        with mock.patch("burning_man_scraper.bm_ingest.hero.urlopen") as mocked:
            rows, stats = build_ingest_rows(
                project_root=PROJECT_ROOT,
                year=2022,
                www_dir=www_dir,
                fetch_missing_heroes=False,
            )
            mocked.assert_not_called()

        self.assertGreater(len(rows), 100)
        self.assertEqual(stats["network_requests_attempted"], 0)
        self.assertGreater(stats["identity_cache_matches"], 100)
        self.assertGreater(stats["hero_images_found"], 100)
        self.assertGreater(sum(1 for r in rows if r.get("contributor_name")), 100)

        sample = next(r for r in rows if "world is Watching" in (r.get("project_title") or ""))
        self.assertEqual(sample["contributor_display_name"], "Eric Tussey")
        self.assertEqual(sample["playa_name"], "pebble")
        self.assertIn("identity_report", sample["source_provenance"])
        self.assertTrue(sample.get("hero_image_url"))
        self.assertEqual(sample.get("bm_hero_image_source_url"), sample.get("hero_image_url"))

        # Provenance must not claim identity_report when falling back for a synthetic row.
        for row in rows:
            if "identity_local_fallback" in (row.get("source_provenance") or ""):
                self.assertNotIn("identity_report", row["source_provenance"])

    def test_missing_identity_falls_back_locally(self):
        import shutil

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year = 2097
            project = root / "proj"
            project.mkdir()
            www_dir = root / "www"
            www_dir.mkdir()
            (www_dir / f"PlayaEvents-{year}_ART.csv").write_text(
                "Title,Description,Type,Camp,Where,Extra,Link,UID\n"
                "Solo Work,Desc,Art,,,-,http://example.com/x#Solo,uid-solo\n",
                encoding="utf-8",
            )
            verification_dir = project / "data" / "verification" / str(year)
            verification_dir.mkdir(parents=True)
            (verification_dir / f"archive_index_{year}.json").write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "uid": "uid-solo",
                                "year": year,
                                "title": "Solo Work",
                                "normalized_title": "solo work",
                                "artist_display_text": "Eric Tussey a.k.a. pebble",
                                "canonical_source_url": "https://example.com/proof",
                                "description": "Desc",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (project / "config").mkdir()
            shutil.copy2(
                PROJECT_ROOT / "config" / "artelier_import_schema.yaml",
                project / "config" / "artelier_import_schema.yaml",
            )
            rows, stats = build_ingest_rows(
                project_root=project,
                year=year,
                www_dir=www_dir,
                fetch_missing_heroes=False,
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(stats["identity_cache_matches"], 0)
            self.assertEqual(stats["identity_local_fallbacks"], 1)
            self.assertEqual(rows[0]["contributor_display_name"], "Eric Tussey")
            self.assertIn("identity_local_fallback", rows[0]["source_provenance"])
            self.assertNotIn("identity_report", rows[0]["source_provenance"])


if __name__ == "__main__":
    unittest.main()
