"""Accounts and sessions: register, log in, log out, current user (T03).

Covers UR-01, FR-01, SEC-01 (login + 30 min timeout), SEC-02 (bcrypt),
SEC-09 (input validation), SEC-12 (limit failed logins) and SEC-13 (audit log).
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import bcrypt
from flask import Blueprint, g, jsonify, request

from audit import log_action
from models import User, UserSession, db, now

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

SESSION_IDLE = timedelta(minutes=30)  # SEC-01
MAX_FAILED_LOGINS = 5                 # SEC-12: lock the account after 5 wrong passwords in a row
LOCK_TIME = timedelta(minutes=15)
MIN_PASSWORD = 10
MAX_PASSWORD_BYTES = 72               # bcrypt only looks at the first 72 bytes
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Used to spend the same time on unknown emails as on known ones (stops guessing which emails exist)
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt())


def error(message, status):
    return jsonify(error=message), status


def as_utc(value):
    """SQLite gives back datetimes without timezone; treat those as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()  # salt is created and stored inside the hash


def clean_text(value, max_length, required=False):
    """Return a trimmed string, or None when the value is invalid."""
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (required and not value) or len(value) > max_length:
        return None
    return value


def profile(user):
    return {"id": user.id, "name": user.name, "email": user.email, "affiliation": user.affiliation}


def login_required(view):
    """Protect a route: needs 'Authorization: Bearer <token>' of an active session (SEC-01)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(header) <= 7:
            return error("Login required", 401)
        session = db.session.get(UserSession, hash_token(header[7:]))
        if session is None:
            return error("Login required", 401)
        current = now()
        if current - as_utc(session.last_seen) > SESSION_IDLE:
            db.session.delete(session)  # inactive for too long: the session ends
            db.session.commit()
            return error("Session expired, please log in again", 401)
        session.last_seen = current     # activity keeps the session alive
        db.session.commit()
        g.user = db.session.get(User, session.user_id)
        g.session = session
        return view(*args, **kwargs)
    return wrapper


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    name = clean_text(data.get("name"), 120, required=True)
    email = clean_text(data.get("email"), 255, required=True)
    affiliation = clean_text(data.get("affiliation"), 255)
    password = data.get("password")

    if name is None:
        return error("A name is required (max 120 characters)", 400)
    if email is None or not EMAIL_RE.match(email):
        return error("A valid email address is required", 400)
    if affiliation is None:
        return error("Affiliation must be text of at most 255 characters", 400)
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return error(f"Password must be at least {MIN_PASSWORD} characters", 400)
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return error(f"Password is too long (max {MAX_PASSWORD_BYTES} bytes)", 400)

    email = email.lower()
    if User.query.filter_by(email=email).first():
        return error("An account with this email already exists", 409)

    user = User(name=name, email=email, affiliation=affiliation or None, password_hash=hash_password(password))
    db.session.add(user)
    db.session.flush()  # gives the user an id for the audit log
    log_action(user.id, "registered")
    db.session.commit()
    return jsonify(profile(user)), 201


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email, password = data.get("email"), data.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or len(password.encode()) > MAX_PASSWORD_BYTES:
        return error("Invalid email or password", 401)

    user = User.query.filter_by(email=email.strip().lower()).first()
    current = now()

    if user and user.locked_until:
        if as_utc(user.locked_until) > current:
            log_action(user.id, "login blocked: account locked")
            db.session.commit()
            return error("Too many failed attempts, try again later", 429)
        user.failed_logins, user.locked_until = 0, None  # lock has expired

    stored = user.password_hash.encode() if user else _DUMMY_HASH
    password_ok = bcrypt.checkpw(password.encode(), stored)

    if not user or not password_ok:
        if user:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED_LOGINS:
                user.locked_until = current + LOCK_TIME
                log_action(user.id, "account locked after repeated failed logins")
        log_action(user.id if user else None, "failed login")
        db.session.commit()
        # same message for unknown email and wrong password
        return error("Invalid email or password", 401)

    user.failed_logins, user.locked_until = 0, None
    token = secrets.token_urlsafe(32)  # random, unguessable
    db.session.add(UserSession(token_hash=hash_token(token), user_id=user.id))
    log_action(user.id, "logged in")
    db.session.commit()
    return jsonify(token=token, expires_after_inactivity_minutes=30, user=profile(user))


@auth_bp.post("/logout")
@login_required
def logout():
    log_action(g.user.id, "logged out")
    db.session.delete(g.session)  # the token stops working immediately
    db.session.commit()
    return jsonify(status="logged out")


@auth_bp.get("/me")
@login_required
def me():
    return jsonify(profile(g.user))
