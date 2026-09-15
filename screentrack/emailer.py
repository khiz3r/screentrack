"""Monthly email report generation + sending via smtplib."""
from __future__ import annotations

import calendar
import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import db
from .config import load_config
from .display import fmt_duration


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def build_report(year: int, month: int, as_of: date | None = None) -> str:
    """Build the plain-text report body for `month`/`year`.

    If `as_of` is given (and falls inside that month), the report covers
    only 1st-of-month through `as_of` — i.e. a month-to-date snapshot of
    whatever's been collected so far — and the "vs last month" comparison
    is made against the same number of days into the previous month, so
    it's an apples-to-apples comparison rather than a partial month vs a
    full one.
    """
    start, end = _month_bounds(year, month)
    is_partial = as_of is not None and start <= as_of < end
    if is_partial:
        end = as_of

    prev_month = month - 1 or 12
    prev_year = year if month > 1 else year - 1
    prev_start, prev_end = _month_bounds(prev_year, prev_month)
    if is_partial:
        days_elapsed = (end - start).days + 1
        prev_end = min(prev_end, prev_start + timedelta(days=days_elapsed - 1))

    with db.get_conn() as conn:
        total = db.total_seconds_for_range(conn, start, end)
        prev_total = db.total_seconds_for_range(conn, prev_start, prev_end)
        daily = db.daily_totals_for_range(conn, start, end)
        top_apps = db.app_totals_for_range(conn, start, end)[:5]

    days_with_data = len(daily) or 1
    avg_seconds = total / days_with_data
    diff = total - prev_total
    diff_sign = "+" if diff >= 0 else "-"
    diff_str = f"{diff_sign}{fmt_duration(abs(diff))}"

    if is_partial:
        heading = f"ScreenTrack Report — {calendar.month_name[month]} {year} (through {end.strftime('%b %d')})"
        compare_label = f"same {days_elapsed} day(s) of {calendar.month_name[prev_month]} {prev_year}"
    else:
        heading = f"ScreenTrack Monthly Report — {calendar.month_name[month]} {year}"
        compare_label = f"{calendar.month_name[prev_month]} {prev_year}"

    lines = [
        heading,
        "=" * len(heading),
        "",
        f"Total screen time: {fmt_duration(total)}",
        f"Daily average:     {fmt_duration(int(avg_seconds))}",
        f"Vs. last month:    {diff_str} ({compare_label}: {fmt_duration(prev_total)})",
        "",
        "Top 5 apps:",
    ]
    if top_apps:
        for row in top_apps:
            lines.append(f"  - {row['app_name']}: {fmt_duration(row['total_seconds'])}")
    else:
        lines.append("  (no data)")

    lines += ["", "Day-by-day breakdown:"]
    for row in daily:
        lines.append(f"  {row['day']}: {fmt_duration(row['total_seconds'])}")

    return "\n".join(lines)


def send_report(year: int, month: int) -> tuple[bool, str]:
    """Send the automated end-of-month report. Respects the `enabled` toggle
    since this is called unattended by the daily timer."""
    cfg = load_config()
    email_cfg = cfg["email"]

    if not email_cfg.get("enabled"):
        return False, "Email reporting is disabled in config.toml ([email] enabled = false)."
    if not email_cfg.get("to_addr") or not email_cfg.get("smtp_user"):
        return False, "Missing to_addr or smtp_user in [email] config section."

    body = build_report(year, month)
    return _send_email(
        subject=f"ScreenTrack Report — {calendar.month_name[month]} {year}",
        body=body,
        cfg=email_cfg,
    )


def send_current_report() -> tuple[bool, str]:
    """Send an on-demand report covering whatever's been collected so far
    this month. Triggered explicitly via `screentrack --send-report`, so it
    doesn't check the `enabled` toggle — only that SMTP creds are set."""
    cfg = load_config()
    email_cfg = cfg["email"]
    if not email_cfg.get("to_addr") or not email_cfg.get("smtp_user"):
        return False, "Missing to_addr or smtp_user in [email] config section. Run --config-edit to set them."

    today = date.today()
    body = build_report(today.year, today.month, as_of=today)
    return _send_email(
        subject=f"ScreenTrack Report — {calendar.month_name[today.month]} {today.year} (through {today.strftime('%b %d')})",
        body=body,
        cfg=email_cfg,
    )


def send_test_email() -> tuple[bool, str]:
    cfg = load_config()
    email_cfg = cfg["email"]
    if not email_cfg.get("to_addr") or not email_cfg.get("smtp_user"):
        return False, "Missing to_addr or smtp_user in [email] config section."
    body = "This is a test email from ScreenTrack. If you're reading this, your SMTP settings work!"
    return _send_email(subject="ScreenTrack — Test Email", body=body, cfg=email_cfg)


def _send_email(subject: str, body: str, cfg: dict) -> tuple[bool, str]:
    msg = MIMEMultipart()
    msg["From"] = cfg.get("from_addr") or cfg["smtp_user"]
    msg["To"] = cfg["to_addr"]
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=15) as server:
            if cfg.get("use_tls", True):
                server.starttls()
            if cfg.get("smtp_password"):
                server.login(cfg["smtp_user"], cfg["smtp_password"])
            server.sendmail(msg["From"], [cfg["to_addr"]], msg.as_string())
        return True, f"Email sent to {cfg['to_addr']}."
    except Exception as e:  # noqa: BLE001 - surface any SMTP failure to the user
        return False, f"Failed to send email: {e}"

