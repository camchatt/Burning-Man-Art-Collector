from dataclasses import replace
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.state import ApprovalContext, ScraperState


URL = "https://history.burningman.org/art-history/archive/?yyyy=2022"


def approval_context(source_id: int) -> ApprovalContext:
    return ApprovalContext(
        preview_run_id="preview-run-1",
        source_id=source_id,
        normalized_source_url=URL,
        proposed_batch_number=1,
        requested_count=10,
        preview_record_id="record-1",
        schema_version="installation-preview-v1",
        parser_version="phase4-installation-preview-v1",
        configuration_hash="configuration-hash",
        source_manifest_hash="manifest-hash",
    )


class PreviewApprovalTests(unittest.TestCase):
    def state_and_context(self):
        temp_dir = tempfile.TemporaryDirectory()
        state = ScraperState(Path(temp_dir.name) / "state.sqlite3")
        source_lookup = state.get_or_create_source(URL, URL)
        return temp_dir, state, approval_context(source_lookup.source.source_id)

    def test_valid_approval(self):
        temp_dir, state, context = self.state_and_context()
        with temp_dir:
            approval = state.save_preview_approval(context, "approved")
            export_batch_id = state.create_pending_export_batch(context)

            self.assertEqual(approval.approval_status, "approved")
            self.assertTrue(state.preview_approval_matches(context))
            self.assertGreater(export_batch_id, 0)

    def test_mismatched_url_invalidates_approval(self):
        temp_dir, state, context = self.state_and_context()
        with temp_dir:
            state.save_preview_approval(context, "approved")

            self.assertFalse(
                state.preview_approval_matches(
                    replace(context, normalized_source_url="https://history.burningman.org/art-history/archive/?yyyy=2023")
                )
            )

    def test_mismatched_run_id_invalidates_approval(self):
        temp_dir, state, context = self.state_and_context()
        with temp_dir:
            state.save_preview_approval(context, "approved")

            self.assertFalse(state.preview_approval_matches(replace(context, preview_run_id="other-run")))

    def test_changed_schema_invalidates_approval(self):
        temp_dir, state, context = self.state_and_context()
        with temp_dir:
            state.save_preview_approval(context, "approved")

            self.assertFalse(state.preview_approval_matches(replace(context, schema_version="new-schema")))

    def test_changed_parser_invalidates_approval(self):
        temp_dir, state, context = self.state_and_context()
        with temp_dir:
            state.save_preview_approval(context, "approved")

            self.assertFalse(state.preview_approval_matches(replace(context, parser_version="new-parser")))

    def test_changed_configuration_invalidates_approval(self):
        temp_dir, state, context = self.state_and_context()
        with temp_dir:
            state.save_preview_approval(context, "approved")

            self.assertFalse(
                state.preview_approval_matches(replace(context, configuration_hash="new-configuration"))
            )

    def test_changed_source_manifest_invalidates_approval(self):
        temp_dir, state, context = self.state_and_context()
        with temp_dir:
            state.save_preview_approval(context, "approved")

            self.assertFalse(
                state.preview_approval_matches(replace(context, source_manifest_hash="new-manifest"))
            )

    def test_changed_record_count_invalidates_approval(self):
        temp_dir, state, context = self.state_and_context()
        with temp_dir:
            state.save_preview_approval(context, "approved")

            self.assertFalse(state.preview_approval_matches(replace(context, requested_count=11)))

    def test_canceled_approval_does_not_create_pending_batch(self):
        temp_dir, state, context = self.state_and_context()
        with temp_dir:
            state.save_preview_approval(context, "canceled")

            self.assertFalse(state.preview_approval_matches(context))
            with self.assertRaisesRegex(ValueError, "without matching preview approval"):
                state.create_pending_export_batch(context)

            connection = sqlite3.connect(state.database_path)
            try:
                batch_count = connection.execute("SELECT COUNT(*) FROM export_batches").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(batch_count, 0)


if __name__ == "__main__":
    unittest.main()
