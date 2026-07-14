from pathlib import Path
import csv
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.artelier_schema import load_import_schema
from burning_man_scraper.enrichment.batch_loader import list_available_batches, load_batch_records
from burning_man_scraper.enrichment.models import ProposedEnrichment
from burning_man_scraper.enrichment.processor import process_approved_enrichment_batch, write_enrichment_outputs
from burning_man_scraper.enrichment.providers import NoOpSearchProvider
from burning_man_scraper.enrichment.state import EnrichmentState
from burning_man_scraper.state import ScraperState


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")


class FakeFetch:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url):
        if url not in self.pages:
            raise KeyError(url)
        return self.pages[url]


class MalformedFetch(FakeFetch):
    def fetch(self, url):
        if url.endswith("/2"):
            return None
        return super().fetch(url)


def base_row(title: str, index: int) -> dict[str, str]:
    row = {header: "" for header in SCHEMA.headers}
    row.update(
        {
            "project_title": title,
            "project_slug": title.lower().replace(" ", "-"),
            "project_year": "2022",
            "contributor_name": f"Artist {index}",
            "proof_external_url": f"https://history.burningman.org/archive/#record-{index}",
        }
    )
    return row


def write_batch(export_root: Path, titles: list[str] | None = None) -> Path:
    titles = titles or ["One", "Two", "Three"]
    batch_dir = export_root / "burning_man" / "2022" / "batches" / "batch_001"
    batch_dir.mkdir(parents=True)
    (batch_dir / "batch_manifest.json").write_text(
        json.dumps({"batch_id": 7, "successful_count": len(titles), "requested_count": len(titles)}),
        encoding="utf-8",
    )
    payload = []
    for index, title in enumerate(titles, start=1):
        row = base_row(title, index)
        row["contributor_website"] = f"https://artist.example/{index}"
        payload.append(
            {
                "mapped_artelier_values": row,
                "original_scraped_values": {
                    "record_id": f"record-{index}",
                    "title": title,
                    "artist_display_text": f"Artist {index}",
                    "website_url": f"https://artist.example/{index}",
                    "year": "2022",
                },
                "source_urls": {
                    "canonical_installation_url": f"https://history.burningman.org/archive/#record-{index}",
                },
                "source_position": index,
                "record_status": "completed",
            }
        )
    (batch_dir / "full_export.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (batch_dir / "artelier_import.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCHEMA.headers)
        writer.writeheader()
        writer.writerows([base_row(title, index) for index, title in enumerate(titles, start=1)])
    return batch_dir


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def proposal(field: str, proposed: str, original: str = "", source_type: str = "first_party") -> ProposedEnrichment:
    return ProposedEnrichment(
        artelier_field=field,
        original_value=original,
        proposed_value=proposed,
        source_url="https://artist.example/source",
        source_title="Artist source",
        source_type=source_type,
        source_excerpt="Materials: steel.",
        confidence=0.9,
        evidence_classification="directly_stated",
        review_required=False,
    )


