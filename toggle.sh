#!/bin/bash
PIDFILE=/tmp/wow_diktate.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    kill "$(cat $PIDFILE)"
    rm -f "$PIDFILE"
else
    pkill -f "wow_diktate.py" 2>/dev/null
    sleep 0.5
    ~/wow-diktate-venv/bin/python3 ~/wow_diktate.py > /dev/null 2>&1 &
    echo $! > "$PIDFILE"
fi
