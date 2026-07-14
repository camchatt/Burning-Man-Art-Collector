import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.identity.alias_resolver import extract_alias_linked_names


class AliasLinkExtractionTests(unittest.TestCase):
    def test_aka_legal_to_alias(self):
        people = extract_alias_linked_names(
            "John Hundt, also known as fnnch, paints murals at Burning Man.",
            alias="fnnch",
            require_burn_context=True,
        )
        self.assertEqual(people[0].name, "John Hundt")
        self.assertGreaterEqual(people[0].confidence, 0.9)

    def test_alias_aka_legal(self):
        people = extract_alias_linked_names(
            "Runester aka Robert Nelson builds oracles on the playa.",
            alias="Runester",
            require_burn_context=True,
        )
        self.assertEqual(people[0].name, "Robert Nelson")

    def test_ignores_unrelated_names(self):
        people = extract_alias_linked_names(
            "Larry Harvey founded Burning Man. fnnch makes hearts.",
            alias="fnnch",
            require_burn_context=True,
        )
        self.assertEqual(people, [])

    def test_rejects_song_translation_noise(self):
        people = extract_alias_linked_names(
            "Wildflower aka Amazon Music Songline lyrics translation",
            alias="Wildflower",
        )
        self.assertEqual(people, [])

    def test_requires_burn_context_when_asked(self):
        people = extract_alias_linked_names(
            "Firefly aka Garfield Lynns is a comic villain.",
            alias="Firefly",
            require_burn_context=True,
        )
        self.assertEqual(people, [])


if __name__ == "__main__":
    unittest.main()
