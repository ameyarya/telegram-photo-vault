#!/bin/zsh
# Turn auto-backup on (runs autobackup.sh whenever a drive is mounted) or off.
#   zsh install_autobackup.sh            # on
#   zsh install_autobackup.sh --remove   # off
LABEL=local.photo-vault.autobackup
DIR="${0:A:h}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null
if [[ "$1" == "--remove" ]]; then rm -f "$PLIST"; echo "Auto-backup turned off"; exit 0; fi
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>$DIR/autobackup.sh</string></array>
  <key>StartOnMount</key><true/>
  <key>StandardOutPath</key><string>$DIR/autobackup.log</string>
  <key>StandardErrorPath</key><string>$DIR/autobackup.log</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$UID" "$PLIST" 2>/dev/null || launchctl bootstrap "gui/$UID" "$PLIST"
echo "Auto-backup is on: plugging in the drive backs up new photos"
