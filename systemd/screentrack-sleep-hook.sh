#!/bin/sh
# systemd-sleep hook: installed to /usr/lib/systemd/system-sleep/screentrack
# by install.sh (requires root, which is why this can't live in a
# `systemd --user` unit — sleep.target itself doesn't exist for user
# session managers). systemd-sleep calls this with:
#   $1 = pre | post
#   $2 = suspend | hibernate | hybrid-sleep | suspend-then-hibernate
# and runs it as root, once, for the whole machine — not per user session.
#
# We signal every running screentrack-daemon process (covers the common
# single-user-desktop case; on a multi-user box each user's daemon gets
# the same signal). SIGUSR1 = about to sleep, SIGUSR2 = just woke up.

case "$1" in
    pre)
        pkill -SIGUSR1 -f screentrack-daemon || true
        ;;
    post)
        pkill -SIGUSR2 -f screentrack-daemon || true
        ;;
esac

exit 0
