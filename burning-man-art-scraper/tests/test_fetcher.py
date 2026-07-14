from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.fetcher import BoundedFetcher, robots_url_for


class FetcherTests(unittest.TestCase):
    def test_robots_url_for_source(self):
        self.assertEqual(
            robots_url_for("https://history.burningman.org/art-history/archive/?yyyy=2022"),
            "https://history.burningman.org/robots.txt",
        )

    def test_refuses_fetch_outside_allowed_boundary(self):
        fetcher = BoundedFetcher(
            user_agent="test",
            delay_seconds=0,
            timeout_seconds=1,
            max_retries=0,
            sleep_func=lambda _seconds: None,
        )

        with self.assertRaisesRegex(ValueError, "outside crawl boundary"):
            fetcher.fetch(
                "https://history.burningman.org/art-history/archive/?yyyy=2021",
                allowed_urls={"https://history.burningman.org/art-history/archive/?yyyy=2022"},
            )


if __name__ == "__main__":
    unittest.main()
