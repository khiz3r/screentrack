"""Thin wrapper around notify-send (libnotify) for desktop notifications."""
from __future__ import annotations

import shutil
import subprocess

_NOTIFY_SEND = shutil.which("notify-send")


def send(title: str, body: str, urgency: str = "normal") -> bool:
    """Fire a desktop notification. Returns True if it was dispatched."""
    if not _NOTIFY_SEND:
        return False
    try:
        subprocess.run(
            [_NOTIFY_SEND, "-u", urgency, "-a", "ScreenTrack", title, body],
            timeout=2, check=False,
        )
        return True
    except OSError:
        return False
