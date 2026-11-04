#!/bin/bash
# build_mac_app.sh - make a double-clickable Tesellate.app that starts tesla_app.py (REQ-199). No Xcode needed.
#
# Usage:  Tools/build_mac_app.sh [DEST.app]    default DEST: ~/Applications/Tesellate.app
#
# Type the command on its own line. Do NOT paste a trailing "# comment" after it: an interactive zsh passes the
# "#" through as an argument (that once built an app folder literally named "#"), so DEST must end in .app.
#
# The app is a tiny launcher: it runs this repo's Tools/tesla_app.py through your login shell (the repo's .direnv venv
# python if it exists, else the python3 on your PATH, so pywebview is found). Update the repo and the app is updated; rebuild it
# only if you move the repo.  Optional native window:  pip install pywebview   (without it the app only serves; it never opens your browser)
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$HOME/Applications/Tesellate.app}"
case "$APP" in
  *.app) ;;
  *) echo "build_mac_app.sh: DEST must end in .app (got '$APP'). Run it with no argument for ~/Applications/Tesellate.app" >&2; exit 2 ;;
esac
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$REPO/Tools/lib/app_ui/icon/Tesellate_Icon.icns" "$APP/Contents/Resources/Tesellate.icns"   # the app icon (Finder, Dock, Cmd-Tab)
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Tesellate</string>
<key>CFBundleDisplayName</key><string>Tesellate</string>
<key>CFBundleIdentifier</key><string>net.inodes.tesellate</string>
<key>CFBundleExecutable</key><string>Tesellate</string>
<key>CFBundleIconFile</key><string>Tesellate</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>11.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/Tesellate" <<LAUNCH
#!/bin/bash
# runs from Finder, so use a login shell to get your usual PATH (python3, pywebview)
# prefer the repo's own virtualenv (direnv's .direnv/python-*), where pywebview lives; else the python3 on your PATH
export PY=\$(ls -d "$REPO"/.direnv/python-*/bin/python 2>/dev/null | head -1)
exec /bin/zsh -lc 'cd "$REPO/Tools" && exec "\${PY:-python3}" tesla_app.py 2>>"\$HOME/Library/Logs/tesla_app.log"'
LAUNCH
chmod +x "$APP/Contents/MacOS/Tesellate"
echo "Built $APP  (repo: $REPO)"
touch "$APP"        # makes Finder re-read the icon
echo "Open it from Finder, or:  open \"$APP\"   Log: ~/Library/Logs/tesla_app.log"
