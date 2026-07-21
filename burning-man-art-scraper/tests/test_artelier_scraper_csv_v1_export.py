import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.exporters.artelier_scraper_csv_v1.contract import (
    ARTELIER_SCRAPER_CSV_V1,
    EXPORT_COLUMNS,
    STANDARD_COLUMNS,
)
from burning_man_scraper.exporters.artelier_scraper_csv_v1.map_row import map_bm_upload_row
from burning_man_scraper.exporters.artelier_scraper_csv_v1.validate import validate_export_row
from burning_man_scraper.exporters.artelier_scraper_csv_v1.write import export_year_to_scraper_v1


def _base_src(**overrides):
    row = {
        "project_title": "Example Piece",
        "project_slug": "example-piece",
        "project_type": "Art",
        "project_year": "2016",
        "project_location": "Black Rock City, NV",
        "project_summary": "A line, with commas, and\nbreaks.",
        "project_tags": "kinetic",
        "project_materials": "steel|LED",
        "project_fabrication_methods": "welding",
        "project_context_tags": "",
        "project_classification_confidence": "medium",
        "client_name": "",
        "hero_image_url": "https://example.org/a.jpg",
        "contributor_name": "Jane Smith",
        "contributor_slug": "jane-smith",
        "role_title": "Artist",
        "contributor_email": "",
        "contributor_website": "https://example.org/jane",
        "collaboration_status": "",
        "what_they_did": "Built the piece",
        "why_it_mattered": "Mattered on playa",
        "proof_title": "Archive page",
        "proof_external_url": "https://history.burningman.org/art-history/archive/?yyyy=2016#uid",
        "proof_description": "Credit line",
        "permission_status": "pending_permission",
        "bm_uid": "uid-1",
        "bm_year": "2016",
        "bm_event_name": "Burning Man",
        "playa_address": "Open Playa",
        "playa_latitude": "",
        "playa_longitude": "",
        "honorarium_status": "",
        "theme_camp": "",
        "installation_type": "Art",
        "source_artist_credit": "Jane Smith",
        "contributor_display_name": "Jane Smith",
        "additional_contributor_credits": "",
        "contributor_kind": "individual",
        "contributor_first_name": "Jane",
        "contributor_last_name": "Smith",
        "playa_name": "",
        "playa_name_confidence": "none",
        "bm_hero_image_source_url": "https://example.org/a.jpg",
        "hero_image_source_page": "https://history.burningman.org/art-history/archive/?yyyy=2016#uid",
        "hero_image_attribution": "",
        "hero_image_confidence": "medium",
        "review_flags": "",
        "source_provenance": "www|verification",
    }
    row.update(overrides)
    return row


