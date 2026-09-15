"""Entry point for the monthly-report systemd timer.

Checks whether today is the configured send day and, if so, emails the
report for the *previous* month. Safe to run daily — it no-ops on all
other days.
"""
from __future__ import annotations

import calendar
from datetime import date

from . import emailer
from .config import load_config


def main() -> int:
    cfg = load_config()
    email_cfg = cfg["email"]

    if not email_cfg.get("enabled") or not email_cfg.get("send_monthly_report"):
        return 0

    today = date.today()
    send_day = email_cfg.get("send_day", 1)
    if today.day != send_day:
        return 0

    prev_month = today.month - 1 or 12
    prev_year = today.year if today.month > 1 else today.year - 1

    ok, msg = emailer.send_report(prev_year, prev_month)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
