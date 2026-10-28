#!/bin/bash
# Bind this to a hotkey: first press starts listening, second press cancels.
DIR="$(dirname "$(readlink -f "$0")")"
exec "$DIR/venv/bin/python3" "$DIR/wow_diktate_toggle.py" "$@"
