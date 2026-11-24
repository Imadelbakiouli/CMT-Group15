"""Tests for T15: encrypted backups, restore, retention, dev certificate and the HTTPS/backup files.

The scripts run for real. Only pg_dump and psql are replaced by small fake programs, because
there is no database server in the tests.
"""
import os
import pathlib
import shutil
import stat
import subprocess
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DUMP_TEXT = "CREATE TABLE secret_table (email text);\nINSERT INTO secret_table VALUES ('alice@example.com');\n"

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None or shutil.which("bash") is None,
                                reason="needs bash and openssl")


@pytest.fixture
def box(tmp_path):
    """A folder with fake pg_dump/psql, an upload folder with two PDFs, and an environment for the scripts."""
    bin_dir, uploads, backups = tmp_path / "bin", tmp_path / "uploads", tmp_path / "backups"
    for d in (bin_dir, uploads):
        d.mkdir()
    (uploads / "aaa.pdf").write_bytes(b"%PDF-1.4 first paper")
    (uploads / "bbb.pdf").write_bytes(b"%PDF-1.4 second paper")
    fake = bin_dir / "pg_dump"
    fake.write_text(f'#!/bin/bash\necho "$@" > "{tmp_path}/pg_dump_args"\ncat <<\'SQL\'\n{DUMP_TEXT}SQL\n')
    psql = bin_dir / "psql"
    psql.write_text(f'#!/bin/bash\ncat > "{tmp_path}/psql_input"\n')
    for f in (fake, psql):
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "BACKUP_PASSPHRASE": "correct horse battery",
           "BACKUP_DIR": str(backups), "UPLOAD_DIR": str(uploads), "DB_PASSWORD": "dbpass"}
    return {"env": env, "tmp": tmp_path, "uploads": uploads, "backups": backups, "bin": bin_dir}


def run(script, *args, env, **kw):
    return subprocess.run([str(SCRIPTS / script), *args], env=env, capture_output=True, text=True, **kw)


def backup_files(box):
    return sorted(p.name for p in box["backups"].glob("*.enc"))


# ---- backup ----

def test_backup_makes_two_encrypted_files(box):
    r = run("backup.sh", env=box["env"])
    assert r.returncode == 0, r.stderr
    names = backup_files(box)
    assert len(names) == 2
    assert names[0].startswith("db-") and names[0].endswith(".sql.gz.enc")
    assert names[1].startswith("uploads-") and names[1].endswith(".tar.gz.enc")


def test_backups_are_really_encrypted(box):
    run("backup.sh", env=box["env"])
    for p in box["backups"].glob("*.enc"):
        data = p.read_bytes()
        assert data.startswith(b"Salted__")                       # OpenSSL encrypted file
        for plain in (b"secret_table", b"alice@example.com", b"%PDF", b"first paper", b"aaa.pdf"):
            assert plain not in data


def test_backup_files_are_private_and_the_passphrase_is_not_in_the_process_list(box):
    run("backup.sh", env=box["env"])
    for p in box["backups"].glob("*.enc"):
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert "correct horse" not in (SCRIPTS / "backup.sh").read_text().replace("BACKUP_PASSPHRASE", "")
    assert "pass:" not in (SCRIPTS / "backup.sh").read_text()      # only `-pass env:`, never on the command line


def test_database_is_dumped_with_clean_options_and_the_password_is_passed_safely(box):
    run("backup.sh", env=box["env"])
    args = (box["tmp"] / "pg_dump_args").read_text()
    assert "--clean" in args and "--if-exists" in args and "dbpass" not in args


def test_backup_refuses_to_run_without_passphrase(box):
    env = {k: v for k, v in box["env"].items() if k != "BACKUP_PASSPHRASE"}
    r = run("backup.sh", env=env)
    assert r.returncode != 0 and "BACKUP_PASSPHRASE" in r.stderr
    assert not box["backups"].exists() or backup_files(box) == []


def test_failed_dump_leaves_no_files_behind(box):
    (box["bin"] / "pg_dump").write_text("#!/bin/bash\necho partial output\nexit 1\n")
    r = run("backup.sh", env=box["env"])
    assert r.returncode != 0
    assert list(box["backups"].iterdir()) == []                  # not even a half-written file


def test_missing_upload_folder_is_an_error(box):
    r = run("backup.sh", env={**box["env"], "UPLOAD_DIR": str(box["tmp"] / "nope")})
    assert r.returncode != 0 and "does not exist" in r.stderr


