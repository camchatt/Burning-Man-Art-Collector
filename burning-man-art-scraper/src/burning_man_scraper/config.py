from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScraperConfig:
    max_records_per_run: int = 100
    state_database_path: Path = Path("data/state/scraper_state.sqlite3")
    preview_manifest_path: Path = Path("data/previews/source_manifest.json")
    raw_html_dir: Path = Path("data/previews/raw_html")
    export_root_dir: Path = Path("data/exports")
    request_delay_seconds: float = 3.0
    request_timeout_seconds: float = 30.0
    max_retries: int = 3
    user_agent: str = "BurningManArtArchiveScraper/0.3 (+standalone inspection)"
    artelier_import_schema_path: Path = Path("config/artelier_import_schema.yaml")
    artelier_field_mapping_path: Path = Path("config/artelier_field_mapping.yaml")


def load_config(config_path: Path | None = None) -> ScraperConfig:
    if config_path is None:
        config_path = Path(__file__).resolve().parents[2] / "config" / "default.yaml"

    values: dict[str, str] = {}
    if config_path.exists():
        for raw_line in config_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()

    max_records = int(values.get("max_records_per_run", ScraperConfig.max_records_per_run))
    state_database_path = Path(
        values.get("state_database_path", str(ScraperConfig.state_database_path))
    )
    if not state_database_path.is_absolute():
        state_database_path = config_path.parent.parent / state_database_path
    preview_manifest_path = Path(
        values.get("preview_manifest_path", str(ScraperConfig.preview_manifest_path))
    )
    if not preview_manifest_path.is_absolute():
        preview_manifest_path = config_path.parent.parent / preview_manifest_path
    raw_html_dir = Path(values.get("raw_html_dir", str(ScraperConfig.raw_html_dir)))
    if not raw_html_dir.is_absolute():
        raw_html_dir = config_path.parent.parent / raw_html_dir
    export_root_dir = Path(values.get("export_root_dir", str(ScraperConfig.export_root_dir)))
    if not export_root_dir.is_absolute():
        export_root_dir = config_path.parent.parent / export_root_dir
    artelier_import_schema_path = Path(
        values.get("artelier_import_schema_path", str(ScraperConfig.artelier_import_schema_path))
    )
    if not artelier_import_schema_path.is_absolute():
        artelier_import_schema_path = config_path.parent.parent / artelier_import_schema_path
    artelier_field_mapping_path = Path(
        values.get("artelier_field_mapping_path", str(ScraperConfig.artelier_field_mapping_path))
    )
    if not artelier_field_mapping_path.is_absolute():
        artelier_field_mapping_path = config_path.parent.parent / artelier_field_mapping_path

    return ScraperConfig(
        max_records_per_run=max_records,
        state_database_path=state_database_path,
        preview_manifest_path=preview_manifest_path,
        raw_html_dir=raw_html_dir,
        export_root_dir=export_root_dir,
        request_delay_seconds=float(
            values.get("request_delay_seconds", ScraperConfig.request_delay_seconds)
        ),
        request_timeout_seconds=float(
            values.get("request_timeout_seconds", ScraperConfig.request_timeout_seconds)
        ),
        max_retries=int(values.get("max_retries", ScraperConfig.max_retries)),
        user_agent=values.get("user_agent", ScraperConfig.user_agent),
        artelier_import_schema_path=artelier_import_schema_path,
        artelier_field_mapping_path=artelier_field_mapping_path,
    )
