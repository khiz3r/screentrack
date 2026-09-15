"""Terminal rendering helpers built on `rich`."""
from __future__ import annotations

from datetime import date
from typing import Sequence

from rich.console import Console
from rich.table import Table
from rich.text import Text

console = Console()

BAR_WIDTH = 14
FILLED = "█"
EMPTY = "░"


def fmt_duration(total_seconds: int) -> str:
    total_seconds = int(total_seconds)
    h, rem = divmod(total_seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m:02d}m"


def ascii_bar(fraction: float, width: int = BAR_WIDTH) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return FILLED * filled + EMPTY * (width - filled)


def app_breakdown(title: str, rows: Sequence, top_n: int | None = None) -> None:
    """rows: sequence of sqlite3.Row with app_name, total_seconds."""
    rows = list(rows)
    total = sum(r["total_seconds"] for r in rows) or 1
    shown = rows[:top_n] if top_n else rows
    hidden_count = len(rows) - len(shown)

    console.print(f"\n[bold]{title}[/bold]  |  Total: {fmt_duration(total if rows else 0)}")
    console.print("─" * 55)

    if not rows:
        console.print("[dim]No usage recorded for this period.[/dim]")
        return

    name_width = max((len(r["app_name"]) for r in rows), default=4)
    name_width = max(name_width, 4)

    for r in shown:
        frac = r["total_seconds"] / total
        pct = round(frac * 100)
        bar = ascii_bar(frac)
        name = r["app_name"].ljust(name_width)
        dur = fmt_duration(r["total_seconds"]).rjust(8)
        console.print(f"{name}  {bar}  {dur}  ({pct}%)")

    if hidden_count > 0:
        console.print(f"[dim]... {hidden_count} more app(s) not shown, 'Other' may be among them "
                       f"(use --top {len(rows)} to see all)[/dim]")


def daily_table(title: str, rows: Sequence, goal_hours: float | None = None) -> None:
    table = Table(title=title, show_lines=False)
    table.add_column("Date", style="cyan")
    table.add_column("Total", justify="right")
    if goal_hours:
        table.add_column("Goal", justify="center")

    for r in rows:
        secs = r["total_seconds"]
        cells = [r["day"], fmt_duration(secs)]
        if goal_hours:
            met = "✅" if secs >= goal_hours * 3600 else "—"
            cells.append(met)
        table.add_row(*cells)

    console.print(table)


def status_line(text: str, style: str = "green") -> None:
    console.print(Text(text, style=style))


def key_value(pairs: Sequence[tuple[str, str]]) -> None:
    for k, v in pairs:
        console.print(f"[bold]{k}:[/bold] {v}")
