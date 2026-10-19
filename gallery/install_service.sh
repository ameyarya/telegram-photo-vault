#!/bin/zsh
# Run the gallery in the background, start it at login, restart it if it crashes.
# Install:    zsh install_service.sh
# Uninstall:  zsh install_service.sh --remove
LABEL=local.photo-vault
DIR="${0:A:h}"
VENV="${DIR:h}/.venv"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null
if [[ "$1" == "--remove" ]]; then
  rm -f "$PLIST"; echo "Photo Vault service removed"; exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
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
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/serve.py</string></array>
  <key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>$SITE</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/server.log</string>
  <key>StandardErrorPath</key><string>$LOGS/server.log</string>
</dict>
</plist>
EOF
launchctl bootstrap "gui/$UID" "$PLIST" && echo "Photo Vault service installed and running"
