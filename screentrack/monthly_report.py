"""Entry point for the screentrack-report systemd timer.

Runs once a day (at 08:00 via the timer). Does two things:

1. DAILY REPORT  — sent every morning with yesterday's screen time summary.
2. MONTHLY REPORT — sent on the 1st of each month with the previous month's
   full summary: total screen time, daily average, and top apps.

Both are controlled by the same [email] enabled flag.  If email is not
configured the script exits silently.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from . import db, emailer
from .config import load_config
from .display import fmt_duration


# --------------------------------------------------------------------------
# daily report
# --------------------------------------------------------------------------

def _build_daily_report(target: date) -> str:
    """Plain-text daily summary for `target` (usually yesterday)."""
    with db.get_conn() as conn:
        total = db.total_seconds_for_range(conn, target, target)
        apps = db.app_totals_for_range(conn, target, target)[:5]

    heading = f"ScreenTrack Daily Report — {target.strftime('%A, %b %d %Y')}"
    lines = [
        heading,
        "=" * len(heading),
        "",
        f"Total screen time: {fmt_duration(total)}",
        "",
        "Top apps:",
    ]
    if apps:
        for row in apps:
            lines.append(f"  - {row['app_name']}: {fmt_duration(row['total_seconds'])}")
    else:
        lines.append("  (no data recorded)")

    return "\n".join(lines)


def _send_daily(cfg: dict, today: date) -> None:
    yesterday = today - timedelta(days=1)
    body = _build_daily_report(yesterday)
    ok, msg = emailer._send_email(
        subject=f"ScreenTrack — {yesterday.strftime('%a %b %d')} summary",
        body=body,
        cfg=cfg,
    )
    print(f"[daily]   {msg}")


# --------------------------------------------------------------------------
# monthly report
# --------------------------------------------------------------------------

def _build_monthly_report(year: int, month: int) -> str:
    """Plain-text full-month summary for the given month."""
    last_day = calendar.monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, last_day)

    with db.get_conn() as conn:
        total = db.total_seconds_for_range(conn, start, end)
        daily_rows = db.daily_totals_for_range(conn, start, end)
        top_apps = db.app_totals_for_range(conn, start, end)[:5]

    days_with_data = len(daily_rows) or 1
    avg = int(total / days_with_data)

    heading = f"ScreenTrack Monthly Report — {calendar.month_name[month]} {year}"
    lines = [
        heading,
        "=" * len(heading),
        "",
        f"Total screen time:  {fmt_duration(total)}",
        f"Daily average:      {fmt_duration(avg)}",
        f"Days with activity: {days_with_data}",
        "",
        "Top 5 apps:",
    ]
    if top_apps:
        for row in top_apps:
            lines.append(f"  - {row['app_name']}: {fmt_duration(row['total_seconds'])}")
    else:
        lines.append("  (no data)")

    lines += ["", "Day-by-day breakdown:"]
    for row in daily_rows:
        lines.append(f"  {row['day']}: {fmt_duration(row['total_seconds'])}")

    return "\n".join(lines)


def _send_monthly(cfg: dict, today: date) -> None:
    # Send the report for the *previous* month.
    prev_month = today.month - 1 or 12
    prev_year = today.year if today.month > 1 else today.year - 1
    body = _build_monthly_report(prev_year, prev_month)
    ok, msg = emailer._send_email(
        subject=f"ScreenTrack Monthly Report — {calendar.month_name[prev_month]} {prev_year}",
        body=body,
        cfg=cfg,
    )
    print(f"[monthly] {msg}")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def main() -> int:
    cfg = load_config()
    email_cfg = cfg["email"]

    if not email_cfg.get("enabled"):
        return 0
    if not email_cfg.get("to_addr") or not email_cfg.get("smtp_user"):
        return 0

    today = date.today()

    # Always send the daily summary.
    _send_daily(email_cfg, today)

    # On the 1st of the month also send the full previous-month report.
    send_day = email_cfg.get("send_day", 1)
    if today.day == send_day and email_cfg.get("send_monthly_report", True):
        _send_monthly(email_cfg, today)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