class ArtelierScraperCsvV1ExportTests(unittest.TestCase):
    def test_person_with_alias(self):
        row = map_bm_upload_row(
            _base_src(
                contributor_kind="individual",
                contributor_display_name="Jane Smith",
                playa_name="Sparky",
                source_artist_credit='Jane "Sparky" Smith',
            )
        )
        self.assertEqual(row["contract_version"], ARTELIER_SCRAPER_CSV_V1)
        self.assertEqual(row["contributor_kind"], "person")
        self.assertEqual(row["artist_name"], "Jane Smith")
        self.assertEqual(row["artist_alias"], "Sparky")
        self.assertEqual(row["bm_artist_text_raw"], 'Jane "Sparky" Smith')
        errors, _ = validate_export_row(row)
        self.assertEqual(errors, [])

    def test_organization(self):
        row = map_bm_upload_row(
            _base_src(
                contributor_kind="organization",
                contributor_display_name="Studio Drift",
                source_artist_credit="Studio Drift",
                playa_name="",
            )
        )
        self.assertEqual(row["contributor_kind"], "organization")
        self.assertEqual(row["organization_name"], "Studio Drift")
        self.assertEqual(row["artist_name"], "")

    def test_collective_and_unknown(self):
        collective = map_bm_upload_row(
            _base_src(contributor_kind="collective", contributor_display_name="The Crew")
        )
        unknown = map_bm_upload_row(
            _base_src(contributor_kind="unknown", contributor_display_name="", source_artist_credit="")
        )
        self.assertEqual(collective["contributor_kind"], "collective")
        self.assertEqual(collective["organization_name"], "The Crew")
        self.assertEqual(unknown["contributor_kind"], "unknown")

    def test_invalid_url_blanked_and_missing_title_can_still_pass_via_proof(self):
        row = map_bm_upload_row(
            _base_src(
                project_title="",
                contributor_website="notaurl",
                hero_image_url="ftp://bad.example/x.jpg",
                proof_external_url="https://example.org/proof",
            )
        )
        self.assertEqual(row["artist_website"], "")
        self.assertEqual(row["image_urls"], "")
        self.assertEqual(row["proof_external_url"], "https://example.org/proof")
        errors, _ = validate_export_row(row)
        self.assertEqual(errors, [])

    def test_multiple_images_pipe_and_description_commas(self):
        row = map_bm_upload_row(_base_src(proof_description=""))
        self.assertIn("commas", row["proof_excerpt"])
        self.assertIn("\n", row["proof_excerpt"])
        self.assertEqual(row["image_urls"], "https://example.org/a.jpg")
        self.assertEqual(row["materials"], "steel|LED")

    def test_image_url_with_spaces_is_percent_encoded(self):
        dirty = (
            "https://cdn.example/img/burningman/ag0kgshcsw/640px/"
            "a2Id0000001IR0aEAG Makhalych (aka Birding Man) Image 2.jpeg?u=r3rtjx"
        )
        row = map_bm_upload_row(_base_src(hero_image_url=dirty, bm_hero_image_source_url=dirty))
        self.assertNotIn(" ", row["image_urls"])
        self.assertIn("%20", row["image_urls"])
        self.assertEqual(row["image_urls"], row["bm_hero_image_source_url"])
        # Idempotent: already-encoded URLs stay valid.
        again = map_bm_upload_row(
            _base_src(hero_image_url=row["image_urls"], bm_hero_image_source_url=row["image_urls"])
        )
        self.assertEqual(again["image_urls"], row["image_urls"])
        errors, _ = validate_export_row(row)
        self.assertEqual(errors, [])

    def test_export_writes_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            year = 2016
            contracts = root / "contracts" / "artelier-scraper-v1"
            contracts.mkdir(parents=True)
            # Point checksum verification at a stub matching itself.
            schema = contracts / "schema.json"
            schema.write_text('{"title":"stub"}', encoding="utf-8")
            import hashlib

            digest = hashlib.sha256(schema.read_bytes()).hexdigest()
            (contracts / "contract.json").write_text(
                f'{{"contract":"artelier_scraper_csv_v1","schema_sha256":"{digest}"}}',
                encoding="utf-8",
            )

            ingest = root / "data" / "bm_ingest" / str(year)
            ingest.mkdir(parents=True)
            source = ingest / f"artelier_bm_upload_{year}.csv"
            rows = [
                _base_src(),
                _base_src(
                    project_title="Org Work",
                    project_slug="org-work",
                    bm_uid="uid-org",
                    contributor_kind="organization",
                    contributor_display_name="Studio Drift",
                    source_artist_credit="Studio Drift",
                    playa_name="",
                ),
                _base_src(
                    project_title="",
                    project_slug="no-title",
                    bm_uid="",
                    proof_title="",
                    proof_external_url="not-a-url",
                    proof_description="",
                    hero_image_url="",
                    hero_image_source_page="",
                    contributor_name="",
                    contributor_display_name="",
                    source_artist_credit="",
                    contributor_kind="unknown",
                    contributor_website="notaurl",
                ),
            ]
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)

            out = root / "out"
            summary = export_year_to_scraper_v1(root, year=year, output_root=out)
            self.assertEqual(summary["accepted_count"], 2)
            self.assertEqual(summary["rejected_count"], 1)
            upload = Path(summary["output_files"]["upload"])
            self.assertTrue(upload.exists())
            with upload.open(encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader)
            self.assertEqual(header[: len(STANDARD_COLUMNS)], list(STANDARD_COLUMNS))
            self.assertEqual(header, list(EXPORT_COLUMNS))
            self.assertTrue(Path(summary["output_files"]["compatibility_report"]).exists())


if __name__ == "__main__":
    unittest.main()
