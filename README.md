# ScreenTrack

A lightweight, privacy-friendly CLI screen time tracker for X11-based Linux
desktops (built and tested with Kali Linux in mind).

Everything is stored locally in SQLite (`~/.local/share/screentrack/screentrack.db`).
Nothing is ever sent anywhere unless you explicitly enable monthly email reports.

## Install

```bash
git clone <this-repo> screentrack && cd screentrack
./install.sh
```

The installer:
1. Installs `xprintidle`, `xdotool`, `libnotify-bin` via apt.
2. Installs the `screentrack` Python package via `pipx`.
3. Installs and enables two `systemd --user` units, plus one root-level hook:
   - `screentrack.service` — the always-on tracking daemon
   - `screentrack-report.timer` — checks daily whether to email the monthly report
   - `/usr/lib/systemd/system-sleep/screentrack` — a root-level suspend/resume
     hook (installed with sudo) that signals the daemon around sleep. This
     can't be a `systemd --user` unit because `sleep.target` only exists in
     the system manager, not the per-user one.

Works out of the box on X11. Wayland compositors that don't support
`xdotool`/`xprintidle`-style queries (e.g. pure GNOME/Wayland) aren't
supported — active-window and idle detection require X11 (or XWayland with a
compositor that exposes it).

## Quick start

```bash
screentrack --status                 # is it running?
screentrack --today                  # today's breakdown
screentrack --set-goal 6h            # daily goal
screentrack --add-app "Burp Suite"   # track another app
screentrack --config                 # view all settings
```

## All commands

**Display**
| Flag | What it shows |
|---|---|
| `--today` | Today's total + app breakdown |
| `--yesterday` | Yesterday's stats |
| `--week` | This week, day by day |
| `--this-month` | This month total + daily average + apps |
| `--last-month` | Last month summary |
| `--all-time` | Total time since first use |
| `--all-months` | Every month with data |
| `--date YYYY-MM-DD` | Stats for a specific date |
| `--top N` | Limit app breakdown to top N (default 10) |
| `--apps-today` / `--apps-week` / `--apps-month` | App-only breakdowns |
| `--app "Name"` | Full day-by-day history for one app |

**Control**
| Flag | Effect |
|---|---|
| `--status` | Running / paused, PID, today's total |
| `--pause` | Pause tracking |
| `--resume` | Resume tracking |

**Config**
| Flag | Effect |
|---|---|
| `--set-goal 6h` | Set daily goal |
| `--idle-threshold 5` | Idle timeout, minutes |
| `--add-app "Firefox"` | Add to whitelist |
| `--remove-app "Firefox"` | Remove from whitelist |
| `--list-apps` | Show whitelist |
| `--config` | Print full config as JSON |
| `--config-edit` | Open `config.toml` in `$EDITOR` |
| `--test-email` | Send a test email using your SMTP settings |
| `--send-report` | Email a report right now using whatever's been collected so far this month (doesn't wait for month-end, doesn't require `email.enabled`) |

**Data**
| Flag | Effect |
|---|---|
| `--export csv` / `--export json` | Export all raw usage records |
| `--backup` | Copy the SQLite DB to `~/.local/share/screentrack/backups/` |
| `--reset` | Wipe all data (asks for confirmation) |

## Configuration file

`~/.config/screentrack/config.toml`, created automatically on first run:

```toml
[general]
idle_threshold_minutes = 5
daily_goal_hours = 6.0
notify_on_goal = true

[apps]
whitelist = ["Firefox", "Terminal", "Code", "kitty", "Burp Suite"]

[email]
enabled = false
smtp_host = "smtp.gmail.com"
smtp_port = 587
smtp_user = ""
smtp_password = ""
use_tls = true
from_addr = ""
to_addr = ""
send_monthly_report = true
send_day = 1

[advanced]
poll_interval_seconds = 5
keep_history_days = 0
```

You can edit it by hand, with `screentrack --config-edit`, or piecemeal via
`--set-goal`, `--idle-threshold`, `--add-app` / `--remove-app`.

For Gmail SMTP, use an [App Password](https://myaccount.google.com/apppasswords)
in `smtp_password`, not your normal login password.

## How it works

- A `systemd --user` daemon (`screentrack-daemon`) polls the focused window
  (`xdotool`) and idle time (`xprintidle`) every `poll_interval_seconds`
  (default 5s).
- Time is attributed to whichever whitelisted app matches the focused
  window's class name (case-insensitive substring match); everything else
  is bucketed as "Other".
- If idle time exceeds `idle_threshold_minutes`, no time is logged until
  activity resumes.
- Each boot/login/suspend cycle is logged as a separate **session**, closed
  with a reason (`logout`, `sleep`, `shutdown`, or `crash-recovered` if the
  daemon didn't shut down cleanly last time).
- When your daily total crosses `daily_goal_hours`, you get one
  desktop notification per day via `notify-send`.

## Uninstall

```bash
systemctl --user disable --now screentrack.service screentrack-report.timer
sudo rm -f /usr/lib/systemd/system-sleep/screentrack
pipx uninstall screentrack
rm -rf ~/.config/screentrack ~/.local/share/screentrack ~/.local/state/screentrack
```

## Tech stack

Python 3 · SQLite · [rich](https://github.com/Textualize/rich) ·
`xprintidle` / python-xlib · `xdotool` / ewmh · `notify-send` ·
systemd user services · `smtplib`
