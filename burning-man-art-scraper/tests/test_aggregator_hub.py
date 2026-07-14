import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.aggregator_hub.multipart import parse_multipart
from burning_man_scraper.aggregator_hub.services import cleanup_temps, prepare_deploy_package, validate_core_csv_path
from burning_man_scraper.artelier_schema import load_import_schema


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MultipartTests(unittest.TestCase):
    def test_parse_fields_and_file(self):
        body = (
            b"------bound\r\n"
            b'Content-Disposition: form-data; name="year"\r\n\r\n'
            b"2023\r\n"
            b"------bound\r\n"
            b'Content-Disposition: form-data; name="file"; filename="PlayaEvents-2023_ART.csv"\r\n'
            b"Content-Type: text/csv\r\n\r\n"
            b"Title,Description\r\nHello,World\r\n"
            b"------bound--\r\n"
        )
        fields, files = parse_multipart(body, "multipart/form-data; boundary=----bound")
        self.assertEqual(fields["year"], "2023")
        self.assertIn("file", files)
        self.assertIn(b"Hello,World", files["file"].content)


class CoreValidationTests(unittest.TestCase):
    def test_valid_and_invalid_core_csv(self):
        schema = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.csv"
            with good.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=schema.headers)
                writer.writeheader()
                row = {header: "" for header in schema.headers}
                row.update(
                    {
                        "project_title": "Test",
                        "project_slug": "test",
                        "proof_external_url": "https://example.com/x",
                        "project_visibility": "private",
                        "contributor_visibility": "private",
                        "contribution_visibility": "private",
                        "proof_visibility": "private",
                        "verification_status": "documented",
                        "approval_status": "draft",
                        "permission_status": "pending_permission",
                    }
                )
                writer.writerow(row)
            result = validate_core_csv_path(good, schema, year=2022)
            self.assertTrue(result["ok"], result)

            bad = Path(tmp) / "bad.csv"
            with bad.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=schema.headers)
                writer.writeheader()
                row = {header: "" for header in schema.headers}
                row["project_title"] = "Missing proof"
                row["project_slug"] = "missing-proof"
                writer.writerow(row)
            result = validate_core_csv_path(bad, schema, year=2022)
            self.assertFalse(result["ok"])
            self.assertGreater(result["error_count"], 0)


class CleanupTests(unittest.TestCase):
    def test_cleanup_removes_tmp_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tmp_file = root / "data" / "uploads" / "tmp" / "job" / "x.csv"
            tmp_file.parent.mkdir(parents=True)
            tmp_file.write_text("a", encoding="utf-8")
            result = cleanup_temps(root, preview_max_age_days=14)
            self.assertFalse(tmp_file.exists())
            self.assertGreaterEqual(result["removed_count"], 1)


class DeployPackageTests(unittest.TestCase):
    def test_prepare_deploy_overwrites_single_folder(self):
        schema = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year = 2099
            ingest = root / "data" / "bm_ingest" / str(year)
            ingest.mkdir(parents=True)
            core = ingest / f"artelier_core_only_{year}.csv"
            with core.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=schema.headers)
                writer.writeheader()
                row = {header: "" for header in schema.headers}
                row.update(
                    {
                        "project_title": "Deploy Me",
                        "project_slug": "deploy-me",
                        "proof_external_url": "https://example.com/proof",
                        "project_visibility": "private",
                        "contributor_visibility": "private",
                        "contribution_visibility": "private",
                        "proof_visibility": "private",
                        "verification_status": "documented",
                        "approval_status": "draft",
                        "permission_status": "pending_permission",
                    }
                )
                writer.writerow(row)
            # stale files that should be replaced by overwrite
            stale = root / "data" / "deploy" / str(year)
            stale.mkdir(parents=True)
            (stale / "old.txt").write_text("stale", encoding="utf-8")
            result = prepare_deploy_package(
                root,
                year,
                admin_import_url="https://example.com/import",
                schema_path=PROJECT_ROOT / "config" / "artelier_import_schema.yaml",
            )
            self.assertTrue(result["ok"], result)
            self.assertTrue(Path(result["core_csv"]).exists())
            self.assertFalse((stale / "old.txt").exists())
            manifest = json.loads((stale / "deploy_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["year"], year)
            self.assertTrue(manifest["upload_ready_only"])

    def test_prepare_deploy_skips_needs_attention(self):
        schema = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")
        from burning_man_scraper.bm_ingest.schema import BM_EXTENSION_HEADERS

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year = 2098
            ingest = root / "data" / "bm_ingest" / str(year)
            ingest.mkdir(parents=True)
            headers = list(schema.headers) + list(BM_EXTENSION_HEADERS)
            path = ingest / f"artelier_bm_upload_{year}.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=headers)
                writer.writeheader()
                ready = {header: "" for header in headers}
                ready.update(
                    {
                        "project_title": "Ready",
                        "project_slug": "ready",
                        "project_year": str(year),
                        "proof_external_url": "https://example.com/ready",
                        "project_visibility": "private",
                        "contributor_visibility": "private",
                        "contribution_visibility": "private",
                        "proof_visibility": "private",
                        "verification_status": "documented",
                        "approval_status": "draft",
                        "permission_status": "pending_permission",
                        "review_flags": "honorarium_unknown",
                    }
                )
                blocked = dict(ready)
                blocked.update(
                    {
                        "project_title": "Blocked",
                        "project_slug": "blocked",
                        "proof_external_url": "https://example.com/blocked",
                        "review_flags": "hero_missing|honorarium_unknown",
                    }
                )
                writer.writerow(ready)
                writer.writerow(blocked)
            result = prepare_deploy_package(
                root,
                year,
                schema_path=PROJECT_ROOT / "config" / "artelier_import_schema.yaml",
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["row_count"], 1)
            self.assertEqual(result["skipped_not_ready"], 1)
            with Path(result["core_csv"]).open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["project_title"], "Ready")


if __name__ == "__main__":
    unittest.main()
