"""The ScreenTrack daemon: polls active window + idle state and logs usage.

Run via `screentrack-daemon` (installed as a systemd --user service) or
directly with `python -m screentrack.tracker`.
"""
from __future__ import annotations

import json
import logging
import signal
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from . import db, notify
from .activewindow import get_active_window_class
from .config import load_config
from .idle import get_idle_seconds
from .paths import LOG_FILE, PID_FILE, STATUS_FILE, ensure_dirs

ensure_dirs()  # must happen before the logging handler below opens LOG_FILE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("screentrack")


def _match_whitelist(window_class: Optional[str], whitelist: list[str]) -> Optional[str]:
    """Map a raw window class to a whitelist entry (case-insensitive
    substring match), or None if nothing matches."""
    if not window_class:
        return None
    lc = window_class.lower()
    for entry in whitelist:
        if entry.lower() in lc or lc in entry.lower():
            return entry
    return None


def _read_status() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"paused": False, "since": None}


def _write_status(status: dict) -> None:
    ensure_dirs()
    STATUS_FILE.write_text(json.dumps(status))


class Tracker:
    # How often (in ticks) to flush the current segment to the DB even when
    # the active app hasn't changed. At the default 5-second poll interval,
    # FLUSH_EVERY=12 means we commit every ~60 seconds, so a crash can lose
    # at most one minute of data instead of an entire uninterrupted session.
    FLUSH_EVERY = 12

    def __init__(self):
        self.cfg = load_config()
        db.init_db()
        self._running = True
        self._current_app: Optional[str] = None
        self._segment_start: Optional[datetime] = None
        self._session_id: Optional[int] = None
        self._idle = False
        self._idle_since: Optional[datetime] = None
        self._sleeping = False
        self._ticks_since_flush: int = 0

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGUSR1, self._handle_sleep_signal)
        signal.signal(signal.SIGUSR2, self._handle_wake_signal)

    # ---------------------------------------------------------- lifecycle --

    def _handle_signal(self, signum, _frame):
        log.info("Received signal %s, shutting down gracefully.", signum)
        self._running = False

    def _handle_sleep_signal(self, _signum, _frame):
        """SIGUSR1 is sent by the system-level systemd-sleep hook right
        before suspend. Close out the current session with reason 'sleep'
        so suspended time is never counted, and mark ourselves as sleeping
        so tick() doesn't immediately reopen a session before the machine
        actually suspends.
        """
        log.info("Suspend signal received; closing session as 'sleep'.")
        self._sleeping = True
        self._close_session("sleep")

    def _handle_wake_signal(self, _signum, _frame):
        """SIGUSR2 is sent by the system-level systemd-sleep hook right
        after resume. Open a fresh session."""
        if self._sleeping:
            log.info("Wake signal received; starting a new session.")
            self._sleeping = False
            self._open_session()

    def _open_session(self):
        with db.get_conn() as conn:
            existing = db.get_open_session(conn)
            if existing:
                # Crash-recovered session: close it out before starting fresh.
                db.end_session(conn, existing["id"], "crash-recovered")
            self._session_id = db.start_session(conn)

            keep_days = load_config()["advanced"].get("keep_history_days", 0)
            if keep_days:
                deleted = db.purge_older_than(conn, keep_days)
                if deleted:
                    log.info("Purged %d app_usage row(s) older than %d day(s).", deleted, keep_days)

        log.info("Session %s started.", self._session_id)

    def _close_session(self, reason: str):
        if self._session_id is None:
            return
        self._flush_segment()
        with db.get_conn() as conn:
            db.end_session(conn, self._session_id, reason)
        log.info("Session %s ended (%s).", self._session_id, reason)
        self._session_id = None

    # ------------------------------------------------------------- ticking --

    def _flush_segment(self, reopen: bool = False):
        """Persist the in-progress app-usage segment, if any.

        When `reopen` is True the current app continues — we write the
        completed chunk to the DB and immediately restart a fresh segment
        for the same app.  This is used by the periodic mid-session flush
        so that a crash can only lose at most FLUSH_EVERY ticks of data.
        When `reopen` is False (default) we clear the current app so the
        next tick picks up whatever window is focused then.
        """
        if self._current_app is None or self._segment_start is None:
            self._ticks_since_flush = 0
            return
        end = datetime.now()
        app = self._current_app
        with db.get_conn() as conn:
            db.record_app_usage(
                conn, self._session_id, app,
                self._segment_start.date(), self._segment_start, end,
            )
        self._ticks_since_flush = 0
        if reopen:
            # Continue tracking the same app — just start a new segment.
            self._segment_start = end
        else:
            self._current_app = None
            self._segment_start = None

    def _check_goal(self, cfg: dict):
        if not cfg["general"].get("notify_on_goal", True):
            return
        goal_hours = cfg["general"].get("daily_goal_hours", 0)
        if not goal_hours:
            return
        today = date.today()
        with db.get_conn() as conn:
            if db.already_notified_today(conn, today):
                return
            total = db.total_seconds_for_range(conn, today, today)
            if total >= goal_hours * 3600:
                notify.send(
                    "ScreenTrack — Daily goal reached",
                    f"You've hit your {goal_hours}h screen time goal for today.",
                    urgency="normal",
                )
                db.mark_notified(conn, today)

    def tick(self):
        if self._session_id is None:
            if self._sleeping:
                # Genuinely suspended, awaiting the wake signal — don't
                # reopen a session mid-suspend.
                return
            # No active session and we're not asleep: something went wrong
            # (e.g. the wake signal never arrived). Self-heal rather than
            # silently tracking nothing forever.
            log.warning("No active session; opening a new one.")
            self._open_session()
            return

        cfg = load_config()  # cheap re-read so config changes apply live
        status = _read_status()
        if status.get("paused"):
            self._flush_segment()
            return

        idle_threshold = cfg["general"].get("idle_threshold_minutes", 5) * 60
        idle_seconds = get_idle_seconds()

        if idle_seconds >= idle_threshold:
            if not self._idle:
                log.info("User went idle.")
                self._idle = True
            self._flush_segment()
            return
        else:
            if self._idle:
                log.info("User returned from idle.")
                self._idle = False

        window_class = get_active_window_class()
        app = _match_whitelist(window_class, cfg["apps"]["whitelist"])

        if app is None:
            # Focused window is not in the whitelist — flush any ongoing
            # segment and wait until a tracked app comes into focus.
            self._flush_segment()
            return

        if app != self._current_app:
            self._flush_segment()
            self._current_app = app
            self._segment_start = datetime.now()
        else:
            # Same app still focused — bump the counter and flush periodically
            # so a crash can only lose at most FLUSH_EVERY ticks of data.
            self._ticks_since_flush += 1
            if self._ticks_since_flush >= self.FLUSH_EVERY:
                self._flush_segment(reopen=True)

        self._check_goal(cfg)

    def run(self):
        ensure_dirs()
        PID_FILE.write_text(str(__import__("os").getpid()))
        self._open_session()
        log.info("ScreenTrack daemon started.")
        try:
            while self._running:
                try:
                    self.tick()
                except Exception:
                    log.exception("Error during tick; continuing.")
                poll = load_config()["advanced"].get("poll_interval_seconds", 5)
                time.sleep(poll)
        finally:
            self._close_session("logout")
            if PID_FILE.exists():
                PID_FILE.unlink()
            log.info("ScreenTrack daemon stopped.")


def main():
    Tracker().run()


if __name__ == "__main__":
    main()
