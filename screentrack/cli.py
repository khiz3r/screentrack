"""ScreenTrack CLI — `screentrack --today`, `--week`, `--config`, etc."""
from __future__ import annotations

import argparse
import calendar
import csv
import json
import os
import shutil
import signal
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import db, emailer, notify
from .config import add_app, load_config, remove_app, save_config, set_value
from .display import app_breakdown, console, daily_table, fmt_duration, key_value, status_line
from .paths import BACKUP_DIR, CONFIG_FILE, DB_FILE, EXPORT_DIR, PID_FILE, STATUS_FILE, ensure_dirs


# --------------------------------------------------------------------------
# date range helpers
# --------------------------------------------------------------------------

def _week_bounds(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())  # Monday
    end = start + timedelta(days=6)
    return start, end


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


# --------------------------------------------------------------------------
# display commands
# --------------------------------------------------------------------------

def cmd_day(target: date, top_n: int | None = None):
    with db.get_conn() as conn:
        rows = db.app_totals_for_range(conn, target, target)
    app_breakdown(f"{target.strftime('%A, %b %d, %Y')}", rows, top_n=top_n)


def cmd_week(target: date, top_n: int | None = None):
    start, end = _week_bounds(target)
    with db.get_conn() as conn:
        daily = db.daily_totals_for_range(conn, start, end)
        totals_by_day = {r["day"]: r["total_seconds"] for r in daily}
        total = sum(totals_by_day.values())

    console.print(f"\n[bold]Week of {start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}[/bold]"
                  f"  |  Total: {fmt_duration(total)}")
    console.print("─" * 55)
    cur = start
    while cur <= end:
        secs = totals_by_day.get(cur.isoformat(), 0)
        bar_frac = secs / (max(totals_by_day.values(), default=1) or 1)
        from .display import ascii_bar
        console.print(f"{cur.strftime('%a %m-%d')}  {ascii_bar(bar_frac)}  {fmt_duration(secs).rjust(8)}")
        cur += timedelta(days=1)


def cmd_month(year: int, month: int, top_n: int | None = None, label: str = ""):
    start, end = _month_bounds(year, month)
    with db.get_conn() as conn:
        total = db.total_seconds_for_range(conn, start, end)
        daily = db.daily_totals_for_range(conn, start, end)
        apps = db.app_totals_for_range(conn, start, end)

    days_with_data = len(daily) or 1
    avg = total / days_with_data
    title = label or f"{calendar.month_name[month]} {year}"
    console.print(f"\n[bold]{title}[/bold]  |  Total: {fmt_duration(total)}  |  Daily avg: {fmt_duration(int(avg))}")
    app_breakdown("App breakdown", apps, top_n=top_n)


def cmd_all_time(top_n: int | None = None):
    with db.get_conn() as conn:
        row = conn.execute("SELECT MIN(day) AS mn, MAX(day) AS mx FROM app_usage").fetchone()
        if not row["mn"]:
            status_line("No data recorded yet.", style="yellow")
            return
        start = date.fromisoformat(row["mn"])
        end = date.fromisoformat(row["mx"])
        apps = db.app_totals_for_range(conn, start, end)
        total = db.total_seconds_for_range(conn, start, end)

    console.print(f"\n[bold]All-time[/bold] ({start.isoformat()} – {end.isoformat()})"
                  f"  |  Total: {fmt_duration(total)}")
    app_breakdown("App breakdown", apps, top_n=top_n)


def cmd_all_months():
    with db.get_conn() as conn:
        months = db.all_months_with_data(conn)
        rows = []
        for ym in months:
            y, m = map(int, ym.split("-"))
            start, end = _month_bounds(y, m)
            total = db.total_seconds_for_range(conn, start, end)
            rows.append((ym, total))

    if not rows:
        status_line("No data recorded yet.", style="yellow")
        return

    from rich.table import Table
    table = Table(title="All months with data")
    table.add_column("Month", style="cyan")
    table.add_column("Total", justify="right")
    for ym, total in rows:
        table.add_row(ym, fmt_duration(total))
    console.print(table)


def cmd_app_history(app_name: str):
    with db.get_conn() as conn:
        rows = db.app_history(conn, app_name)
    if not rows:
        status_line(f"No history found for '{app_name}'. Check --list-apps for exact names.", style="yellow")
        return
    daily_table(f"History — {app_name}", rows)
    total = sum(r["total_seconds"] for r in rows)
    console.print(f"\n[bold]Grand total:[/bold] {fmt_duration(total)}")


# --------------------------------------------------------------------------
# control commands
# --------------------------------------------------------------------------

def _daemon_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # check it's alive without sending a real signal
        return pid
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return None


def cmd_status():
    pid = _daemon_pid()
    status = {}
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    if pid is None:
        status_line("● Tracker is NOT running.", style="red")
        console.print("  Start it with: systemctl --user start screentrack")
        return

    if status.get("paused"):
        status_line(f"● Tracker is running (PID {pid}) but PAUSED.", style="yellow")
    else:
        status_line(f"● Tracker is running (PID {pid}).", style="green")

    with db.get_conn() as conn:
        today = date.today()
        total = db.total_seconds_for_range(conn, today, today)
        sessions = db.session_count_for_day(conn, today)
    console.print(f"  Today so far: {fmt_duration(total)}  |  Sessions today: {sessions}")


