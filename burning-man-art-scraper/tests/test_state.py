from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.state import ScraperState
from burning_man_scraper.url_utils import validate_archive_url


class StateTests(unittest.TestCase):
    def test_creating_a_new_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ScraperState(Path(temp_dir) / "scraper_state.sqlite3")
            url = "https://history.burningman.org/art-history/archive/?yyyy=2022"

            lookup = state.get_or_create_source(url, validate_archive_url(url))

            self.assertTrue(lookup.created)
            self.assertEqual(lookup.source.normalized_url, url)
            self.assertEqual(lookup.source.detected_year, "2022")
            self.assertEqual(lookup.source.detected_collection, "archive")

    def test_recognizing_the_same_normalized_source_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ScraperState(Path(temp_dir) / "scraper_state.sqlite3")
            url = "https://history.burningman.org/art-history/archive/?yyyy=2022"

            first_lookup = state.get_or_create_source(url, validate_archive_url(url))
            second_lookup = state.get_or_create_source(url, validate_archive_url(url))

            self.assertTrue(first_lookup.created)
            self.assertFalse(second_lookup.created)
            self.assertEqual(first_lookup.source.source_id, second_lookup.source.source_id)

    def test_tracking_parameters_do_not_create_a_new_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ScraperState(Path(temp_dir) / "scraper_state.sqlite3")
            clean_url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
            tracked_url = (
                "https://history.burningman.org/art-history/archive/"
                "?utm_source=chatgpt.com&yyyy=2022"
            )

            first_lookup = state.get_or_create_source(clean_url, validate_archive_url(clean_url))
            second_lookup = state.get_or_create_source(tracked_url, validate_archive_url(tracked_url))

            self.assertEqual(first_lookup.source.source_id, second_lookup.source.source_id)
            connection = sqlite3.connect(state.database_path)
            try:
                source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(source_count, 1)

    def test_loading_prior_checkpoints(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ScraperState(Path(temp_dir) / "scraper_state.sqlite3")
            url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
            source_lookup = state.get_or_create_source(url, validate_archive_url(url))
            state.save_checkpoint(
                source_lookup.source.source_id,
                last_discovered_position=75,
                last_completed_position=50,
                last_exported_position=50,
            )

            loaded_lookup = state.get_or_create_source(url, validate_archive_url(url))

            self.assertIsNotNone(loaded_lookup.checkpoint)
            self.assertEqual(loaded_lookup.checkpoint.last_discovered_position, 75)
            self.assertEqual(loaded_lookup.checkpoint.last_completed_position, 50)
            self.assertEqual(loaded_lookup.checkpoint.last_exported_position, 50)

    def test_database_persistence_across_program_restarts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "scraper_state.sqlite3"
            url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
            first_state = ScraperState(database_path)
            first_lookup = first_state.get_or_create_source(url, validate_archive_url(url))
            first_state.save_checkpoint(first_lookup.source.source_id, last_completed_position=9)

            second_state = ScraperState(database_path)
            second_lookup = second_state.get_or_create_source(url, validate_archive_url(url))

            self.assertFalse(second_lookup.created)
            self.assertEqual(second_lookup.source.source_id, first_lookup.source.source_id)
            self.assertEqual(second_lookup.checkpoint.last_completed_position, 9)

    def test_canonical_move_frees_occupied_source_position(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = ScraperState(Path(temp_dir) / "scraper_state.sqlite3")
            url = "https://history.burningman.org/art-history/archive/?yyyy=2022"
            source = state.get_or_create_source(url, url).source
            state.mark_source_record_by_canonical(
                source.source_id,
                1,
                f"{url}#old",
                f"{url}#old",
                "old",
                "completed",
            )
            state.mark_source_record_by_canonical(
                source.source_id,
                2,
                f"{url}#new",
                f"{url}#new",
                "new",
                "completed",
            )

            state.mark_source_record_by_canonical(
                source.source_id,
                1,
                f"{url}#new",
                f"{url}#new",
                "new",
                "completed",
            )

            connection = sqlite3.connect(state.database_path)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """
                    SELECT canonical_installation_url, source_position
                    FROM source_records
                    WHERE source_id = ?
                    ORDER BY canonical_installation_url
                    """,
                    (source.source_id,),
                ).fetchall()
            finally:
                connection.close()

            positions = {row["canonical_installation_url"]: row["source_position"] for row in rows}
            self.assertEqual(positions[f"{url}#new"], 1)
            self.assertLess(positions[f"{url}#old"], 0)


if __name__ == "__main__":
    unittest.main()
