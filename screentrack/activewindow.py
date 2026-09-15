"""Active (focused) window detection for X11.

Primary strategy: `xdotool getactivewindow` to find the focused window ID,
then `xprop -id <id> WM_CLASS` to read its class. We deliberately don't use
`xdotool getwindowclassname` — that subcommand isn't present in every
xdotool build (notably missing from some Debian/Kali packaged versions),
where it fails with "Unknown command" and silently breaks all tracking.
`xprop` reads the WM_CLASS property directly via Xlib and is available on
essentially every X11 install (part of x11-utils).

Fallback: EWMH via python-xlib if xdotool isn't installed at all.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional

_XDOTOOL = shutil.which("xdotool")
_XPROP = shutil.which("xprop")

_ewmh = None
if not _XDOTOOL:
    try:
        from ewmh import EWMH
        _ewmh = EWMH()
    except Exception:
        _ewmh = None


def _class_from_xprop(win_id: str) -> Optional[str]:
    """Parse `xprop -id <id> WM_CLASS` output, e.g.:
    WM_CLASS(STRING) = "Navigator", "firefox"
    Returns the second (class) field, which is the conventional one to
    match on; falls back to the first (instance) field if only one exists.
    """
    if not _XPROP:
        return None
    try:
        out = subprocess.check_output(
            [_XPROP, "-id", win_id, "WM_CLASS"], timeout=2, stderr=subprocess.DEVNULL
        ).decode("utf-8", "ignore")
    except (subprocess.SubprocessError, OSError):
        return None

    if "not found" in out or "=" not in out:
        return None  # window has no WM_CLASS (some menus/popups don't)

    fields = re.findall(r'"([^"]*)"', out)
    if not fields:
        return None
    return fields[-1] or fields[0]


def get_active_window_class() -> Optional[str]:
    """Return a human-friendly name for the focused window's application.

    Returns None if it can't be determined (e.g. no window focused, or
    running outside an X11 session).
    """
    if _XDOTOOL:
        try:
            win_id = subprocess.check_output(
                [_XDOTOOL, "getactivewindow"], timeout=2, stderr=subprocess.DEVNULL
            ).decode("utf-8", "ignore").strip()
            if not win_id:
                return None
            return _class_from_xprop(win_id)
        except (subprocess.SubprocessError, OSError):
            return None

    if _ewmh:
        try:
            win = _ewmh.getActiveWindow()
            if win is None:
                return None
            wm_class = win.get_wm_class()
            if wm_class:
                return wm_class[-1]
            return win.get_wm_name()
        except Exception:
            return None

    return None

