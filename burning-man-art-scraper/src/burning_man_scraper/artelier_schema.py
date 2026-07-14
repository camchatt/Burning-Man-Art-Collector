from __future__ import annotations

from dataclasses import dataclass
import csv
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from burning_man_scraper.models import InstallationRecord


EXPECTED_SCHEMA_VERSION = "artelier-import-template-2026-06-17-v1"


@dataclass(frozen=True)
class ColumnDefinition:
    name: str
    order: int
    required: bool
    data_type: str
    null_behavior: str
    default_value: str
    validation_rules: list[str]


@dataclass(frozen=True)
class ImportSchema:
    schema_version: str
    authoritative_source: dict[str, object]
    columns: list[ColumnDefinition]

    @property
    def headers(self) -> list[str]:
        return [column.name for column in sorted(self.columns, key=lambda column: column.order)]


@dataclass(frozen=True)
class FieldMapping:
    scraper_source_field: str
    artelier_destination_field: str
    transformation: str
    required: bool
    null_behavior: str
    review_requirement: str


@dataclass(frozen=True)
class MappingConfig:
    schema_version: str
    mappings: list[FieldMapping]


@dataclass(frozen=True)
class FieldValidation:
    field_name: str
    value: str
    valid: bool
    errors: list[str]


@dataclass(frozen=True)
class ArtelierPreview:
    schema: ImportSchema
    row: dict[str, str]
    validations: list[FieldValidation]
    unmapped_source_fields: list[str]

    @property
    def valid(self) -> bool:
        return all(validation.valid for validation in self.validations)


def load_import_schema(path: Path) -> ImportSchema:
    data = json.loads(path.read_text(encoding="utf-8"))
    columns = [
        ColumnDefinition(
            name=column["name"],
            order=int(column["order"]),
            required=bool(column["required"]),
            data_type=column["data_type"],
            null_behavior=column["null_behavior"],
            default_value=str(column["default_value"]),
            validation_rules=list(column["validation_rules"]),
        )
        for column in data["columns"]
    ]
    schema = ImportSchema(
        schema_version=data["schema_version"],
        authoritative_source=data["authoritative_source"],
        columns=columns,
    )
    if schema.schema_version != EXPECTED_SCHEMA_VERSION:
        raise ValueError(f"Unsupported Artelier schema version: {schema.schema_version}")
    if [column.order for column in schema.columns] != list(range(1, len(schema.columns) + 1)):
        raise ValueError("Artelier schema column order must be contiguous and 1-based.")
    return schema


def load_field_mapping(path: Path) -> MappingConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    return MappingConfig(
        schema_version=data["schema_version"],
        mappings=[
            FieldMapping(
                scraper_source_field=mapping["scraper_source_field"],
                artelier_destination_field=mapping["artelier_destination_field"],
                transformation=mapping["transformation"],
                required=bool(mapping["required"]),
                null_behavior=mapping["null_behavior"],
                review_requirement=mapping["review_requirement"],
            )
            for mapping in data["mappings"]
        ],
    )


def build_artelier_preview(
    record: InstallationRecord,
    schema: ImportSchema,
    mapping_config: MappingConfig,
) -> ArtelierPreview:
    row = {column.name: column.default_value for column in schema.columns}
    mapped_source_fields: set[str] = set()

    for mapping in mapping_config.mappings:
        if mapping.artelier_destination_field not in row:
            raise ValueError(f"Mapping targets unknown Artelier field: {mapping.artelier_destination_field}")
        row[mapping.artelier_destination_field] = transform_value(record, mapping)
        if mapping.scraper_source_field != "__default__":
            mapped_source_fields.add(mapping.scraper_source_field)

    row = format_row_for_schema(row, schema)
    validations = validate_artelier_row(row, schema)
    source_fields = set(InstallationRecord.model_fields.keys())
    unmapped = sorted(source_fields - mapped_source_fields)
    return ArtelierPreview(schema=schema, row=row, validations=validations, unmapped_source_fields=unmapped)


