#!/bin/bash
# restart_app.command - quit Tesellate (and its server), rebuild the app launcher and open it again.
# Double-click it in Finder, or run Tools/restart_app.command in a terminal.
cd "$(dirname "$0")" || exit 1
osascript -e 'quit app "Tesellate"' >/dev/null 2>&1
for i in 1 2 3 4 5 6 7 8 9 10; do pgrep -f "[t]esla_app.py" >/dev/null || break; sleep 1; done
pkill -f "[t]esla_app.py" 2>/dev/null; sleep 1
./build_mac_app.sh && open "$HOME/Applications/Tesellate.app"
