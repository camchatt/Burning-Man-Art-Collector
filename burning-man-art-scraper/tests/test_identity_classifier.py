import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.identity.classifier import classify_archive_credit


class CreditClassifierTests(unittest.TestCase):
    def test_aka_legal_then_playa(self):
        result = classify_archive_credit("Eric Tussey a.k.a. pebble")
        self.assertEqual(result.legal_name, "Eric Tussey")
        self.assertEqual(result.playa_name, "pebble")
        self.assertEqual(result.playa_name_confidence, "high")

    def test_aka_playa_then_legal(self):
        result = classify_archive_credit("wizzard aka Bob Marzewski")
        self.assertEqual(result.legal_name, "Bob Marzewski")
        self.assertEqual(result.playa_name, "wizzard")
        self.assertEqual(result.playa_name_confidence, "high")

    def test_aka_with_comma(self):
        result = classify_archive_credit("Sarah Gonsalves, aka Sassy Galaxy")
        self.assertEqual(result.legal_name, "Sarah Gonsalves")
        self.assertEqual(result.playa_name, "Sassy Galaxy")

    def test_parenthetical_nickname(self):
        result = classify_archive_credit("Dan Barnes (Tinker)")
        self.assertEqual(result.legal_name, "Dan Barnes")
        self.assertEqual(result.playa_name, "Tinker")
        self.assertEqual(result.playa_name_confidence, "high")

    def test_mid_parenthetical_nickname(self):
        result = classify_archive_credit("Kristen (Kilowatt) Williams")
        self.assertEqual(result.legal_name, "Kristen Williams")
        self.assertEqual(result.playa_name, "Kilowatt")

    def test_role_parenthetical_not_playa(self):
        result = classify_archive_credit("Aaron Feinberg (design) / Max Lemaire (build)")
        self.assertNotEqual(result.playa_name_confidence, "high")
        self.assertIsNone(result.playa_name)

    def test_collective_needs_search(self):
        result = classify_archive_credit("Farsight Collective")
        self.assertEqual(result.credit_type, "collective")
        self.assertTrue(result.needs_identity_search)
        self.assertIsNone(result.playa_name)

    def test_plain_legal_name(self):
        result = classify_archive_credit("Jason Gronlund")
        self.assertEqual(result.credit_type, "person")
        self.assertEqual(result.legal_name, "Jason Gronlund")
        self.assertFalse(result.needs_identity_search)

    def test_engineering_by_clause(self):
        result = classify_archive_credit("Benjamin Langholz with engineering by Amihay Gonen")
        self.assertEqual(result.credit_type, "multi_person")
        self.assertIn("Benjamin Langholz", result.named_people)
        self.assertIn("Amihay Gonen", result.named_people)

    def test_art_by_clause(self):
        result = classify_archive_credit(
            "DANG'er Saaz Colletive art by Carson West and Tucker Roberts"
        )
        self.assertIn("Carson West", result.named_people)
        self.assertIn("Tucker Roberts", result.named_people)
        self.assertNotIn("DANG'er Saaz Colletive", result.named_people)

    def test_parenthetical_people_not_nickname(self):
        result = classify_archive_credit("unbound (christina and anna de quero)")
        self.assertEqual(result.credit_type, "multi_person")
        self.assertIn("Christina", result.named_people)
        self.assertTrue(any("Anna" in name and "Quero" in name for name in result.named_people))


if __name__ == "__main__":
    unittest.main()
