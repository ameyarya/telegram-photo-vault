#!/bin/zsh
# Make https://photo-vault.local show a padlock. Run once (asks for your Mac password):  zsh setup_https.sh
# mkcert creates a certificate authority that exists only on this Mac and adds it to the trusted list.
set -e
cd "${0:A:h}"
command -v mkcert >/dev/null || brew install mkcert
mkcert -install
mkdir -p certs && chmod 700 certs
[[ -f certs/photo-vault.local.pem ]] || mkcert -cert-file certs/photo-vault.local.pem \
  -key-file certs/photo-vault.local-key.pem photo-vault.local localhost 127.0.0.1 ::1
chmod 600 certs/*
launchctl kickstart -k "gui/$UID/local.photo-vault" 2>/dev/null || true
echo "Done. Open https://photo-vault.local (restart your browser if it still warns)."