def test_old_backups_are_removed_and_other_files_stay(box):
    run("backup.sh", env=box["env"])
    old_db, old_up, other = (box["backups"] / n for n in ("db-20200101-000000.sql.gz.enc",
                                                           "uploads-20200101-000000.tar.gz.enc", "notes.txt"))
    for f in (old_db, old_up, other):
        f.write_bytes(b"x")
        os.utime(f, (time.time() - 30 * 86400,) * 2)
    r = run("backup.sh", env=box["env"])
    assert r.returncode == 0
    assert not old_db.exists() and not old_up.exists() and other.exists()
    assert len(backup_files(box)) >= 2                            # the new ones are kept


# ---- verify and restore ----

def test_verify_accepts_right_passphrase_and_refuses_wrong_one(box):
    run("backup.sh", env=box["env"])
    db_file = str(next(box["backups"].glob("db-*.enc")))
    assert run("restore.sh", "verify", db_file, env=box["env"]).returncode == 0
    wrong = run("restore.sh", "verify", db_file, env={**box["env"], "BACKUP_PASSPHRASE": "wrong"})
    assert wrong.returncode != 0


def test_damaged_backup_is_noticed(box):
    run("backup.sh", env=box["env"])
    f = next(box["backups"].glob("db-*.enc"))
    data = bytearray(f.read_bytes())
    data[len(data) // 2] ^= 0xFF
    f.write_bytes(bytes(data))
    assert run("restore.sh", "verify", str(f), env=box["env"]).returncode != 0


def test_restore_db_needs_confirmation_then_sends_the_dump_to_psql(box):
    run("backup.sh", env=box["env"])
    f = str(next(box["backups"].glob("db-*.enc")))
    refused = run("restore.sh", "db", f, env=box["env"])
    assert refused.returncode == 3 and "CONFIRM=yes" in refused.stderr
    assert not (box["tmp"] / "psql_input").exists()               # nothing was touched
    done = run("restore.sh", "db", f, env={**box["env"], "CONFIRM": "yes"})
    assert done.returncode == 0, done.stderr
    assert (box["tmp"] / "psql_input").read_text() == DUMP_TEXT


def test_restore_uploads_gives_back_the_same_files(box):
    run("backup.sh", env=box["env"])
    f = str(next(box["backups"].glob("uploads-*.enc")))
    target = box["tmp"] / "restored"
    assert run("restore.sh", "uploads", f, str(target), env=box["env"]).returncode == 3     # needs CONFIRM
    r = run("restore.sh", "uploads", f, str(target), env={**box["env"], "CONFIRM": "yes"})
    assert r.returncode == 0, r.stderr
    assert (target / "aaa.pdf").read_bytes() == b"%PDF-1.4 first paper"
    assert (target / "bbb.pdf").read_bytes() == b"%PDF-1.4 second paper"


def test_restore_without_arguments_shows_help(box):
    r = run("restore.sh", env=box["env"])
    assert r.returncode == 2 and "verify" in r.stdout


# ---- certificate for HTTPS ----

def test_dev_certificate_is_made_for_localhost_and_not_overwritten(tmp_path):
    folder = tmp_path / "certs"
    r = run("make-dev-cert.sh", str(folder), env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    key = folder / "server.key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    text = subprocess.run(["openssl", "x509", "-in", str(folder / "server.crt"), "-noout", "-text"],
                          capture_output=True, text=True).stdout
    assert "DNS:localhost" in text and "IP Address:127.0.0.1" in text
    before = key.read_bytes()
    again = run("make-dev-cert.sh", str(folder), env=os.environ.copy())
    assert again.returncode == 1 and key.read_bytes() == before     # an existing key is never replaced silently


# ---- the HTTPS and backup files (text checks, they are not run in Docker here) ----

def test_nginx_config_uses_only_modern_tls_and_redirects_http():
    conf = (ROOT / "proxy" / "nginx.conf").read_text()
    assert "return 301 https://" in conf
    assert "ssl_protocols       TLSv1.2 TLSv1.3;" in conf
    assert "TLSv1 " not in conf and "TLSv1.1" not in conf and "SSLv" not in conf
    assert "Strict-Transport-Security" in conf
    assert conf.count("server_tokens off") == 2      # in the http block too, found in the Docker test
    assert "proxy_pass http://interface:5000" in conf and "client_max_body_size 21m" in conf


def test_compose_files_and_gitignore_protect_secrets():
    backup = (ROOT / "docker-compose.backup.yml").read_text()
    assert "BACKUP_PASSPHRASE: ${BACKUP_PASSPHRASE:?" in backup          # refuses to start without a passphrase
    assert "uploads:/data/uploads:ro" in backup                          # the backup can only read the papers
    assert "127.0.0.1:5000:5000" in (ROOT / "docker-compose.yml").read_text()
    ignore = (ROOT / ".gitignore").read_text()
    assert "backups/" in ignore and "certs/" in ignore
    assert "BACKUP_PASSPHRASE=\n" in (ROOT / ".env.example").read_text()  # the example has no real secret
