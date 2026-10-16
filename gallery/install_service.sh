#!/bin/zsh
# Run the gallery in the background, start it at login, restart it if it crashes.
# Install:    zsh install_service.sh
# Uninstall:  zsh install_service.sh --remove
LABEL=local.photo-vault
DIR="${0:A:h}"
PY="${DIR:h}/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null
if [[ "$1" == "--remove" ]]; then
  rm -f "$PLIST"; echo "Photo Vault service removed"; exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/serve.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DIR/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/server.log</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$UID" "$PLIST" && echo "Photo Vault service installed and running"
