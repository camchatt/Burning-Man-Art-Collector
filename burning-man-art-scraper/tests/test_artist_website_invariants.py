"""Property-style invariants for artist-website extraction."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.artelier_schema import load_import_schema
from burning_man_scraper.sources.artist_website import ArtistWebsiteAdapter
from burning_man_scraper.sources.artist_website import ingest as artist_ingest
from burning_man_scraper.sources.artist_website.audit import SCHEMA_VERSION
from burning_man_scraper.sources.artist_website.classify import classify_page
from burning_man_scraper.sources.artist_website.discover import discover_collection_candidates
from burning_man_scraper.sources.artist_website.extract import extract_detail_candidate
from burning_man_scraper.sources.artist_website.pipeline import extract_site_artworks
from burning_man_scraper.sources.artelier_map import artelier_headers, artist_internal_to_artelier36


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "artist_website"
SCHEMA = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")
SRC_ROOT = PROJECT_ROOT / "src" / "burning_man_scraper" / "sources"

NAV_BANNED = {
    "about",
    "contact",
    "press",
    "cart",
    "home",
    "work",
    "gallery",
    "menu",
}

FIXTURE_NAME_PATTERN = re.compile(
    r"clara\s*berta|caleb\s*hawkins|felipe\s*ortiz|claraberta|calebhawkins|felipeortiz",
    re.I,
)


def load_page(name: str, url: str) -> artist_ingest.Page:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return artist_ingest.parse_html(url, url, 200, "text/html", html)


class InvariantHelpers:
    @staticmethod
    def assert_unique_detail_urls(test: unittest.TestCase, candidates) -> None:
        urls = [c.detail_url for c in candidates if c.detail_url]
        test.assertEqual(len(urls), len(set(urls)), msg=f"duplicate detail URLs: {urls}")

    @staticmethod
    def assert_every_candidate_has_evidence(test: unittest.TestCase, candidates) -> None:
        for candidate in candidates:
            test.assertTrue(
                candidate.evidence or candidate.images or candidate.title,
                msg=f"candidate lacks evidence: {candidate.title!r}",
            )
            if candidate.title:
                title_ev = [e for e in candidate.evidence if e.field == "title"]
                test.assertTrue(
                    title_ev or "title_inferred_from_alt" in candidate.review_flags
                    or "title_inferred_from_slug" in candidate.review_flags
                    or candidate.source_granularity,
                    msg=f"title without provenance: {candidate.title!r}",
                )

    @staticmethod
    def assert_inferred_fields_flagged(test: unittest.TestCase, candidates) -> None:
        for candidate in candidates:
            for item in candidate.evidence:
                if item.source_kind in {"url_slug", "image_alt"} and item.field == "title":
                    test.assertTrue(
                        any(
                            flag.startswith("title_inferred")
                            for flag in candidate.review_flags
                        ),
                        msg=f"inferred title missing flag: {candidate.title!r}",
                    )


class FixtureMatrixTests(unittest.TestCase):
    def test_shopify_collection_and_detail_merge(self):
        collection = load_page(
            "shopify_collection.html", "https://shop.example.com/collections/all"
        )
        detail = load_page(
            "shopify_detail_aurora.html", "https://shop.example.com/products/aurora-print"
        )
        merged = extract_site_artworks([collection, detail], artist_name="Example Artist")
        aurora = [c for c in merged if "aurora" in c.title.casefold()]
        self.assertEqual(len(aurora), 1)
        self.assertIn("/products/aurora-print", aurora[0].detail_url)
        self.assertNotIn("utm_", aurora[0].detail_url)

    def test_wordpress_grid(self):
        page = load_page("wordpress_grid.html", "https://example.com/works/")
        candidates = discover_collection_candidates(page, artist_name="Example Artist")
        titles = {c.title for c in candidates}
        self.assertIn("Red Field", titles)
        InvariantHelpers.assert_unique_detail_urls(self, candidates)

    def test_webflow_list(self):
        page = load_page("webflow_list.html", "https://example.com/projects")
        candidates = discover_collection_candidates(page, artist_name="Example Artist")
        self.assertTrue(any("Kinetic" in c.title for c in candidates))

    def test_json_ld_detail_preserves_accents(self):
        page = load_page("json_ld_detail.html", "https://example.com/artworks/etude-3")
        detail = extract_detail_candidate(page, artist_name="Example Artist")
        self.assertEqual(detail.title, "Étude Nº 3")
        self.assertEqual(detail.year, "2022")

    def test_og_only_sparse(self):
        page = load_page("og_only_sparse.html", "https://example.com/cafe-neon")
        detail = extract_detail_candidate(page, artist_name="Example Artist")
        self.assertEqual(detail.title, "Café Néon")
        dims = detail.metadata.get("dimensions") or ""
        self.assertTrue(dims)
        self.assertNotIn("Ã—", dims)

    def test_caption_only_murals(self):
        page = load_page("caption_only_murals.html", "https://example.com/murals")
        candidates = discover_collection_candidates(page, artist_name="Example Artist")
        titles = {c.title for c in candidates}
        self.assertTrue(any("Sunrise" in t for t in titles))
        self.assertTrue(any(not c.detail_url for c in candidates))

    def test_linkless_figures(self):
        page = load_page("linkless_figures.html", "https://example.com/gallery")
        candidates = discover_collection_candidates(page, artist_name="Example Artist")
        self.assertTrue(any("Quiet Room" in c.title for c in candidates))

    def test_js_placeholders_prefer_data_src(self):
        page = load_page("js_placeholders.html", "https://example.com/")
        candidates = discover_collection_candidates(page, artist_name="Example Artist")
        self.assertTrue(candidates)
        for candidate in candidates:
            for image in candidate.images:
                self.assertFalse(image.url.startswith("data:"))

    def test_nav_must_not_emit(self):
        page = load_page("nav_utility.html", "https://example.com/")
        interpretation = classify_page(page, artist_name="Example Artist")
        self.assertIn(interpretation.page_type, {"navigation", "irrelevant", "commerce_utility", "unknown"})
        candidates = discover_collection_candidates(page, artist_name="Example Artist")
        titles = {c.title.casefold() for c in candidates}
        self.assertTrue(NAV_BANNED.isdisjoint(titles))

    def test_related_grid_must_not_bleed(self):
        page = load_page(
            "related_grid_detail.html", "https://example.com/artworks/main-piece"
        )
        interpretation = classify_page(page, artist_name="Example Artist")
        self.assertIn(
            interpretation.page_type,
            {"artwork_detail", "editorial_project_detail"},
        )
        detail = extract_detail_candidate(page, artist_name="Example Artist")
        self.assertEqual(detail.title, "Main Piece")
        # Related titles must not become the primary title
        self.assertNotEqual(detail.title.casefold(), "other one")

    def test_duplicate_cards_cdn_collapse(self):
        page = load_page("duplicate_cdn_cards.html", "https://example.com/")
        candidates = discover_collection_candidates(page, artist_name="Example Artist")
        echo = [c for c in candidates if c.title.casefold() == "echo"]
        self.assertEqual(len(echo), 1)
        self.assertNotIn("utm_", echo[0].detail_url or "")
        self.assertNotIn("format=", (echo[0].images[0].url if echo[0].images else ""))


class PipelineInvariantTests(unittest.TestCase):
    def test_nav_titles_never_exported_from_existing_fixtures(self):
        for name, url, artist in (
            ("clara_collection.html", "https://claraberta.com/artworks-by-clara-berta/", "Clara Berta"),
            ("caleb_work_collection.html", "https://www.calebhawkins.design/", "Caleb Hawkins"),
            ("felipe_store_collection.html", "https://www.felipeortiz.com/store-2", "Felipe Ortiz"),
        ):
            page = load_page(name, url)
            candidates = discover_collection_candidates(page, artist_name=artist)
            titles = {c.title.casefold() for c in candidates}
            self.assertTrue(NAV_BANNED.isdisjoint(titles), msg=f"{name}: {titles & NAV_BANNED}")

    def test_weak_collection_never_overrides_detail(self):
        collection = load_page(
            "clara_collection.html",
            "https://claraberta.com/artworks-by-clara-berta/",
        )
        detail = load_page(
            "clara_detail_in_the_light.html",
            "https://claraberta.com/artworks/281-clara-berta-in-the-light-2025/",
        )
        merged = extract_site_artworks([collection, detail], artist_name="Clara Berta")
        light = next(c for c in merged if c.title == "In the Light")
        self.assertEqual(light.year, "2025")
        self.assertIn("Acrylic", light.metadata.get("medium", ""))

    def test_merge_one_entity_per_detail_url(self):
        collection = load_page(
            "caleb_work_collection.html",
            "https://www.calebhawkins.design/",
        )
        detail = load_page(
            "caleb_water_finds_a_way_detail.html",
            "https://www.calebhawkins.design/water-finds-a-way",
        )
        merged = extract_site_artworks([collection, detail], artist_name="Caleb Hawkins")
        water = [c for c in merged if "water" in c.title.casefold()]
        self.assertEqual(len(water), 1)

    def test_every_candidate_has_evidence_and_flags_for_inferred(self):
        page = load_page(
            "clara_collection.html",
            "https://claraberta.com/artworks-by-clara-berta/",
        )
        candidates = discover_collection_candidates(page, artist_name="Clara Berta")
        InvariantHelpers.assert_every_candidate_has_evidence(self, candidates)
        InvariantHelpers.assert_inferred_fields_flagged(self, candidates)

    def test_page_extraction_audit_written(self):
        collection = load_page(
            "shopify_collection.html", "https://shop.example.com/collections/all"
        )
        detail = load_page(
            "shopify_detail_aurora.html", "https://shop.example.com/products/aurora-print"
        )
        with tempfile.TemporaryDirectory() as tmp:
            run_path = Path(tmp)
            extract_site_artworks(
                [collection, detail],
                artist_name="Example Artist",
                run_path=run_path,
            )
            audit_path = run_path / "page_extraction_audit.json"
            self.assertTrue(audit_path.exists())
            payload = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
            self.assertGreaterEqual(payload["page_count"], 2)
            page0 = payload["pages"][0]
            self.assertIn("classification", page0)
            self.assertIn("render_recommendation", page0)
            self.assertIn("accept", page0)
            for candidate in payload["merged_candidates"]:
                self.assertIn("confidence", candidate)
                self.assertIn("review_flags", candidate)
                self.assertIn("field_provenance", candidate)

    def test_artelier_36_headers_frozen(self):
        headers = artelier_headers(PROJECT_ROOT)
        self.assertEqual(headers, list(SCHEMA.headers))
        self.assertEqual(len(headers), 36)
        page = load_page(
            "json_ld_detail.html", "https://example.com/artworks/etude-3"
        )
        detail = extract_detail_candidate(page, artist_name="Example Artist")
        row = artist_ingest.candidate_to_row(
            artist_ingest.artwork_to_candidate(detail),
            "Example Artist",
            "https://example.com/",
        )
        mapped = artist_internal_to_artelier36(row, headers)
        self.assertEqual(list(mapped.keys()), headers)
        # No fabricated empty unsupported fields forced into required columns beyond defaults
        self.assertEqual(mapped["project_title"], "Étude Nº 3")

    def test_no_fixture_names_in_production_rules(self):
        offenders: list[str] = []
        scan_roots = [
            SRC_ROOT / "artist_website",
            SRC_ROOT / "artelier_map.py",
            SRC_ROOT / "base.py",
            SRC_ROOT / "registry.py",
        ]
        for root in scan_roots:
            paths = [root] if root.is_file() else list(root.rglob("*.py"))
            for path in paths:
                text = path.read_text(encoding="utf-8")
                if FIXTURE_NAME_PATTERN.search(text):
                    offenders.append(str(path.relative_to(PROJECT_ROOT)))
        self.assertEqual(offenders, [], msg=f"fixture names in production: {offenders}")

    def test_public_apis_green(self):
        for name in (
            "normalize_url",
            "parse_html",
            "extract_project_entries",
            "candidate_to_row",
            "artwork_to_candidate",
            "deduplicate_rows",
        ):
            self.assertTrue(hasattr(artist_ingest, name))
        self.assertTrue(callable(ArtistWebsiteAdapter().inspect))
        self.assertTrue(callable(ArtistWebsiteAdapter().prepare))

    def test_arbitrary_path_needs_evidence(self):
        html = """
        <html><body>
          <a href="/random/path/xyz">xyz</a>
        </body></html>
        """
        page = artist_ingest.parse_html(
            "https://example.com/", "https://example.com/", 200, "text/html", html
        )
        candidates = discover_collection_candidates(page, artist_name="Example Artist")
        # Bare arbitrary path without card evidence should not emit
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
