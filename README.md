# CMT-Group15
Secure AI-driven Conference Management Tool – SE4CySec II Group 15

## Backups and HTTPS (T15)

Both are optional extras on top of `docker compose up`. Both were tried with Docker Desktop on a Mac on 24 Nov 2026 (see `docs/requirements-changes.md`). The scripts are also tested with `pytest tests/test_backup.py`.

**Encrypted daily backup** of the database and the uploaded papers:
1. Put `BACKUP_PASSPHRASE=...` in `.env` (see `.env.example`). Keep the passphrase apart from the backups, without it they cannot be opened.
2. `docker compose -f docker-compose.yml -f docker-compose.backup.yml up -d`. Backups appear in `./backups` (AES-256, kept 14 days).
3. Check a backup: `BACKUP_PASSPHRASE=... scripts/restore.sh verify backups/<file>`.
4. Restore: `CONFIRM=yes BACKUP_PASSPHRASE=... scripts/restore.sh db backups/<db file>` (replaces the tables) and `... restore.sh uploads backups/<uploads file> <folder>`.

**HTTPS** in front of the interface (nginx, TLS 1.2+, HSTS):
1. `scripts/make-dev-cert.sh` makes a self-signed certificate in `./certs` (for a real server put a real certificate in the same two files).
2. `docker compose -f docker-compose.yml -f docker-compose.https.yml up --build`, then open https://localhost.

Traffic between the containers inside Docker is still plain http, and the PDFs and database are not encrypted on disk (only the backups are).
