"""Evidence-based artist website extraction acceptance tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.sources.artist_website import ingest as artist_ingest
from burning_man_scraper.sources.artist_website.classify import classify_page
from burning_man_scraper.sources.artist_website.discover import (
    discover_collection_candidates,
    normalize_detail_url,
    score_detail_url,
)
from burning_man_scraper.sources.artist_website.extract import extract_detail_candidate
from burning_man_scraper.sources.artist_website.pipeline import extract_site_artworks
from burning_man_scraper.sources.artist_website.render import initial_render_reasons
from burning_man_scraper.artelier_schema import load_import_schema
from burning_man_scraper.sources.artelier_map import artist_internal_to_artelier36


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "artist_website"
SCHEMA = load_import_schema(PROJECT_ROOT / "config" / "artelier_import_schema.yaml")


def load_page(name: str, url: str) -> artist_ingest.Page:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return artist_ingest.parse_html(url, url, 200, "text/html", html)


class DetailUrlNormalizationTests(unittest.TestCase):
    def test_strips_fragment_tracking_and_slash(self):
        url = normalize_detail_url(
            "https://Example.com/artworks/281-work/?utm_source=x&fbclid=1&keep=1#section",
            "https://example.com/",
        )
        self.assertEqual(url, "https://example.com/artworks/281-work?keep=1")

    def test_score_rejects_cart_and_accepts_store_product(self):
        self.assertLess(score_detail_url("https://example.com/cart"), 0)
        self.assertGreaterEqual(
            score_detail_url("https://www.felipeortiz.com/store-2/p/oystercatcher-print"),
            4,
        )


class FelipeStoreAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = load_page(
            "felipe_store_collection.html",
            "https://www.felipeortiz.com/store-2",
        )

    def test_store_classified_as_collection(self):
        interpretation = classify_page(self.page, artist_name="Felipe Ortiz")
        self.assertEqual(interpretation.page_type, "artwork_collection")

    def test_oystercatcher_extracted_once_with_clean_title(self):
        candidates = discover_collection_candidates(self.page, artist_name="Felipe Ortiz")
        oyster = [c for c in candidates if "oystercatcher" in c.title.casefold()]
        self.assertEqual(len(oyster), 1)
        candidate = oyster[0]
        self.assertEqual(candidate.title, "Oystercatcher")
        self.assertNotIn("$", candidate.title)
        self.assertNotIn("200", candidate.title)
        self.assertTrue(candidate.metadata.get("dimensions"))
        self.assertIn("18", candidate.metadata["dimensions"])
        self.assertIn("/store-2/p/oystercatcher-print", candidate.detail_url)
        row = artist_ingest.candidate_to_row(
            artist_ingest.artwork_to_candidate(candidate),
            "Felipe Ortiz",
            "https://www.felipeortiz.com/",
        )
        self.assertEqual(row["project_type"], "Product / Object")
        self.assertNotIn("$", str(row["project_title"]))

    def test_nav_and_cart_are_not_records(self):
        candidates = discover_collection_candidates(self.page, artist_name="Felipe Ortiz")
        titles = {c.title.casefold() for c in candidates}
        self.assertNotIn("cart", titles)
        self.assertNotIn("contact", titles)
        self.assertNotIn("home", titles)


class ClaraAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.collection = load_page(
            "clara_collection.html",
            "https://claraberta.com/artworks-by-clara-berta/",
        )
        self.detail = load_page(
            "clara_detail_in_the_light.html",
            "https://claraberta.com/artworks/281-clara-berta-in-the-light-2025/",
        )

    def test_collection_provisional_candidates(self):
        interpretation = classify_page(self.collection, artist_name="Clara Berta")
        self.assertEqual(interpretation.page_type, "artwork_collection")
        candidates = interpretation.candidates
        self.assertGreaterEqual(len(candidates), 2)
        titles = {c.title for c in candidates}
        self.assertTrue(any("Light" in title for title in titles))
        for banned in (
            "View more details",
            "Sold",
            "Enquire",
            "Gallery",
            "Clara Berta",
        ):
            self.assertNotIn(banned, titles)

    def test_lazy_image_paired_with_alt(self):
        candidates = discover_collection_candidates(
            self.collection, artist_name="Clara Berta"
        )
        light = next(c for c in candidates if "Light" in c.title)
        self.assertTrue(light.images)
        self.assertFalse(light.images[0].url.startswith("data:"))
        self.assertIn("In the Light", light.images[0].alt)

    def test_detail_overrides_and_merge(self):
        merged = extract_site_artworks(
            [self.collection, self.detail],
            artist_name="Clara Berta",
        )
        light = [c for c in merged if c.title == "In the Light"]
        self.assertEqual(len(light), 1)
        candidate = light[0]
        self.assertEqual(candidate.year, "2025")
        self.assertIn("Acrylic", candidate.metadata.get("medium", ""))
        self.assertIn("Canvas", candidate.metadata.get("medium", ""))
        self.assertIn(
            "/artworks/281-clara-berta-in-the-light-2025",
            candidate.detail_url,
        )
        self.assertTrue(candidate.images)
        self.assertFalse(any(img.url.startswith("data:") for img in candidate.images))
        row = artist_ingest.candidate_to_row(
            artist_ingest.artwork_to_candidate(candidate),
            "Clara Berta",
            "https://claraberta.com/",
        )
        self.assertEqual(row["project_title"], "In the Light")
        self.assertEqual(row["year"], "2025")
        self.assertIn(
            "281-clara-berta-in-the-light-2025",
            str(row["proof_external_url"]),
        )

    def test_detail_page_extraction(self):
        detail = extract_detail_candidate(self.detail, artist_name="Clara Berta")
        self.assertEqual(detail.title, "In the Light")
        self.assertEqual(detail.year, "2025")
        self.assertIn("Bloom", detail.metadata.get("series", ""))
        self.assertEqual(detail.metadata.get("inventory"), "BERC/0250")


class RenderPolicyTests(unittest.TestCase):
    def test_lazy_placeholders_trigger_render_not_disagreement(self):
        page = load_page(
            "clara_collection.html",
            "https://claraberta.com/artworks-by-clara-berta/",
        )
        reasons = initial_render_reasons(page)
        self.assertIn("lazy_image_placeholders", reasons)
        self.assertNotIn("static_vs_rendered_disagree", reasons)


class CalebSquarespaceAcceptanceTests(unittest.TestCase):
    """Generic Squarespace portfolio acceptance using sanitized Caleb fixtures."""

    def setUp(self) -> None:
        self.collection = load_page(
            "caleb_work_collection.html",
            "https://www.calebhawkins.design/",
        )
        self.detail = load_page(
            "caleb_water_finds_a_way_detail.html",
            "https://www.calebhawkins.design/water-finds-a-way",
        )
        self.artist = "Caleb Hawkins"

    def test_homepage_classified_as_collection(self):
        interpretation = classify_page(self.collection, artist_name=self.artist)
        self.assertEqual(interpretation.page_type, "artwork_collection")

    def test_structural_cards_discovered_once_with_visible_titles(self):
        candidates = discover_collection_candidates(
            self.collection, artist_name=self.artist
        )
        by_path = {
            (c.detail_url or "").rstrip("/").split("/")[-1]: c for c in candidates if c.detail_url
        }
        self.assertIn("new-project-45", by_path)
        self.assertEqual(by_path["new-project-45"].title, "HELD IN TENSION")
        self.assertIn("water-finds-a-way", by_path)
        self.assertEqual(by_path["water-finds-a-way"].title, "WATER FINDS A WAY")
        self.assertIn("ascention", by_path)
        self.assertEqual(by_path["ascention"].title, "ASCENSION")
        self.assertNotEqual(by_path["ascention"].title.casefold(), "ascention")
        self.assertIn("ibili", by_path)
        self.assertEqual(by_path["ibili"].title, "FACADE INTERFACE")
        # Duplicate image/title anchors collapse to one candidate per detail URL
        self.assertEqual(
            len([c for c in candidates if c.detail_url and c.detail_url.endswith("/ibili")]),
            1,
        )
        banned = {"about", "press", "work", "caleb hawkins", "works"}
        titles = {c.title.casefold() for c in candidates}
        self.assertTrue(banned.isdisjoint(titles))
        for candidate in candidates:
            for image in candidate.images:
                self.assertNotIn("LOGO", image.url.upper())
                self.assertFalse(image.url.startswith("data:"))
            # data-src originals preferred over ?format= resize variants
            if candidate.images:
                self.assertNotIn("format=", candidate.images[0].url)

    def test_detail_page_and_merge(self):
        interpretation = classify_page(self.detail, artist_name=self.artist)
        self.assertEqual(interpretation.page_type, "editorial_project_detail")
        detail = extract_detail_candidate(self.detail, artist_name=self.artist)
        self.assertEqual(detail.title, "WATER FINDS A WAY")
        self.assertTrue(detail.images)
        self.assertTrue(all("LOGO" not in image.url.upper() for image in detail.images))
        self.assertTrue(
            any("water-1.jpg" in image.url and "format=" not in image.url for image in detail.images)
        )

        merged = extract_site_artworks(
            [self.collection, self.detail],
            artist_name=self.artist,
        )
        water = [c for c in merged if c.title == "WATER FINDS A WAY"]
        self.assertEqual(len(water), 1)
        candidate = water[0]
        self.assertIn("/water-finds-a-way", candidate.detail_url)
        self.assertFalse(candidate.year)
        self.assertFalse(candidate.metadata.get("dimensions"))
        self.assertFalse(candidate.metadata.get("medium"))
        row = artist_ingest.candidate_to_row(
            artist_ingest.artwork_to_candidate(candidate),
            self.artist,
            "https://www.calebhawkins.design/",
        )
        mapped = artist_internal_to_artelier36(row, list(SCHEMA.headers))
        self.assertEqual(mapped["contributor_name"], "Caleb Hawkins")
        self.assertEqual(mapped["contributor_website"], "https://www.calebhawkins.design/")
        self.assertIn("/water-finds-a-way", mapped["proof_external_url"])
        self.assertEqual(mapped["proof_title"], "WATER FINDS A WAY")
        self.assertEqual(mapped["project_title"], "WATER FINDS A WAY")
        self.assertEqual(mapped["project_year"], "")
        self.assertNotIn("LOGO", mapped["hero_image_url"].upper())
        self.assertTrue(mapped["hero_image_url"])

    def test_sparse_project_fixture_skips_logo_hero(self):
        page = load_page(
            "caleb_project.html",
            "https://www.calebhawkins.design/new-project-45",
        )
        entries = artist_ingest.extract_project_entries(page, artist_name=self.artist)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].image_urls)
        self.assertTrue(all("LOGO" not in url.upper() for url in entries[0].image_urls))
        export = artist_ingest.canonical_export_row(
            artist_ingest.candidate_to_row(
                entries[0], self.artist, "https://www.calebhawkins.design/"
            )
        )
        self.assertNotIn("LOGO", export["hero_image_url"].upper())
        self.assertIn("main-front.jpg", export["hero_image_url"])


class PublicApiRegressionTests(unittest.TestCase):
    def test_public_helpers_remain_importable(self):
        for name in (
            "normalize_url",
            "parse_html",
            "split_collection_page_entries",
            "extract_project_entries",
            "candidate_to_row",
            "crawl_site",
            "deduplicate_rows",
            "artwork_to_candidate",
        ):
            self.assertTrue(hasattr(artist_ingest, name))

    def test_cli_flags_unchanged(self):
        parser = artist_ingest.build_parser()
        actions = {action.dest for action in parser._actions}
        for required in (
            "artist",
            "url",
            "project_index",
            "template",
            "output_dir",
            "out",
            "max_pages",
            "delay",
            "timeout",
            "playwright",
        ):
            self.assertIn(required, actions)


if __name__ == "__main__":
    unittest.main()
