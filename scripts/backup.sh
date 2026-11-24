#!/usr/bin/env bash
# Backup of the CMT database and the uploaded papers (T15: NFR-07 backups, SEC-11 encrypted storage).
#
# Makes two files in BACKUP_DIR, both encrypted with AES-256 and the passphrase in BACKUP_PASSPHRASE:
#   db-<date>.sql.gz.enc        the whole database (reviews, submissions, users, ...)
#   uploads-<date>.tar.gz.enc   the uploaded PDFs
# Backups older than BACKUP_KEEP_DAYS are removed. Nothing is ever stored unencrypted.
#
# Settings (environment):
#   BACKUP_PASSPHRASE  required. Keep it somewhere else than the backups, without it they cannot be opened
#   BACKUP_DIR         where to put the backups (default /backups)
#   UPLOAD_DIR         the folder with the uploaded PDFs (default /data/uploads)
#   BACKUP_KEEP_DAYS   how long to keep them (default 14)
#   PGHOST, PGUSER, PGDATABASE, PGPASSWORD (or DB_PASSWORD)  how to reach the database
set -euo pipefail
umask 077   # only the owner can read the backups

: "${BACKUP_PASSPHRASE:?Set BACKUP_PASSPHRASE, backups are never stored without encryption}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
UPLOAD_DIR="${UPLOAD_DIR:-/data/uploads}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
export PGHOST="${PGHOST:-db}" PGUSER="${PGUSER:-cmt}" PGDATABASE="${PGDATABASE:-cmt}"
export PGPASSWORD="${PGPASSWORD:-${DB_PASSWORD:-}}"

[ -d "$UPLOAD_DIR" ] || { echo "backup: upload folder $UPLOAD_DIR does not exist" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"

# The passphrase goes in through the environment, so it never shows up in the process list
encrypt() { openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE; }

stamp="$(date -u +%Y%m%d-%H%M%S)"
tmp_db="$BACKUP_DIR/.db-$stamp.partial"
tmp_up="$BACKUP_DIR/.uploads-$stamp.partial"
trap 'rm -f "$tmp_db" "$tmp_up"' EXIT   # a half-written file is removed, never mistaken for a good backup

pg_dump --clean --if-exists --no-owner | gzip | encrypt > "$tmp_db"
tar -C "$UPLOAD_DIR" -czf - . | encrypt > "$tmp_up"

mv "$tmp_db" "$BACKUP_DIR/db-$stamp.sql.gz.enc"
mv "$tmp_up" "$BACKUP_DIR/uploads-$stamp.tar.gz.enc"

find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'db-*.enc' -o -name 'uploads-*.enc' \) -mtime +"$KEEP_DAYS" -delete
echo "backup: ready, $stamp"
