"""SQLite storage layer for ScreenTrack."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from .paths import DB_FILE, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,          -- ISO timestamp
    end_time TEXT,                      -- ISO timestamp, NULL while active
    end_reason TEXT                     -- 'logout' | 'sleep' | 'shutdown' | 'crash-recovered'
);

CREATE TABLE IF NOT EXISTS app_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    app_name TEXT NOT NULL,
    day TEXT NOT NULL,                  -- YYYY-MM-DD, local date the usage occurred on
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    seconds INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_usage_day ON app_usage(day);
CREATE INDEX IF NOT EXISTS idx_app_usage_app ON app_usage(app_name);

CREATE TABLE IF NOT EXISTS idle_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    start_time TEXT NOT NULL,
    end_time TEXT,
    seconds INTEGER
);

CREATE TABLE IF NOT EXISTS goal_notifications (
    day TEXT PRIMARY KEY,
    notified_at TEXT NOT NULL
);
"""


@contextmanager
def get_conn(path: Path = DB_FILE) -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db(path: Path = DB_FILE) -> None:
    with get_conn(path) as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------- sessions --

def start_session(conn: sqlite3.Connection, start_time: Optional[datetime] = None) -> int:
    start_time = start_time or datetime.now()
    cur = conn.execute(
        "INSERT INTO sessions (start_time) VALUES (?)", (start_time.isoformat(),)
    )
    return cur.lastrowid


def end_session(conn: sqlite3.Connection, session_id: int, reason: str,
                 end_time: Optional[datetime] = None) -> None:
    end_time = end_time or datetime.now()
    conn.execute(
        "UPDATE sessions SET end_time = ?, end_reason = ? WHERE id = ?",
        (end_time.isoformat(), reason, session_id),
    )


def get_open_session(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sessions WHERE end_time IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()


def sessions_for_day(conn: sqlite3.Connection, day: date) -> list[sqlite3.Row]:
    day_str = day.isoformat()
    return conn.execute(
        "SELECT * FROM sessions WHERE substr(start_time, 1, 10) = ? ORDER BY start_time",
        (day_str,),
    ).fetchall()


# --------------------------------------------------------------- app usage --

def record_app_usage(conn: sqlite3.Connection, session_id: int, app_name: str,
                      day: date, start_time: datetime, end_time: datetime) -> None:
    seconds = int((end_time - start_time).total_seconds())
    if seconds <= 0:
        return
    conn.execute(
        """INSERT INTO app_usage (session_id, app_name, day, start_time, end_time, seconds)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, app_name, day.isoformat(), start_time.isoformat(),
         end_time.isoformat(), seconds),
    )


def app_totals_for_range(conn: sqlite3.Connection, start_day: date, end_day: date) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT app_name, SUM(seconds) AS total_seconds
           FROM app_usage
           WHERE day BETWEEN ? AND ?
           GROUP BY app_name
           ORDER BY total_seconds DESC""",
        (start_day.isoformat(), end_day.isoformat()),
    ).fetchall()


def total_seconds_for_range(conn: sqlite3.Connection, start_day: date, end_day: date) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(seconds), 0) AS s FROM app_usage WHERE day BETWEEN ? AND ?",
        (start_day.isoformat(), end_day.isoformat()),
    ).fetchone()
    return row["s"]


def daily_totals_for_range(conn: sqlite3.Connection, start_day: date, end_day: date) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT day, SUM(seconds) AS total_seconds
           FROM app_usage
           WHERE day BETWEEN ? AND ?
           GROUP BY day
           ORDER BY day""",
        (start_day.isoformat(), end_day.isoformat()),
    ).fetchall()


def all_months_with_data(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT substr(day, 1, 7) AS ym FROM app_usage ORDER BY ym"
    ).fetchall()
    return [r["ym"] for r in rows]


def app_history(conn: sqlite3.Connection, app_name: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT day, SUM(seconds) AS total_seconds
           FROM app_usage
           WHERE app_name = ?
           GROUP BY day
           ORDER BY day""",
        (app_name,),
    ).fetchall()


def session_count_for_day(conn: sqlite3.Connection, day: date) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM sessions WHERE substr(start_time, 1, 10) = ?",
        (day.isoformat(),),
    ).fetchone()
    return row["c"]


# ------------------------------------------------------------- goal alerts --

def already_notified_today(conn: sqlite3.Connection, day: date) -> bool:
    row = conn.execute(
        "SELECT 1 FROM goal_notifications WHERE day = ?", (day.isoformat(),)
    ).fetchone()
    return row is not None


def mark_notified(conn: sqlite3.Connection, day: date) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO goal_notifications (day, notified_at) VALUES (?, ?)",
        (day.isoformat(), datetime.now().isoformat()),
    )


# --------------------------------------------------------------- maintain. --

def reset_all(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """DELETE FROM app_usage;
           DELETE FROM idle_periods;
           DELETE FROM sessions;
           DELETE FROM goal_notifications;"""
    )


def purge_older_than(conn: sqlite3.Connection, keep_days: int) -> int:
    """Delete app_usage/session/idle rows older than `keep_days` days ago.

    A `keep_days` of 0 means "keep forever" and this is a no-op. Returns the
    number of app_usage rows deleted.
    """
    if not keep_days or keep_days <= 0:
        return 0

    cutoff = (datetime.now() - timedelta(days=keep_days)).date().isoformat()

    cur = conn.execute("DELETE FROM app_usage WHERE day < ?", (cutoff,))
    deleted = cur.rowcount

    # Drop sessions (and their idle periods) that ended before the cutoff
    # and have no remaining app_usage rows referencing them.
    conn.execute(
        """DELETE FROM idle_periods WHERE session_id IN (
               SELECT id FROM sessions
               WHERE end_time IS NOT NULL AND substr(end_time, 1, 10) < ?
           )""",
        (cutoff,),
    )
    conn.execute(
        """DELETE FROM sessions
           WHERE end_time IS NOT NULL AND substr(end_time, 1, 10) < ?
             AND id NOT IN (SELECT DISTINCT session_id FROM app_usage)""",
        (cutoff,),
    )
    conn.execute("DELETE FROM goal_notifications WHERE day < ?", (cutoff,))

    return deleted
