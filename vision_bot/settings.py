"""Persistent user settings stored outside the application source tree."""
import json
from pathlib import Path

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "MacScreenVisionBot"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

DEFAULTS = {"threshold": 0.72, "deadzone": 0.10, "gain": 1.25, "max_output": 0.65}

def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
    except (OSError, ValueError, TypeError):
        return dict(DEFAULTS)

def save_settings(settings):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: settings[k] for k in DEFAULTS if k in settings}, indent=2))
    tmp.replace(SETTINGS_FILE)