def cmd_pause():
    ensure_dirs()
    STATUS_FILE.write_text(json.dumps({"paused": True, "since": datetime.now().isoformat()}))
    status_line("Tracking paused. Run --resume to continue.", style="yellow")


def cmd_resume():
    ensure_dirs()
    STATUS_FILE.write_text(json.dumps({"paused": False, "since": None}))
    status_line("Tracking resumed.", style="green")


# --------------------------------------------------------------------------
# config commands
# --------------------------------------------------------------------------

def cmd_set_goal(value: str):
    hours = _parse_hours(value)
    set_value("general.daily_goal_hours", hours)
    status_line(f"Daily goal set to {hours}h.", style="green")


def _parse_hours(value: str) -> float:
    value = value.strip().lower()
    if value.endswith("h"):
        return float(value[:-1])
    if "h" in value and "m" in value:
        h_part, m_part = value.split("h")
        m_part = m_part.replace("m", "").strip()
        return float(h_part) + float(m_part or 0) / 60
    return float(value)


def cmd_idle_threshold(minutes: int):
    set_value("general.idle_threshold_minutes", minutes)
    status_line(f"Idle threshold set to {minutes} minute(s).", style="green")


def cmd_add_app(name: str):
    add_app(name)
    status_line(f"Added '{name}' to the tracking whitelist.", style="green")


def cmd_remove_app(name: str):
    remove_app(name)
    status_line(f"Removed '{name}' from the tracking whitelist.", style="green")


def cmd_list_apps():
    cfg = load_config()
    console.print("[bold]Tracked apps (whitelist):[/bold]")
    for app in cfg["apps"]["whitelist"]:
        console.print(f"  - {app}")
    console.print("[dim]Anything else is bucketed under 'Other'.[/dim]")


def cmd_config(edit: bool):
    if edit:
        editor = os.environ.get("EDITOR", "nano")
        ensure_dirs()
        load_config()  # ensure file exists
        os.system(f'{editor} "{CONFIG_FILE}"')
        return
    cfg = load_config()
    console.print(f"[bold]Config file:[/bold] {CONFIG_FILE}\n")
    console.print_json(data=cfg)


def cmd_test_email():
    ok, msg = emailer.send_test_email()
    status_line(msg, style="green" if ok else "red")


def cmd_send_report():
    console.print("[dim]Sending report with data collected so far this month...[/dim]")
    ok, msg = emailer.send_current_report()
    status_line(msg, style="green" if ok else "red")


# --------------------------------------------------------------------------
# data commands
# --------------------------------------------------------------------------

def cmd_export(fmt: str):
    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT day, app_name, start_time, end_time, seconds FROM app_usage ORDER BY start_time"
        ).fetchall()

    if fmt == "csv":
        out_path = EXPORT_DIR / f"screentrack_export_{ts}.csv"
        with open(out_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["day", "app_name", "start_time", "end_time", "seconds"])
            for r in rows:
                writer.writerow([r["day"], r["app_name"], r["start_time"], r["end_time"], r["seconds"]])
    elif fmt == "json":
        out_path = EXPORT_DIR / f"screentrack_export_{ts}.json"
        data = [dict(r) for r in rows]
        out_path.write_text(json.dumps(data, indent=2))
    else:
        status_line(f"Unknown export format '{fmt}'. Use csv or json.", style="red")
        return

    status_line(f"Exported {len(rows)} records to {out_path}", style="green")


def cmd_backup():
    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"screentrack_{ts}.db"
    shutil.copy2(DB_FILE, dest)
    status_line(f"Database backed up to {dest}", style="green")