class EnrichmentProcessorTests(unittest.TestCase):
    def test_original_files_unchanged_and_outputs_in_batch_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            batch_dir = write_batch(Path(temp_dir) / "exports")
            originals = {name: (batch_dir / name).read_bytes() for name in ("artelier_import.csv", "full_export.json", "batch_manifest.json")}
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            batch = list_available_batches(Path(temp_dir) / "exports")[0]
            records = load_batch_records(batch)
            enrichment_state = EnrichmentState(state)
            run = enrichment_state.create_run(batch.export_batch_id, batch.batch_directory, 1, records[:1])

            paths = write_enrichment_outputs(enrichment_state, run, records, SCHEMA)

            self.assertTrue(paths[0].exists())
            self.assertTrue(paths[1].exists())
            self.assertTrue(paths[2].parent == batch_dir)
            for name, before in originals.items():
                self.assertEqual((batch_dir / name).read_bytes(), before)

    def test_enriched_file_contains_every_original_row_and_exact_headers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            batch_dir = write_batch(Path(temp_dir) / "exports", ["One", "Two"])
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            batch = list_available_batches(Path(temp_dir) / "exports")[0]
            records = load_batch_records(batch)
            enrichment_state = EnrichmentState(state)
            run = enrichment_state.create_run(batch.export_batch_id, batch.batch_directory, 2, records)

            paths = write_enrichment_outputs(enrichment_state, run, records, SCHEMA)
            with paths[0].open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                headers = next(reader)
                rows = list(reader)

        self.assertEqual(headers, SCHEMA.headers)
        self.assertEqual(len(rows), 2)

    def test_approved_changes_apply_rejected_and_unresolved_preserve_originals(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            write_batch(Path(temp_dir) / "exports", ["One"])
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            batch = list_available_batches(Path(temp_dir) / "exports")[0]
            records = load_batch_records(batch)
            enrichment_state = EnrichmentState(state)
            run = enrichment_state.create_run(batch.export_batch_id, batch.batch_directory, 1, records)
            enrichment_state.save_proposed_changes(
                run.enrichment_run_id,
                records[0],
                [proposal("project_materials", "steel"), proposal("project_tags", "light"), proposal("why_it_mattered", "community")],
                approval_mode="auto_apply_high_confidence_direct_statements",
            )
            with state.connection() as connection:
                connection.execute("UPDATE enrichment_changes SET review_status = 'rejected' WHERE artelier_field = 'project_tags'")
                connection.execute("UPDATE enrichment_changes SET review_status = 'unresolved' WHERE artelier_field = 'why_it_mattered'")

            paths = write_enrichment_outputs(enrichment_state, run, records, SCHEMA)
            row = read_rows(paths[0])[0]
            review_rows = read_rows(paths[1])

        self.assertEqual(row["project_materials"], "steel")
        self.assertEqual(row["project_tags"], "")
        self.assertEqual(row["why_it_mattered"], "")
        self.assertEqual({item["review_status"] for item in review_rows}, {"approved", "rejected", "unresolved"})

    def test_protected_fields_require_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            write_batch(Path(temp_dir) / "exports", ["One"])
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            batch = list_available_batches(Path(temp_dir) / "exports")[0]
            record = load_batch_records(batch)[0]
            enrichment_state = EnrichmentState(state)
            run = enrichment_state.create_run(batch.export_batch_id, batch.batch_directory, 1, [record])

            enrichment_state.save_proposed_changes(
                run.enrichment_run_id,
                record,
                [proposal("client_name", "Sensitive Client")],
                approval_mode="auto_apply_high_confidence_direct_statements",
            )
            changes = enrichment_state.changes_for_batch(batch.export_batch_id, batch.batch_directory)

        self.assertEqual(changes[0]["review_status"], "unresolved")

    def test_batch_processing_manifest_counts_and_failures_preserve_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            write_batch(Path(temp_dir) / "exports", ["One", "Two"])
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            batch = list_available_batches(Path(temp_dir) / "exports")[0]
            records = load_batch_records(batch)
            enrichment_state = EnrichmentState(state)
            run = enrichment_state.create_run(batch.export_batch_id, batch.batch_directory, 2, records)

            result = process_approved_enrichment_batch(
                enrichment_state=enrichment_state,
                run=run,
                batch_records=records,
                selected_records=records,
                schema=SCHEMA,
                search_client=NoOpSearchProvider(),
                fetch_client=MalformedFetch({"https://artist.example/1": "<html><p>Artist 1 created One for Burning Man 2022. Materials: steel.</p></html>"}),
                approval_mode="auto_apply_high_confidence_direct_statements",
            )
            rows = read_rows(result.enriched_csv)
            manifest = json.loads(result.manifest_json.read_text(encoding="utf-8"))

        self.assertEqual(len(rows), 2)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(manifest["attempted_count"], 2)
        self.assertEqual(manifest["failed_count"], 1)

    def test_resume_behavior_and_rerun_does_not_duplicate_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            write_batch(Path(temp_dir) / "exports", ["One", "Two"])
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            batch = list_available_batches(Path(temp_dir) / "exports")[0]
            records = load_batch_records(batch)
            enrichment_state = EnrichmentState(state)
            run = enrichment_state.create_run(batch.export_batch_id, batch.batch_directory, 1, records[:1])
            enrichment_state.save_proposed_changes(
                run.enrichment_run_id,
                records[0],
                [proposal("project_materials", "steel")],
                approval_mode="auto_apply_high_confidence_direct_statements",
            )
            enrichment_state.save_proposed_changes(
                run.enrichment_run_id,
                records[0],
                [proposal("project_materials", "steel")],
                approval_mode="auto_apply_high_confidence_direct_statements",
            )
            enrichment_state.update_record_result(run.enrichment_run_id, records[0].project_record_id, "enriched")

            selected = enrichment_state.select_records(records, batch.export_batch_id, batch.batch_directory, 1)
            changes = enrichment_state.changes_for_batch(batch.export_batch_id, batch.batch_directory)

        self.assertEqual([record.project_record_id for record in selected], ["record-2"])
        self.assertEqual(len(changes), 1)

    def test_regeneration_from_sqlite_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            write_batch(Path(temp_dir) / "exports", ["One"])
            database_path = Path(temp_dir) / "state.sqlite3"
            state = ScraperState(database_path)
            batch = list_available_batches(Path(temp_dir) / "exports")[0]
            records = load_batch_records(batch)
            enrichment_state = EnrichmentState(state)
            run = enrichment_state.create_run(batch.export_batch_id, batch.batch_directory, 1, records)
            enrichment_state.save_proposed_changes(
                run.enrichment_run_id,
                records[0],
                [proposal("project_materials", "steel")],
                approval_mode="auto_apply_high_confidence_direct_statements",
            )

            reloaded_state = EnrichmentState(ScraperState(database_path))
            paths = write_enrichment_outputs(reloaded_state, run, records, SCHEMA)
            row = read_rows(paths[0])[0]

        self.assertEqual(row["project_materials"], "steel")


if __name__ == "__main__":
    unittest.main()
