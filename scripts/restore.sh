#!/usr/bin/env bash
# Check or restore a backup made by backup.sh (T15).
#
#   restore.sh verify  <file.enc>                 check that the file opens with the passphrase (changes nothing)
#   restore.sh db      <db-....sql.gz.enc>        put the database back. REPLACES the current tables
#   restore.sh uploads <uploads-....tar.gz.enc> [folder]   unpack the PDFs (default folder: UPLOAD_DIR or /data/uploads)
#
# Needs BACKUP_PASSPHRASE. "db" and "uploads" only run when CONFIRM=yes is set, because they overwrite data.
# "db" uses PGHOST, PGUSER, PGDATABASE and PGPASSWORD (or DB_PASSWORD) like backup.sh.
set -euo pipefail

: "${BACKUP_PASSPHRASE:?Set BACKUP_PASSPHRASE}"
mode="${1:-}"; file="${2:-}"
[ -n "$mode" ] && [ -f "$file" ] || { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

decrypt() { openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in "$file"; }
need_confirm() { [ "${CONFIRM:-}" = "yes" ] || { echo "restore: this overwrites data. Run again with CONFIRM=yes" >&2; exit 3; }; }

case "$mode" in
  verify)
    decrypt | gunzip -t   # a wrong passphrase or a damaged file fails here
    echo "restore: $file is fine" ;;
  db)
    need_confirm
    export PGHOST="${PGHOST:-db}" PGUSER="${PGUSER:-cmt}" PGDATABASE="${PGDATABASE:-cmt}"
    export PGPASSWORD="${PGPASSWORD:-${DB_PASSWORD:-}}"
    decrypt | gunzip | psql --set ON_ERROR_STOP=on --quiet ;;
  uploads)
    need_confirm
    target="${3:-${UPLOAD_DIR:-/data/uploads}}"
    mkdir -p "$target"
    decrypt | tar -xzf - -C "$target" ;;
  *) echo "restore: unknown mode $mode" >&2; exit 2 ;;
esac
