#!/bin/zsh
# Turn auto-backup on (runs autobackup.py daily at 3 AM, and when the drive is plugged in) or off.
#   zsh install_autobackup.sh            # on
#   zsh install_autobackup.sh --remove   # off
LABEL=local.photo-vault.autobackup
DIR="${0:A:h}"
VENV="${DIR:h}/.venv"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null
if [[ "$1" == "--remove" ]]; then rm -f "$PLIST"; echo "Auto-backup turned off"; exit 0; fi
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
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/autobackup.py</string></array>
  <key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>$SITE</string></dict>
  <key>StartOnMount</key><true/>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$LOGS/autobackup.out</string>
  <key>StandardErrorPath</key><string>$LOGS/autobackup.out</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null || launchctl bootstrap "gui/$UID" "$PLIST"
echo "Auto-backup is on: new photos on the drive are backed up once a day (3 AM, or when the drive is plugged in)"
