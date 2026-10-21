#!/bin/zsh
# Turn real-time backup on (watcher.py runs all the time: new photos reach Telegram within minutes) or off.
#   zsh install_autobackup.sh            # on
#   zsh install_autobackup.sh --remove   # off
LABEL=local.photo-vault.watcher
OLD=local.photo-vault.autobackup   # the daily job it replaces
DIR="${0:A:h}"
VENV="${DIR:h}/.venv"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null
launchctl bootout "gui/$UID/$OLD" 2>/dev/null; rm -f "$HOME/Library/LaunchAgents/$OLD.plist"
if [[ "$1" == "--remove" ]]; then rm -f "$PLIST"; echo "Real-time backup turned off"; exit 0; fi
# launchd can't start programs, open logs or change into folders inside ~/Documents (or Desktop, Downloads), so it
# starts Homebrew's Python (outside them) with the project's packages on PYTHONPATH; logs go to ~/Library/Logs/PhotoVault.
read PY SITE <<< "$("$VENV/bin/python" -c 'import os, site, sys
v = "%d.%d" % sys.version_info[:2]; s = f"/opt/homebrew/opt/python@{v}/bin/python{v}"
print(s if os.path.exists(s) else sys._base_executable, site.getsitepackages()[0])')"
LOGS="$HOME/Library/Logs/PhotoVault"; mkdir -p "$LOGS"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/watcher.py</string></array>
  <key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>$SITE</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOGS/watcher.out</string>
  <key>StandardErrorPath</key><string>$LOGS/watcher.out</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null || launchctl bootstrap "gui/$UID" "$PLIST"
echo "Real-time backup is on: new photos reach Telegram and the gallery within minutes"
