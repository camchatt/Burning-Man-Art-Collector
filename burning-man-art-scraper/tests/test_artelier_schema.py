from pathlib import Path
import csv
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from burning_man_scraper.artelier_schema import (
    build_artelier_preview,
    format_row_for_schema,
    load_field_mapping,
    load_import_schema,
    validate_artelier_row,
)
from burning_man_scraper.models import InstallationRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "config" / "artelier_import_schema.yaml"
MAPPING_PATH = PROJECT_ROOT / "config" / "artelier_field_mapping.yaml"
TEMPLATE_PATH = Path("C:/Users/camch/Downloads/registry-import-template (1).csv")


class ArtelierSchemaTests(unittest.TestCase):
    def load_schema(self):
        return load_import_schema(SCHEMA_PATH)

    def test_exact_header_equality(self):
        schema = self.load_schema()
        with TEMPLATE_PATH.open("r", encoding="utf-8-sig", newline="") as file:
            template_headers = next(csv.reader(file))

        self.assertEqual(schema.headers, template_headers)

    def test_exact_column_order(self):
        schema = self.load_schema()

        self.assertEqual(
            schema.headers[:5],
            [
                "project_title",
                "project_slug",
                "project_type",
                "project_year",
                "project_location",
            ],
        )
        self.assertEqual(schema.headers[-1], "permission_status")

    def test_required_field_validation(self):
        schema = self.load_schema()
        row = {header: "" for header in schema.headers}

        validations = validate_artelier_row(row, schema)
        failures = {validation.field_name: validation.errors for validation in validations if not validation.valid}

        self.assertIn("project_title", failures)
        self.assertIn("proof_external_url", failures)

    def test_unknown_field_rejection(self):
        schema = self.load_schema()
        row = {header: "" for header in schema.headers}
        row["not_an_artelier_field"] = "nope"

        with self.assertRaises(ValueError):
            validate_artelier_row(row, schema)

    def test_null_formatting(self):
        schema = self.load_schema()
        row = {header: None for header in schema.headers}

        formatted = format_row_for_schema(row, schema)

        self.assertTrue(all(value == "" for value in formatted.values()))

    def test_integer_conversion(self):
        schema = self.load_schema()
        mapping = load_field_mapping(MAPPING_PATH)
        record = InstallationRecord(
            title="Temple of Dust",
            year="Burning Man 2022",
            canonical_source_url="https://history.burningman.org/art-history/installation/temple-of-dust/",
        )

        preview = build_artelier_preview(record, schema, mapping)

        self.assertEqual(preview.row["project_year"], "2022")

    def test_url_validation(self):
        schema = self.load_schema()
        row = {header: "" for header in schema.headers}
        row["project_title"] = "Temple"
        row["project_slug"] = "temple"
        row["proof_external_url"] = "not-a-url"

        validations = validate_artelier_row(row, schema)
        proof_validation = next(
            validation for validation in validations if validation.field_name == "proof_external_url"
        )

        self.assertFalse(proof_validation.valid)
        self.assertTrue(any("URL" in error for error in proof_validation.errors))

    def test_schema_version_locking(self):
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        data["schema_version"] = "unexpected-version"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_schema = Path(temp_dir) / "schema.yaml"
            temp_schema.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported Artelier schema version"):
                load_import_schema(temp_schema)


if __name__ == "__main__":
    unittest.main()
