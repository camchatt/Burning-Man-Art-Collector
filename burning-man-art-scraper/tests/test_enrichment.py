from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.config import ScraperConfig
from burning_man_scraper.enrichment.batch_loader import list_available_batches, load_batch_records
from burning_man_scraper.enrichment.cli import prompt_for_batch_selection, run_enrichment_workflow
from burning_man_scraper.enrichment.providers import NoOpSearchProvider
from burning_man_scraper.enrichment.state import EnrichmentState
from burning_man_scraper.state import ScraperState


def write_batch(
    export_root: Path,
    year: str = "2022",
    batch_name: str = "batch_001",
    batch_id: int = 1,
    titles: list[str] | None = None,
) -> Path:
    titles = titles or ["One", "Two", "Three"]
    batch_dir = export_root / "burning_man" / year / "batches" / batch_name
    batch_dir.mkdir(parents=True)
    (batch_dir / "batch_manifest.json").write_text(
        json.dumps(
            {
                "batch_id": batch_id,
                "detected_year": year,
                "successful_count": len(titles),
                "requested_count": len(titles),
            }
        ),
        encoding="utf-8",
    )
    payload = []
    for index, title in enumerate(titles, start=1):
        payload.append(
            {
                "mapped_artelier_values": {
                    "project_title": title,
                    "project_slug": title.lower(),
                    "contributor_name": f"Artist {index}",
                    "proof_external_url": f"https://example.com/{index}",
                },
                "original_scraped_values": {
                    "record_id": f"record-{index}",
                    "title": title,
                    "artist_display_text": f"Artist {index}",
                },
                "source_urls": {
                    "canonical_installation_url": f"https://history.burningman.org/archive/#record-{index}",
                },
                "source_position": index,
                "record_status": "completed",
            }
        )
    (batch_dir / "full_export.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (batch_dir / "artelier_import.csv").write_text(
        "project_title,contributor_name,proof_external_url\n"
        + "\n".join(f"{title},Artist {index},https://example.com/{index}" for index, title in enumerate(titles, 1)),
        encoding="utf-8",
    )
    return batch_dir


