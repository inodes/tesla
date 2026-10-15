#!/bin/bash
# build_mac_app.sh - make a double-clickable Tesla.app that starts tesla_app.py (REQ-199). No Xcode needed.
#
# Usage:  Tools/build_mac_app.sh [DEST.app]    default DEST: ~/Applications/Tesla.app
#
# Type the command on its own line. Do NOT paste a trailing "# comment" after it: an interactive zsh passes the
# "#" through as an argument (that once built an app folder literally named "#"), so DEST must end in .app.
#
# The app is a tiny launcher: it runs this repo's Tools/tesla_app.py through your login shell (the repo's .direnv venv
# python if it exists, else the python3 on your PATH, so pywebview is found). Update the repo and the app is updated; rebuild it
# only if you move the repo.  Optional native window:  pip install pywebview   (without it the app opens your browser)
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$HOME/Applications/Tesla.app}"
case "$APP" in
  *.app) ;;
  *) echo "build_mac_app.sh: DEST must end in .app (got '$APP'). Run it with no argument for ~/Applications/Tesla.app" >&2; exit 2 ;;
esac
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Tesla</string>
<key>CFBundleDisplayName</key><string>Tesla</string>
<key>CFBundleIdentifier</key><string>net.inodes.tesla-app</string>
<key>CFBundleExecutable</key><string>Tesla</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>11.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/Tesla" <<LAUNCH
#!/bin/bash
# runs from Finder, so use a login shell to get your usual PATH (python3, pywebview)
# prefer the repo's own virtualenv (direnv's .direnv/python-*), where pywebview lives; else the python3 on your PATH
export PY=\$(ls -d "$REPO"/.direnv/python-*/bin/python 2>/dev/null | head -1)
exec /bin/zsh -lc 'cd "$REPO/Tools" && exec "\${PY:-python3}" tesla_app.py 2>>"\$HOME/Library/Logs/tesla_app.log"'
LAUNCH
chmod +x "$APP/Contents/MacOS/Tesla"
echo "Built $APP  (repo: $REPO)"
echo "Open it from Finder, or:  open \"$APP\"   Log: ~/Library/Logs/tesla_app.log"
