# CMT-Group15
Secure AI-driven Conference Management Tool – SE4CySec II Group 15

## Run it (any computer with Docker)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine with the compose plugin) and git. Nothing else.

```bash
git clone https://github.com/Imadelbakiouli/CMT-Group15.git
cd CMT-Group15
git checkout develop
cp .env.example .env        # on Windows PowerShell: copy .env.example .env
docker compose up --build
```

The first start downloads and builds everything (a few minutes). When it says the servers are running, in a second terminal:

```bash
curl http://localhost:5000/health/all
```

You should see `"ok"` for the interface, orchestrator, cmt_service and ai_agent. Stop with `Ctrl+C`; `docker compose down` removes the containers, `docker compose down -v` also deletes all data (database and uploaded papers).

- **Port 5000 is busy** (on a Mac, AirPlay Receiver uses it): add `INTERFACE_PORT=5055` to `.env` and use that port.
- `.env` is never committed. `DB_PASSWORD=change-me` is fine on your own computer. Use a real password and a real `CSRF_SECRET` anywhere else.
- There is no web page yet, only the API. Anything that changes data needs a CSRF token: first `GET /` (the cookie `csrf_token` is set), then send its value as the header `X-CSRF-Token` and the cookie back. Examples in `tests/test_orchestrator.py`; the assistant is at `POST /api/ai/chat`.
- On Windows run the `scripts/*.sh` files in Git Bash or WSL. The containers themselves need nothing special.

## Run the tests (no Docker needed)

```bash
pip install -r requirements-dev.txt
pytest
```

Needs Python 3.11 or newer. About 360 tests, 15 seconds. `python scripts/make_test_report.py` writes `docs/test-report.md`.

More: `docs/requirements-changes.md` (what differs from Task 2), `docs/test-report.md`, `docs/load-test.md`, `docs/ai-assistant.md`.

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
