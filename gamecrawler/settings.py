import json
from typing import Dict
from pathlib import Path

def load_settings(settings_file: Path) -> Dict:
    default = {"default_sandboxed": True}
    try:
        if settings_file.exists():
            data = json.loads(settings_file.read_text("utf-8"))
            default.update({k: data.get(k, default[k]) for k in default})
    except Exception:
        pass
    return default

def save_settings(settings_file: Path, settings: dict) -> None:
    settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
