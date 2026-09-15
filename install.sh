#!/usr/bin/env bash
# ScreenTrack installer for Kali/Debian-based X11 systems.
set -euo pipefail

echo "== ScreenTrack installer =="

# --- 1. System dependencies (idle detection, active window, notifications) --
echo "--> Installing system dependencies (requires sudo)..."
sudo apt update -qq
sudo apt install -y xprintidle xdotool x11-utils libnotify-bin python3-pip pipx

pipx ensurepath >/dev/null 2>&1 || true

# --- 2. Install the Python package into an isolated pipx environment -------
echo "--> Installing ScreenTrack Python package..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pipx install --force "$SCRIPT_DIR"

# --- 3. Install systemd --user units ---------------------------------------
echo "--> Installing systemd user units..."
mkdir -p ~/.config/systemd/user
cp "$SCRIPT_DIR/systemd/screentrack.service" ~/.config/systemd/user/
cp "$SCRIPT_DIR/systemd/screentrack-report.service" ~/.config/systemd/user/
cp "$SCRIPT_DIR/systemd/screentrack-report.timer" ~/.config/systemd/user/

# The shipped unit assumes DISPLAY=:0 and XAUTHORITY=~/.Xauthority, which
# aren't always right (multiple displays, some greeters/display managers
# use a different XAUTHORITY path or location entirely). Patch in whatever
# this install script is actually running under, since that reflects the
# real logged-in desktop session.
DETECTED_DISPLAY="${DISPLAY:-:0}"
DETECTED_XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
sed -i "s|^Environment=DISPLAY=.*|Environment=DISPLAY=${DETECTED_DISPLAY}|" \
    ~/.config/systemd/user/screentrack.service
sed -i "s|^Environment=XAUTHORITY=.*|Environment=XAUTHORITY=${DETECTED_XAUTHORITY}|" \
    ~/.config/systemd/user/screentrack.service
echo "    Using DISPLAY=${DETECTED_DISPLAY} XAUTHORITY=${DETECTED_XAUTHORITY}"
echo "    (edit ~/.config/systemd/user/screentrack.service to change either)"

systemctl --user daemon-reload

if systemctl --user is-active --quiet screentrack.service; then
    echo "--> ScreenTrack is already running; stopping before reinstall (in-progress time for this session will be saved as a closed session)..."
    systemctl --user stop screentrack.service
fi

# Clean up any stale enablement symlink from a previous install of this
# unit (e.g. if it used to be WantedBy=graphical-session.target), so we
# don't end up with dangling symlinks alongside the current one.
systemctl --user disable screentrack.service 2>/dev/null || true

systemctl --user enable --now screentrack.service
systemctl --user enable --now screentrack-report.timer

# --- 4. Install the suspend/resume hook -------------------------------------
# sleep.target doesn't exist for `systemd --user` — it's a system-manager-only
# target — so suspend/resume awareness has to come from a root-level
# systemd-sleep drop-in instead of a user unit.
echo "--> Installing suspend/resume hook (requires sudo)..."
sudo install -m 755 "$SCRIPT_DIR/systemd/screentrack-sleep-hook.sh" \
    /usr/lib/systemd/system-sleep/screentrack

# Let the user service keep running after logout / across suspend, and
# — critically — let it start automatically at boot without a login.
loginctl enable-linger "$USER" 2>/dev/null || true

if [ "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null)" = "yes" ]; then
    echo "--> Linger enabled: ScreenTrack will start automatically at boot."
else
    echo "--> WARNING: could not confirm linger is enabled for $USER."
    echo "    Run manually: sudo loginctl enable-linger $USER"
fi

echo ""
echo "== Done! =="
echo "Config file: ~/.config/screentrack/config.toml"
echo "Try:  screentrack --status"
echo "      screentrack --today"
echo "      screentrack --config"