def cmd_reset():
    console.print("[bold red]This will permanently delete ALL ScreenTrack data.[/bold red]")
    answer = input("Type 'yes' to confirm: ").strip().lower()
    if answer != "yes":
        status_line("Reset cancelled.", style="yellow")
        return
    with db.get_conn() as conn:
        db.reset_all(conn)
    status_line("All data cleared.", style="green")
    # Re-initialise the schema so the DB is ready for fresh writes, then
    # bounce the daemon so it opens a new session against the empty database.
    # Without this the running daemon still holds a reference to the old
    # (now empty) session and tracks nothing until it is restarted manually.
    db.init_db()
    restart_rc = os.system("systemctl --user restart screentrack.service 2>/dev/null")
    if restart_rc == 0:
        status_line("Tracker restarted — recording fresh data now.", style="green")
    else:
        status_line(
            "Data cleared, but could not restart the tracker automatically.\n"
            "  Run:  systemctl --user restart screentrack.service",
            style="yellow",
        )


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="screentrack",
        description="ScreenTrack — lightweight CLI screen time tracker.",
    )

    disp = p.add_argument_group("Display")
    disp.add_argument("--today", action="store_true", help="Today's total time + app breakdown")
    disp.add_argument("--yesterday", action="store_true", help="Yesterday's stats")
    disp.add_argument("--week", action="store_true", help="This week day-by-day breakdown")
    disp.add_argument("--this-month", action="store_true", help="This month total + daily avg + apps")
    disp.add_argument("--last-month", action="store_true", help="Last month summary")
    disp.add_argument("--all-time", action="store_true", help="Total time from day one")
    disp.add_argument("--all-months", action="store_true", help="All months with data collected")
    disp.add_argument("--date", metavar="YYYY-MM-DD", help="Stats for a specific date")
    disp.add_argument("--top", type=int, metavar="N", help="Show top N apps only (default: 10)")
    disp.add_argument("--apps-today", action="store_true", help="App breakdown for today")
    disp.add_argument("--apps-week", action="store_true", help="App breakdown for this week")
    disp.add_argument("--apps-month", action="store_true", help="App breakdown for this month")
    disp.add_argument("--app", metavar="AppName", help="Full history of one specific app")

    ctrl = p.add_argument_group("Control")
    ctrl.add_argument("--status", action="store_true", help="Show if tracker is running or paused")
    ctrl.add_argument("--pause", action="store_true", help="Pause tracking manually")
    ctrl.add_argument("--resume", action="store_true", help="Resume tracking")

    cfg = p.add_argument_group("Config")
    cfg.add_argument("--set-goal", metavar="6h", help="Set daily screen time goal")
    cfg.add_argument("--idle-threshold", type=int, metavar="MINUTES", help="Set idle timeout in minutes")
    cfg.add_argument("--add-app", metavar="AppName", help="Add app to tracking whitelist")
    cfg.add_argument("--remove-app", metavar="AppName", help="Remove app from whitelist")
    cfg.add_argument("--list-apps", action="store_true", help="Show all tracked apps")
    cfg.add_argument("--config", action="store_true", help="View all settings")
    cfg.add_argument("--config-edit", action="store_true", help="Open config.toml in $EDITOR")
    cfg.add_argument("--test-email", action="store_true", help="Send a test report email")
    cfg.add_argument("--send-report", action="store_true",
                      help="Email a report right now using currently collected data (month-to-date)")

    data = p.add_argument_group("Data")
    data.add_argument("--export", choices=["csv", "json"], help="Export all data")
    data.add_argument("--backup", action="store_true", help="Backup the database")
    data.add_argument("--reset", action="store_true", help="Clear all data (with confirmation)")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Make sure everything's initialized even on a fresh machine.
    ensure_dirs()
    load_config()
    db.init_db()

    top_n = args.top or 10
    today = date.today()

    if args.today:
        cmd_day(today, top_n=top_n)
    elif args.yesterday:
        cmd_day(today - timedelta(days=1), top_n=top_n)
    elif args.week:
        cmd_week(today, top_n=top_n)
    elif args.this_month:
        cmd_month(today.year, today.month, top_n=top_n, label="This Month")
    elif args.last_month:
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        cmd_month(y, m, top_n=top_n, label="Last Month")
    elif args.all_time:
        cmd_all_time(top_n=top_n)
    elif args.all_months:
        cmd_all_months()
    elif args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            parser.error("--date must be in YYYY-MM-DD format")
            return 2
        cmd_day(target, top_n=top_n)
    elif args.apps_today:
        with db.get_conn() as conn:
            rows = db.app_totals_for_range(conn, today, today)
        app_breakdown("App breakdown — today", rows, top_n=top_n)
    elif args.apps_week:
        start, end = _week_bounds(today)
        with db.get_conn() as conn:
            rows = db.app_totals_for_range(conn, start, end)
        app_breakdown("App breakdown — this week", rows, top_n=top_n)
    elif args.apps_month:
        start, end = _month_bounds(today.year, today.month)
        with db.get_conn() as conn:
            rows = db.app_totals_for_range(conn, start, end)
        app_breakdown("App breakdown — this month", rows, top_n=top_n)
    elif args.app:
        cmd_app_history(args.app)

    elif args.status:
        cmd_status()
    elif args.pause:
        cmd_pause()
    elif args.resume:
        cmd_resume()

    elif args.set_goal:
        cmd_set_goal(args.set_goal)
    elif args.idle_threshold is not None:
        cmd_idle_threshold(args.idle_threshold)
    elif args.add_app:
        cmd_add_app(args.add_app)
    elif args.remove_app:
        cmd_remove_app(args.remove_app)
    elif args.list_apps:
        cmd_list_apps()
    elif args.config_edit:
        cmd_config(edit=True)
    elif args.config:
        cmd_config(edit=False)
    elif args.test_email:
        cmd_test_email()
    elif args.send_report:
        cmd_send_report()

    elif args.export:
        cmd_export(args.export)
    elif args.backup:
        cmd_backup()
    elif args.reset:
        cmd_reset()

    else:
        # No flags: default to today's view, the most common use case.
        cmd_day(today, top_n=top_n)

    return 0


if __name__ == "__main__":
    sys.exit(main())
