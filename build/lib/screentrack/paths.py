"""Central path definitions for ScreenTrack, honoring XDG conventions."""
import os
from pathlib import Path

XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
XDG_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))

APP_DIR_NAME = "screentrack"

CONFIG_DIR = XDG_CONFIG_HOME / APP_DIR_NAME
DATA_DIR = XDG_DATA_HOME / APP_DIR_NAME
STATE_DIR = XDG_STATE_HOME / APP_DIR_NAME

CONFIG_FILE = CONFIG_DIR / "config.toml"
DB_FILE = DATA_DIR / "screentrack.db"
LOG_FILE = STATE_DIR / "screentrack.log"
PID_FILE = STATE_DIR / "screentrack.pid"
STATUS_FILE = STATE_DIR / "status.json"  # {"paused": bool, "since": iso}
BACKUP_DIR = DATA_DIR / "backups"
EXPORT_DIR = DATA_DIR / "exports"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, STATE_DIR, BACKUP_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
