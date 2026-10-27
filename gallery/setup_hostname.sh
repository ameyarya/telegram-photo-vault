#!/bin/zsh
# Point http://photos.local at this Mac. Run once:  sudo zsh setup_hostname.sh
set -e
[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo zsh $0"; exit 1; }
if grep -q "photos.local" /etc/hosts; then
  echo "photos.local is already in /etc/hosts"
else
  cp /etc/hosts /etc/hosts.before-photos-local
  printf '\n# Photos (local gallery)\n127.0.0.1 photos.local\n::1 photos.local\n' >> /etc/hosts
  echo "Added photos.local to /etc/hosts (backup: /etc/hosts.before-photos-local)"
fi
dscacheutil -flushcache; killall -HUP mDNSResponder 2>/dev/null || true
echo "Done. Open http://photos.local"
