"""Tests for T03 accounts: registration, login, sessions, lockout (UR-01, FR-01, SEC-01, SEC-02, SEC-09, SEC-12, SEC-13)."""
from datetime import timedelta

import bcrypt

from conftest import USER, auth_header, login, register


def sys_models(module):
    """The models module, for checking what was stored in the database."""
    import models
    return models


# ---- registration (FR-01) ----

def test_register_creates_account_without_leaking_password(cmt):
    client, _ = cmt
    r = register(client)
    assert r.status_code == 201
    body = r.get_json()
    assert body["email"] == "test.user@example.com"        # email is stored lowercase
    assert "password" not in body and "password_hash" not in body


def test_register_duplicate_email_rejected_case_insensitive(cmt):
    client, _ = cmt
    assert register(client).status_code == 201
    assert register(client, email="TEST.USER@example.com").status_code == 409


def test_register_rejects_bad_input(cmt):
    client, _ = cmt
    assert register(client, name="").status_code == 400
    assert register(client, name="x" * 121).status_code == 400
    assert register(client, email="not-an-email").status_code == 400
    assert register(client, password="short").status_code == 400
    assert register(client, password="a" * 73).status_code == 400   # bcrypt limit
    assert register(client, name=["list"]).status_code == 400       # wrong type
    assert client.post("/auth/register", data="not json").status_code == 400


# ---- password storage (SEC-02) ----

def test_password_is_stored_as_salted_bcrypt_hash(cmt):
    client, module = cmt
    register(client)
    register(client, email="other@example.com")                      # same password, other user
    models = sys_models(module)
    with module.app.app_context():
        hashes = [u.password_hash for u in models.User.query.order_by(models.User.id)]
    assert all(h.startswith("$2") for h in hashes)                   # bcrypt format
    assert USER["password"] not in hashes[0]
    assert hashes[0] != hashes[1]                                    # different salt per user
    assert bcrypt.checkpw(USER["password"].encode(), hashes[0].encode())


# ---- login / logout (UR-01, SEC-01) ----

def test_login_and_me(cmt):
    client, _ = cmt
    register(client)
    r = login(client)
    assert r.status_code == 200
    token = r.get_json()["token"]
    me = client.get("/auth/me", headers=auth_header(token))
    assert me.status_code == 200 and me.get_json()["name"] == "Test User"


def test_protected_route_needs_login(cmt):
    client, _ = cmt
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_logout_ends_session(cmt):
    client, _ = cmt
    register(client)
    token = login(client).get_json()["token"]
    assert client.post("/auth/logout", headers=auth_header(token)).status_code == 200
    assert client.get("/auth/me", headers=auth_header(token)).status_code == 401  # token is dead


def test_session_expires_after_30_minutes_inactive(cmt):
    client, module = cmt
    register(client)
    token = login(client).get_json()["token"]
    models = sys_models(module)
    with module.app.app_context():
        s = models.UserSession.query.one()
        s.last_seen = s.last_seen - timedelta(minutes=31)
        models.db.session.commit()
    r = client.get("/auth/me", headers=auth_header(token))
    assert r.status_code == 401
    assert client.get("/auth/me", headers=auth_header(token)).status_code == 401  # and stays gone


def test_activity_keeps_session_alive(cmt):
    client, module = cmt
    register(client)
    token = login(client).get_json()["token"]
    models = sys_models(module)
    with module.app.app_context():
        s = models.UserSession.query.one()
        s.last_seen = s.last_seen - timedelta(minutes=29)   # still inside the window
        models.db.session.commit()
    assert client.get("/auth/me", headers=auth_header(token)).status_code == 200


def test_session_token_is_stored_hashed(cmt):
    client, module = cmt
    register(client)
    token = login(client).get_json()["token"]
    models = sys_models(module)
    with module.app.app_context():
        assert models.UserSession.query.one().token_hash != token


# ---- wrong credentials and lockout (SEC-12) ----

def test_wrong_password_and_unknown_email_give_same_answer(cmt):
    client, _ = cmt
    register(client)
    wrong = login(client, password="wrong-password-123")
    unknown = login(client, email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json() == unknown.get_json()                    # no hint which emails exist


def test_account_locks_after_five_failed_logins(cmt):
    client, _ = cmt
    register(client)
    for _ in range(5):
        assert login(client, password="wrong-password-123").status_code == 401
    # even the correct password is refused while locked
    assert login(client).status_code == 429


def test_lock_expires_and_success_resets_counter(cmt):
    client, module = cmt
    register(client)
    for _ in range(5):
        login(client, password="wrong-password-123")
    models = sys_models(module)
    with module.app.app_context():
        u = models.User.query.one()
        u.locked_until = u.locked_until - timedelta(minutes=16)
        models.db.session.commit()
    assert login(client).status_code == 200
    with module.app.app_context():
        u = models.User.query.one()
        assert u.failed_logins == 0 and u.locked_until is None


# ---- injection safety (SEC-09) ----

def test_sql_injection_in_login_does_not_work(cmt):
    client, _ = cmt
    register(client)
    r = login(client, email="' OR '1'='1' --", password="x" * 12)
    assert r.status_code == 401
    r = login(client, email=USER["email"], password="' OR '1'='1")
    assert r.status_code == 401


# ---- audit log (SEC-13) ----

def test_audit_log_records_events_without_secrets(cmt):
    client, module = cmt
    register(client)
    login(client, password="wrong-password-123")
    token = login(client).get_json()["token"]
    models = sys_models(module)
    with module.app.app_context():
        actions = [a.action for a in models.AuditLog.query.order_by(models.AuditLog.id)]
    assert actions == ["registered", "failed login", "logged in"]
    joined = " ".join(actions)
    assert token not in joined and USER["password"] not in joined