def transform_value(record: InstallationRecord, mapping: FieldMapping) -> str:
    transformation = mapping.transformation
    if transformation.startswith("default:"):
        return transformation.split(":", 1)[1]

    value = getattr(record, mapping.scraper_source_field, None)
    if transformation == "copy":
        return stringify(value)
    if transformation == "slugify":
        return slugify(stringify(value))
    if transformation == "integer_string":
        return integer_string(value)
    if transformation == "url" or transformation == "url_or_empty":
        return stringify(value)
    if transformation == "collective_or_empty":
        return "Collective project" if stringify(value) else ""
    if transformation == "artist_contribution_title":
        title = stringify(value)
        return f"Artist contribution to {title}" if title else ""
    raise ValueError(f"Unknown Artelier field transformation: {transformation}")


def format_row_for_schema(row: dict[str, object], schema: ImportSchema) -> dict[str, str]:
    unknown_fields = set(row) - set(schema.headers)
    if unknown_fields:
        raise ValueError(f"Unknown Artelier import fields: {sorted(unknown_fields)}")

    formatted: dict[str, str] = {}
    for column in sorted(schema.columns, key=lambda item: item.order):
        value = row.get(column.name, column.default_value)
        if value is None:
            formatted[column.name] = "" if column.null_behavior == "empty_string" else "null"
        elif isinstance(value, list):
            formatted[column.name] = "; ".join(str(item) for item in value)
        else:
            formatted[column.name] = str(value)
    return formatted


def validate_artelier_row(row: dict[str, str], schema: ImportSchema) -> list[FieldValidation]:
    if list(row.keys()) != schema.headers:
        raise ValueError("Artelier row header order does not match schema.")
    unknown_fields = set(row) - set(schema.headers)
    if unknown_fields:
        raise ValueError(f"Unknown Artelier import fields: {sorted(unknown_fields)}")

    validations: list[FieldValidation] = []
    for column in sorted(schema.columns, key=lambda item: item.order):
        value = row[column.name]
        errors: list[str] = []
        if column.required and not value:
            errors.append("required value missing")
        errors.extend(validate_type_and_rules(value, column))
        validations.append(
            FieldValidation(
                field_name=column.name,
                value=value,
                valid=not errors,
                errors=errors,
            )
        )
    return validations


def validate_type_and_rules(value: str, column: ColumnDefinition) -> list[str]:
    errors: list[str] = []
    if not value and not column.required:
        return errors

    if column.data_type == "integer":
        if not re.fullmatch(r"\d+", value):
            errors.append("expected integer")
    elif column.data_type == "url":
        if value and not is_valid_url(value):
            errors.append("expected absolute http(s) URL")

    for rule in column.validation_rules:
        if rule == "non_empty" and not value:
            errors.append("must be non-empty")
        elif rule == "slug" and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            errors.append("expected slug")
        elif rule == "slug_or_empty" and value and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            errors.append("expected slug or empty")
        elif rule == "year_or_empty" and value and not re.fullmatch(r"(19|20)\d{2}", value):
            errors.append("expected four-digit year or empty")
        elif rule == "url" and not is_valid_url(value):
            errors.append("expected absolute http(s) URL")
        elif rule == "url_or_empty" and value and not is_valid_url(value):
            errors.append("expected absolute http(s) URL or empty")
        elif rule.startswith("one_of:") and value:
            choices = set(rule.split(":", 1)[1].split(","))
            if value not in choices:
                errors.append(f"expected one of {sorted(choices)}")
    return errors


def write_artelier_import_csv(path: Path, rows: list[dict[str, str]], schema: ImportSchema) -> None:
    for row in rows:
        validations = validate_artelier_row(row, schema)
        failures = [validation for validation in validations if not validation.valid]
        if failures:
            raise ValueError("Refusing to create Artelier import CSV with validation failures.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=schema.headers, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value).strip()


def integer_string(value: object) -> str:
    text = stringify(value)
    match = re.search(r"(19|20)\d{2}", text)
    return match.group(0) if match else ""


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def is_valid_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
