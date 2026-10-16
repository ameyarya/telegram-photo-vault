#!/bin/zsh
# Point http://photo-vault.local at this Mac. Run once:  sudo zsh setup_hostname.sh
set -e
[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo zsh $0"; exit 1; }
if grep -q "photo-vault.local" /etc/hosts; then
  echo "photo-vault.local is already in /etc/hosts"
else
  cp /etc/hosts /etc/hosts.before-photo-vault
  printf '\n# Photo Vault (local gallery)\n127.0.0.1 photo-vault.local\n::1 photo-vault.local\n' >> /etc/hosts
  echo "Added photo-vault.local to /etc/hosts (backup: /etc/hosts.before-photo-vault)"
fi
dscacheutil -flushcache; killall -HUP mDNSResponder 2>/dev/null || true
echo "Done. Open http://photo-vault.local"
