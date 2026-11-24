#!/usr/bin/env bash
# Make a self-signed certificate for trying out HTTPS on your own computer (T15, SEC-11).
# The browser will warn about it, that is normal for a self-made certificate.
# For a real server use a certificate from a real authority (for example Let's Encrypt) in the same two files.
#
#   scripts/make-dev-cert.sh [folder]     default folder: certs   (makes server.key and server.crt)
set -euo pipefail
umask 077
dir="${1:-certs}"
if [ -e "$dir/server.key" ] && [ "${FORCE:-}" != "yes" ]; then
  echo "make-dev-cert: $dir/server.key already exists, run with FORCE=yes to replace it" >&2
  exit 1
fi
mkdir -p "$dir"
openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
  -keyout "$dir/server.key" -out "$dir/server.crt" 2>/dev/null
chmod 600 "$dir/server.key"
echo "make-dev-cert: made $dir/server.crt and $dir/server.key (valid 365 days, for localhost)"
