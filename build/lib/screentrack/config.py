"""TOML-backed configuration for ScreenTrack.

Reading uses the stdlib `tomllib` (Python 3.11+). Writing uses `tomli_w`
(a small third-party lib) since the stdlib has no TOML writer.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - fallback for older Python
    import tomli as tomllib

import tomli_w

from .paths import CONFIG_FILE, ensure_dirs

DEFAULT_CONFIG: Dict[str, Any] = {
    "general": {
        "idle_threshold_minutes": 5,
        "daily_goal_hours": 6.0,
        "notify_on_goal": True,
    },
    "apps": {
        # Whitelist of window classes/names to track individually.
        # Anything not in this list is bucketed under "Other".
        "whitelist": ["Firefox", "Terminal", "Code", "kitty", "Burp Suite"],
    },
    "email": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "use_tls": True,
        "from_addr": "",
        "to_addr": "",
        "send_monthly_report": True,
        "send_day": 1,  # day of month to send the previous month's report
    },
    "advanced": {
        "poll_interval_seconds": 5,
        "keep_history_days": 0,  # 0 = keep forever
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, recursively, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path = CONFIG_FILE) -> Dict[str, Any]:
    """Load config, creating a default one on first run."""
    ensure_dirs()
    if not path.exists():
        save_config(DEFAULT_CONFIG, path)
        return dict(DEFAULT_CONFIG)

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    # Merge with defaults so new keys introduced in upgrades are present.
    return _deep_merge(DEFAULT_CONFIG, raw)


def save_config(cfg: Dict[str, Any], path: Path = CONFIG_FILE) -> None:
    ensure_dirs()
    with open(path, "wb") as f:
        tomli_w.dump(cfg, f)


def set_value(dotted_key: str, value: Any, path: Path = CONFIG_FILE) -> Dict[str, Any]:
    """Set a nested config value using a 'section.key' dotted path."""
    cfg = load_config(path)
    section, _, key = dotted_key.partition(".")
    if not key:
        raise ValueError(f"Expected 'section.key', got '{dotted_key}'")
    cfg.setdefault(section, {})[key] = value
    save_config(cfg, path)
    return cfg


def add_app(app_name: str, path: Path = CONFIG_FILE) -> Dict[str, Any]:
    cfg = load_config(path)
    whitelist = cfg["apps"]["whitelist"]
    if app_name not in whitelist:
        whitelist.append(app_name)
    save_config(cfg, path)
    return cfg


def remove_app(app_name: str, path: Path = CONFIG_FILE) -> Dict[str, Any]:
    cfg = load_config(path)
    whitelist = cfg["apps"]["whitelist"]
    cfg["apps"]["whitelist"] = [a for a in whitelist if a.lower() != app_name.lower()]
    save_config(cfg, path)
    return cfg
