#!/bin/bash
# Bind this to a hotkey: first press starts listening, second press cancels.
DIR="$(dirname "$(readlink -f "$0")")"
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/wow_diktate.pid"
LOGFILE="${XDG_RUNTIME_DIR:-/tmp}/wow_diktate.log"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")"
    rm -f "$PIDFILE"
else
    "$DIR/venv/bin/python3" "$DIR/wow_diktate.py" "$@" > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
fi