class EnrichmentTests(unittest.TestCase):
    def test_listing_available_scrape_batches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            write_batch(export_root, "2022", "batch_001", 1, ["One", "Two"])
            write_batch(export_root, "2023", "batch_001", 2, ["Three"])

            batches = list_available_batches(export_root)

        self.assertEqual([batch.display_label for batch in batches], [
            "2022 / batch_001 / 2 records",
            "2023 / batch_001 / 1 records",
        ])

    def test_selecting_a_batch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            write_batch(export_root, "2022", "batch_001", 1)
            write_batch(export_root, "2022", "batch_002", 2)
            outputs: list[str] = []

            selected = prompt_for_batch_selection(
                list_available_batches(export_root),
                input_func=lambda _: "2",
                output_func=outputs.append,
            )

        self.assertEqual(selected.batch_name, "batch_002")
        self.assertIn("Available batches:", outputs)

    def test_reading_batch_records_from_full_export(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            batch_dir = write_batch(export_root, titles=["One", "Two"])
            batch = list_available_batches(export_root)[0]

            records = load_batch_records(batch)

        self.assertEqual([record.project_title for record in records], ["One", "Two"])
        self.assertEqual(records[0].project_record_id, "record-1")
        self.assertEqual(batch_dir.name, "batch_001")

    def test_preserving_original_batch_files_and_reserving_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            batch_dir = write_batch(export_root)
            before = {
                name: (batch_dir / name).read_text(encoding="utf-8")
                for name in ("artelier_import.csv", "full_export.json", "batch_manifest.json")
            }
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            inputs = iter(["1", "2", "2"])

            exit_code = run_enrichment_workflow(
                config=ScraperConfig(
                    export_root_dir=export_root,
                    state_database_path=state.database_path,
                ),
                state_store=state,
                input_func=lambda _: next(inputs),
                output_func=lambda _message: None,
                search_client=NoOpSearchProvider(),
                fetch_client=FakeFetch({}),
            )

            after = {
                name: (batch_dir / name).read_text(encoding="utf-8")
                for name in ("artelier_import.csv", "full_export.json", "batch_manifest.json")
            }

        self.assertEqual(exit_code, 1)
        self.assertEqual(before, after)
        self.assertFalse((batch_dir / "enriched_artelier_import.csv").exists())
        self.assertFalse((batch_dir / "enrichment_review.csv").exists())
        self.assertFalse((batch_dir / "enrichment_manifest.json").exists())

    def test_no_provider_stops_enrichment_and_does_not_create_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            write_batch(export_root)
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            inputs = iter(["1", "1"])
            outputs: list[str] = []

            exit_code = run_enrichment_workflow(
                config=ScraperConfig(
                    export_root_dir=export_root,
                    state_database_path=state.database_path,
                ),
                state_store=state,
                input_func=lambda _: next(inputs),
                output_func=outputs.append,
                search_client=NoOpSearchProvider(),
                fetch_client=FakeFetch({}),
            )
            with state.connection() as connection:
                state.initialize()
                count = connection.execute("SELECT COUNT(*) FROM enrichment_runs").fetchone()[0]

        self.assertEqual(exit_code, 1)
        self.assertIn("ENRICHMENT NOT STARTED", outputs)
        self.assertEqual(count, 0)

    def test_creating_enrichment_state_and_batch_association(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            write_batch(export_root, batch_id=42)
            batch = list_available_batches(export_root)[0]
            records = load_batch_records(batch)
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            enrichment_state = EnrichmentState(state)

            run = enrichment_state.create_run(
                export_batch_id=batch.export_batch_id,
                source_batch_directory=batch.batch_directory,
                requested_count=2,
                selected_records=records[:2],
            )
            run_records = enrichment_state.records_for_run(run.enrichment_run_id)

        self.assertEqual(run.export_batch_id, 42)
        self.assertEqual(run.records_selected, 2)
        self.assertEqual(len(run_records), 2)
        self.assertEqual(run_records[0]["enrichment_status"], "pending")

    def test_resuming_from_next_unenriched_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            write_batch(export_root, titles=["One", "Two", "Three"])
            batch = list_available_batches(export_root)[0]
            records = load_batch_records(batch)
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            enrichment_state = EnrichmentState(state)
            enrichment_state.mark_project_status(
                batch.export_batch_id,
                batch.batch_directory,
                "record-1",
                "One",
                "Artist 1",
                "approved",
            )

            selected = enrichment_state.select_records(
                records,
                batch.export_batch_id,
                batch.batch_directory,
                requested_count=2,
            )

        self.assertEqual([record.project_record_id for record in selected], ["record-2", "record-3"])

    def test_not_selecting_approved_records_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            write_batch(export_root, titles=["One", "Two"])
            batch = list_available_batches(export_root)[0]
            records = load_batch_records(batch)
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            enrichment_state = EnrichmentState(state)
            enrichment_state.mark_project_status(
                batch.export_batch_id,
                batch.batch_directory,
                "record-1",
                "One",
                "Artist 1",
                "approved",
            )

            selected = enrichment_state.select_records(
                records,
                batch.export_batch_id,
                batch.batch_directory,
                requested_count=2,
            )

        self.assertEqual([record.project_record_id for record in selected], ["record-2"])

    def test_retrying_failed_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            export_root = Path(temp_dir) / "exports"
            write_batch(export_root, titles=["One", "Two"])
            batch = list_available_batches(export_root)[0]
            records = load_batch_records(batch)
            state = ScraperState(Path(temp_dir) / "state.sqlite3")
            enrichment_state = EnrichmentState(state)
            enrichment_state.mark_project_status(
                batch.export_batch_id,
                batch.batch_directory,
                "record-1",
                "One",
                "Artist 1",
                "failed",
            )

            selected = enrichment_state.select_records(
                records,
                batch.export_batch_id,
                batch.batch_directory,
                requested_count=2,
                resume_action="retry_failed",
            )

        self.assertEqual([record.project_record_id for record in selected], ["record-1"])


if __name__ == "__main__":
    unittest.main()


class FakeFetch:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url):
        if url not in self.pages:
            raise KeyError(url)
        return self.pages[url]
