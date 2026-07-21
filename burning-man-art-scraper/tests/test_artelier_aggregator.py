"""Adapter contract + artist-site + Artelier Aggregator API tests."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.artelier_schema import load_import_schema
from burning_man_scraper.sources.artelier_map import artist_internal_to_artelier36, artelier_headers
from burning_man_scraper.sources.artist_website import ArtistWebsiteAdapter, internal_row_to_normalized
from burning_man_scraper.sources.artist_website import ingest as artist_ingest
from burning_man_scraper.sources.base import NormalizedRecord
from burning_man_scraper.sources.burning_man_csv import bm_row_to_normalized
from burning_man_scraper.sources.registry import get_adapter, list_sources
from burning_man_scraper.sources.run_store import (
    apply_record_corrections,
    create_run,
    load_normalized_records,
    resolve_run_csv,
    write_artelier_outputs,
    write_normalized_records,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "artist_website"
SCHEMA = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")


class SourceRegistryTests(unittest.TestCase):
    def test_lists_both_initial_adapters(self):
        ids = {source.id for source in list_sources()}
        self.assertEqual(ids, {"artist_website", "burning_man_csv"})
        self.assertEqual(get_adapter("artist_website").descriptor.label, "Artist website")
        self.assertEqual(get_adapter("burning_man_csv").descriptor.label, "Burning Man CSV")


class AdapterContractTests(unittest.TestCase):
    def _assert_normalized_shape(self, record: NormalizedRecord) -> None:
        self.assertTrue(record.record_id)
        self.assertIn(record.source_id, {"artist_website", "burning_man_csv"})
        self.assertIsInstance(record.project_title.value, str)
        self.assertIn(record.project_title.status, {"sourced", "inferred", "missing", "conflicting", "corrected"})
        self.assertEqual(set(record.artelier_row) & set(SCHEMA.headers), set(SCHEMA.headers) & set(record.artelier_row))
        for header in SCHEMA.headers:
            self.assertIn(header, record.artelier_row)

    def test_artist_and_bm_emit_same_normalized_structure(self):
        page = artist_ingest.parse_html(
            "https://www.felipeortiz.com/murals",
            "https://www.felipeortiz.com/murals",
            200,
            "text/html",
            (FIXTURES / "felipe_collection.html").read_text(encoding="utf-8"),
        )
        internal = artist_ingest.candidate_to_row(
            artist_ingest.split_collection_page_entries(page)[0],
            "Felipe Ortiz",
            "https://www.felipeortiz.com/",
        )
        artist_norm = internal_row_to_normalized(internal, list(SCHEMA.headers))
        bm_row = {header: "" for header in SCHEMA.headers}
        bm_row.update(
            {
                "project_title": "Temple of Honor",
                "project_slug": "temple-of-honor",
                "proof_external_url": "https://history.burningman.org/x",
                "contributor_name": "Example Artist",
                "project_visibility": "private",
                "contributor_visibility": "private",
                "contribution_visibility": "private",
                "proof_visibility": "private",
                "verification_status": "documented",
                "approval_status": "draft",
                "permission_status": "pending_permission",
                "bm_uid": "abc",
                "review_flags": "",
            }
        )
        bm_norm = bm_row_to_normalized(bm_row, list(SCHEMA.headers))
        self._assert_normalized_shape(artist_norm)
        self._assert_normalized_shape(bm_norm)
        self.assertEqual(set(artist_norm.to_dict()), set(bm_norm.to_dict()))


class ArtistSiteIngestTests(unittest.TestCase):
    def fixture_page(self, name: str, url: str) -> artist_ingest.Page:
        html = (FIXTURES / name).read_text(encoding="utf-8")
        return artist_ingest.parse_html(url, url, 200, "text/html", html)

    def test_normalize_and_internal_urls(self):
        self.assertEqual(
            artist_ingest.normalize_url(
                "HTTPS://WWW.Example.com/work/?utm_source=x&itemId=modal123&b=2&a=1"
            ),
            "https://www.example.com/work?a=1&b=2",
        )
        self.assertTrue(
            artist_ingest.is_internal_url("https://example.com/work", "https://www.example.com")
        )
        self.assertFalse(
            artist_ingest.is_internal_url("https://other.example/work", "https://example.com")
        )

    def test_collection_page_splits_and_maps_collaborators(self):
        page = self.fixture_page("felipe_collection.html", "https://www.felipeortiz.com/murals")
        entries = artist_ingest.split_collection_page_entries(page)
        self.assertEqual([entry.title for entry in entries], ["Umana Rising", "The Bee"])
        first = artist_ingest.candidate_to_row(entries[0], "Felipe Ortiz", "https://www.felipeortiz.com/")
        self.assertEqual(first["project_type"], "Public Art")
        self.assertEqual(first["year"], "2024")
        second = artist_ingest.candidate_to_row(entries[1], "Felipe Ortiz", "https://www.felipeortiz.com/")
        self.assertEqual(second["collaboration_status"], "Collaborative project")
        self.assertEqual(second["collaborators"], "Adam O'Day")

    def test_sparse_individual_page_is_image_only(self):
        page = self.fixture_page(
            "caleb_project.html", "https://www.calebhawkins.design/new-project-45"
        )
        entries = artist_ingest.extract_project_entries(page, artist_name="Caleb Hawkins")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].source_granularity, "Image-only inference")
        self.assertTrue(entries[0].image_urls)
        self.assertTrue(all("LOGO" not in url.upper() for url in entries[0].image_urls))
        row = artist_ingest.candidate_to_row(
            entries[0], "Caleb Hawkins", "https://www.calebhawkins.design/"
        )
        self.assertEqual(row["proof_confidence"], "Low")
        self.assertEqual(row["review_status"], "Needs review")
        export = artist_ingest.canonical_export_row(row)
        self.assertNotIn("LOGO", export["hero_image_url"].upper())
        self.assertIn("main-front.jpg", export["hero_image_url"])

    def test_duplicate_projects_are_collapsed(self):
        row = {
            "project_title": "Island",
            "proof_external_url": "https://claraberta.com/artworks/island",
            "proof_excerpt": "short",
            "image_urls": ["https://example.com/a.jpg"],
        }
        richer = dict(row)
        richer["proof_excerpt"] = "much longer excerpt with materials"
        richer["image_urls"] = ["https://example.com/a.jpg", "https://example.com/b.jpg"]
        logs: list[artist_ingest.LogEntry] = []
        out = artist_ingest.deduplicate_rows([row, richer], logs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["proof_excerpt"], richer["proof_excerpt"])
        self.assertTrue(any(entry.action == "deduplicate" for entry in logs))

    def test_artelier_36_headers_exact(self):
        page = self.fixture_page("felipe_collection.html", "https://www.felipeortiz.com/murals")
        internal = artist_ingest.candidate_to_row(
            artist_ingest.split_collection_page_entries(page)[0],
            "Felipe Ortiz",
            "https://www.felipeortiz.com/",
        )
        mapped = artist_internal_to_artelier36(internal, list(SCHEMA.headers))
        self.assertEqual(list(mapped.keys()), list(SCHEMA.headers))
        self.assertEqual(mapped["project_visibility"], "private")
        self.assertEqual(mapped["approval_status"], "draft")
        self.assertEqual(mapped["verification_status"], "documented")
        self.assertEqual(mapped["permission_status"], "pending_permission")
        self.assertIn("acrylic paint", mapped["project_materials"])
        self.assertNotIn("public_reference_url", mapped)

    def test_crawl_failure_is_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                artist_ingest,
                "fetch_page",
                side_effect=artist_ingest.requests.RequestException("boom"),
            ):
                with mock.patch.object(artist_ingest, "robots_parser") as robots:
                    robots.return_value.can_fetch.return_value = True
                    pages, logs = artist_ingest.crawl_site(
                        "https://example.com/",
                        None,
                        Path(tmp),
                        max_pages=1,
                        delay=0,
                        timeout=1,
                    )
        self.assertEqual(pages, [])
        self.assertTrue(any(entry.status == "failed" for entry in logs))


class ArtistPrepareE2ETests(unittest.TestCase):
    def test_artist_fixture_prepare_review_export(self):
        page = artist_ingest.parse_html(
            "https://www.felipeortiz.com/murals",
            "https://www.felipeortiz.com/murals",
            200,
            "text/html",
            (FIXTURES / "felipe_collection.html").read_text(encoding="utf-8"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "artelier_import_schema.yaml").write_text(
                (PROJECT_ROOT / "config" / "artelier_import_schema.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            adapter = ArtistWebsiteAdapter()
            result = adapter.prepare(
                project_root=root,
                artist_name="Felipe Ortiz",
                website_url="https://www.felipeortiz.com/",
                pages=[page],
            )
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["project_count"], 2)
            run_id = result["run_id"]
            csv_path = resolve_run_csv(root, run_id)
            self.assertIsNotNone(csv_path)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, list(SCHEMA.headers))
                rows = list(reader)
            self.assertGreaterEqual(len(rows), 2)

            # Correction path clears sparse flags when present and rewrites outputs.
            records = load_normalized_records(root / "data" / "runs" / run_id)
            record_id = records[0]["record_id"]
            updated = apply_record_corrections(
                root,
                run_id,
                record_id=record_id,
                corrections={
                    "project_title": rows[0]["project_title"],
                    "contributor_name": "Felipe Ortiz",
                    "project_year": "2024",
                    "approval_status": "approved",
                },
            )
            self.assertEqual(updated["approval_status"], "approved")


class BurningManNormalizedRegressionTests(unittest.TestCase):
    def test_bm_adapter_preserves_core_headers(self):
        headers = list(SCHEMA.headers)
        row = {header: "" for header in headers}
        row.update(
            {
                "project_title": "Night Wave",
                "project_slug": "night-wave",
                "proof_external_url": "https://history.burningman.org/night-wave",
                "contributor_name": "Ada",
                "project_visibility": "private",
                "contributor_visibility": "private",
                "contribution_visibility": "private",
                "proof_visibility": "private",
                "bm_uid": "uid-1",
                "review_flags": "hero_missing",
            }
        )
        normalized = bm_row_to_normalized(row, headers)
        self.assertEqual(normalized.source_id, "burning_man_csv")
        self.assertEqual(normalized.artelier_row["project_slug"], "night-wave")
        self.assertIn("hero_missing", normalized.review_flags)


class RunStoreApiShapeTests(unittest.TestCase):
    def test_write_and_reload_run_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "artelier_import_schema.yaml").write_text(
                (PROJECT_ROOT / "config" / "artelier_import_schema.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            path = create_run(
                root,
                source_id="artist_website",
                label="Clara Berta",
                input_summary={"website_url": "https://claraberta.com/"},
            )
            headers = artelier_headers(root)
            row = {header: "" for header in headers}
            row.update(
                {
                    "project_title": "Island",
                    "project_slug": "island",
                    "proof_external_url": "https://claraberta.com/artworks/island",
                    "contributor_name": "Clara Berta",
                    "project_visibility": "private",
                    "contributor_visibility": "private",
                    "contribution_visibility": "private",
                    "proof_visibility": "private",
                    "verification_status": "documented",
                    "approval_status": "draft",
                    "permission_status": "pending_permission",
                    "review_flags": "",
                }
            )
            record = bm_row_to_normalized(row, headers)
            record.source_id = "artist_website"
            write_normalized_records(path, [record])
            paths = write_artelier_outputs(
                path,
                root,
                artelier_rows=[row],
                label="Clara Berta",
                unsupported_relationships=[{"collaborators": "Guest"}],
            )
            self.assertTrue(paths["core"].exists())
            view = json.loads(paths["view"].read_text(encoding="utf-8"))
            self.assertEqual(view["meta"]["run_id"], path.name)
            self.assertEqual(view["projects"][0]["title"], "Island")


class HubHandlerApiTests(unittest.TestCase):
    def test_sources_and_inspect_artist_endpoints(self):
        from burning_man_scraper.aggregator_hub.server import create_handler
        from http.server import ThreadingHTTPServer
        import threading
        import urllib.request

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "viewer" / "aggregator").mkdir(parents=True)
            (root / "viewer" / "aggregator" / "index.html").write_text("<html></html>", encoding="utf-8")
            (root / "config").mkdir()
            (root / "config" / "artelier_import_schema.yaml").write_text(
                (PROJECT_ROOT / "config" / "artelier_import_schema.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (root / "config" / "artelier_deploy.yaml").write_text(
                json.dumps({"hub_port": 0, "cleanup_tmp_on_success": True}),
                encoding="utf-8",
            )
            handler = create_handler(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/sources") as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertEqual({item["id"] for item in payload["sources"]}, {"artist_website", "burning_man_csv"})

                boundary = "----bound"
                body = (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="source_id"\r\n\r\n'
                    "artist_website\r\n"
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="artist_name"\r\n\r\n'
                    "Clara Berta\r\n"
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="website_url"\r\n\r\n'
                    "https://claraberta.com/\r\n"
                    f"--{boundary}--\r\n"
                ).encode("utf-8")
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/inspect",
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    inspect_payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(inspect_payload["ok"])
                self.assertIn("claraberta.com", inspect_payload["message"])
            finally:
                server.shutdown()


if __name__ == "__main__":
    unittest.main()
