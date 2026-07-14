from __future__ import annotations

import json
from pathlib import Path


DEFAULTS = {
    "admin_import_url": "",
    "prefer_core_csv": True,
    "cleanup_tmp_on_success": True,
    "save_uploaded_art_csv_to_www": False,
    "hub_port": 8765,
    "preview_html_max_age_days": 14,
}


def load_deploy_config(project_root: Path) -> dict:
    path = project_root / "config" / "artelier_deploy.yaml"
    config = dict(DEFAULTS)
    if not path.exists():
        return config
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            config.update(raw)
    except json.JSONDecodeError:
        # Allow simple key: value yaml-ish without a YAML dependency.
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value.lower() in {"true", "false"}:
                config[key] = value.lower() == "true"
            elif value.isdigit():
                config[key] = int(value)
            else:
                config[key] = value
    return config
