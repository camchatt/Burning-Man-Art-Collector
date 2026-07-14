from pathlib import Path
import csv
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.artelier_schema import load_import_schema
from burning_man_scraper.batch import BatchResult, ManifestChangeReport
from burning_man_scraper.exporter import export_completed_batch, next_batch_directory, write_artelier_csv
from burning_man_scraper.state import ApprovalContext, ScraperState


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")
URL = "https://history.burningman.org/art-history/archive/?yyyy=2022"


def batch_result(successful: int = 1, failed: int = 0, skipped: int = 0) -> BatchResult:
    return BatchResult(
        attempted=successful + failed,
        succeeded=successful,
        failed=failed,
        skipped=skipped,
        duplicates=0,
        next_unprocessed_record=successful + 1,
        completed_urls=[f"{URL}#record-{index}" for index in range(1, successful + 1)],
        failed_urls=[],
        skipped_urls=[],
        manifest_changes=ManifestChangeReport(),
        attempt_ceiling=successful + failed + 1,
    )


def artelier_row(title: str, summary: str = "Summary") -> dict[str, object]:
    row = {header: "" for header in SCHEMA.headers}
    row.update(
        {
            "project_title": title,
            "project_slug": title.lower().replace(" ", "-"),
            "project_year": "2022",
            "project_summary": summary,
            "project_visibility": "private",
            "proof_external_url": f"{URL}#{title.lower().replace(' ', '-')}",
            "proof_visibility": "private",
            "permission_status": "pending_permission",
        }
    )
    return row


class ExporterTests(unittest.TestCase):
    def state_source_batch(self):
        temp_dir = tempfile.TemporaryDirectory()
        state = ScraperState(Path(temp_dir.name) / "state.sqlite3")
        source = state.get_or_create_source(URL, URL).source
        context = ApprovalContext(
            preview_run_id="preview-run",
            source_id=source.source_id,
            normalized_source_url=URL,
            proposed_batch_number=state.next_proposed_batch_number(source.source_id),
            requested_count=2,
            preview_record_id="preview",
            schema_version="installation-preview-v1",
            parser_version="phase4-installation-preview-v1",
            configuration_hash="config",
            source_manifest_hash="manifest",
        )
        state.save_preview_approval(context, "approved")
        export_batch_id = state.create_pending_export_batch(context)
        return temp_dir, state, source, export_batch_id

    def add_completed_record(self, state, source, export_batch_id, position, title, row=None):
        row = row or artelier_row(title)
        state.mark_source_record_by_canonical(
            source_id=source.source_id,
            source_position=position,
            installation_url=f"{URL}#record-{position}",
            canonical_installation_url=f"{URL}#record-{position}",
            record_id=f"record-{position}",
            record_status="completed",
            content_hash=f"hash-{position}",
            export_batch_id=export_batch_id,
            record_json=json.dumps(
                {
                    "title": title,
                    "description": row.get("project_summary"),
                    "warnings": [],
                    "parsing_errors": [],
                }
            ),
            artelier_row_json=json.dumps(row),
        )

    def test_unique_batch_numbering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            batches = Path(temp_dir) / "batches"
            (batches / "batch_001").mkdir(parents=True)

            self.assertEqual(next_batch_directory(batches).name, "batch_002")

    def test_no_accidental_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            batches = Path(temp_dir) / "batches"
            target = batches / "batch_001"
            target.mkdir(parents=True)
            (target / "artelier_import.csv").write_text("old", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Overwrite requires confirmation"):
                next_batch_directory(batches, overwrite_batch=1)

    def test_exact_artelier_csv_headers(self):
        temp_dir, state, source, export_batch_id = self.state_source_batch()
        with temp_dir:
            self.add_completed_record(state, source, export_batch_id, 1, "One")
            paths = export_completed_batch(
                state, source, export_batch_id, batch_result(), 1, SCHEMA, Path(temp_dir.name) / "exports"
            )
            with paths.artelier_csv.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.reader(file)
                headers = next(reader)

        self.assertEqual(headers, SCHEMA.headers)

    def test_correct_csv_quoting_and_multiline_descriptions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "quoted.csv"
            row = artelier_row("Comma Work", "Line one,\nLine two")
            write_artelier_csv(path, [row], SCHEMA)
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                saved = next(csv.DictReader(file))

        self.assertEqual(saved["project_summary"], "Line one,\nLine two")

    def test_empty_cells_for_null_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nulls.csv"
            row = artelier_row("Null Work")
            row["hero_image_url"] = None
            write_artelier_csv(path, [row], SCHEMA)
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                saved = next(csv.DictReader(file))

        self.assertEqual(saved["hero_image_url"], "")

    def test_array_flattening_with_double_pipe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "arrays.csv"
            row = artelier_row("Array Work")
            row["project_tags"] = ["fire", "light"]
            write_artelier_csv(path, [row], SCHEMA)
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                saved = next(csv.DictReader(file))

        self.assertEqual(saved["project_tags"], "fire||light")

    def test_manifest_counts(self):
        temp_dir, state, source, export_batch_id = self.state_source_batch()
        with temp_dir:
            self.add_completed_record(state, source, export_batch_id, 1, "One")
            paths = export_completed_batch(
                state,
                source,
                export_batch_id,
                batch_result(successful=1, failed=1, skipped=2),
                3,
                SCHEMA,
                Path(temp_dir.name) / "exports",
            )
            manifest = json.loads(paths.batch_manifest.read_text(encoding="utf-8"))

        self.assertEqual(manifest["requested_count"], 3)
        self.assertEqual(manifest["successful_count"], 1)
        self.assertEqual(manifest["failed_count"], 1)
        self.assertEqual(manifest["skipped_count"], 2)

    def test_export_history_logging(self):
        temp_dir, state, source, export_batch_id = self.state_source_batch()
        with temp_dir:
            self.add_completed_record(state, source, export_batch_id, 1, "One")
            paths = export_completed_batch(
                state, source, export_batch_id, batch_result(), 1, SCHEMA, Path(temp_dir.name) / "exports"
            )
            with paths.export_history.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["batch_id"], str(export_batch_id))

    def test_consolidated_exports(self):
        temp_dir, state, source, export_batch_id = self.state_source_batch()
        with temp_dir:
            self.add_completed_record(state, source, export_batch_id, 1, "One")
            paths = export_completed_batch(
                state, source, export_batch_id, batch_result(), 1, SCHEMA, Path(temp_dir.name) / "exports"
            )
            self.assertTrue(paths.consolidated_csv.exists())
            self.assertTrue(paths.consolidated_json.exists())
            with paths.consolidated_csv.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project_title"], "One")

    def test_rerunning_exports_without_duplicate_rows(self):
        temp_dir, state, source, export_batch_id = self.state_source_batch()
        with temp_dir:
            self.add_completed_record(state, source, export_batch_id, 1, "One")
            self.add_completed_record(state, source, export_batch_id, 1, "One Duplicate")
            paths = export_completed_batch(
                state, source, export_batch_id, batch_result(), 1, SCHEMA, Path(temp_dir.name) / "exports"
            )
            with paths.consolidated_csv.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
