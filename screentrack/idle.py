"""Idle-time detection for X11 sessions.

Primary strategy: shell out to `xprintidle` (fast, reliable, tiny dependency).
Fallback: query XScreenSaver info directly via python-xlib if xprintidle
isn't installed.
"""
from __future__ import annotations

import shutil
import subprocess

_XPRINTIDLE = shutil.which("xprintidle")


def get_idle_seconds() -> float:
    """Return seconds since the last keyboard/mouse input, or 0.0 if unknown."""
    if _XPRINTIDLE:
        try:
            out = subprocess.check_output([_XPRINTIDLE], timeout=2)
            return int(out.strip()) / 1000.0
        except (subprocess.SubprocessError, ValueError, OSError):
            pass

    # Fallback: python-xlib + XScreenSaver extension
    try:
        from Xlib import display
        from Xlib.ext import screensaver

        d = display.Display()
        root = d.screen().root
        info = screensaver.query_info(d, root)
        return info.idle / 1000.0
    except Exception:
        # No X11, no xprintidle, no python-xlib: assume active so we never
        # silently lose tracking data.
        return 0.0
