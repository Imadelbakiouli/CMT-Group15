"""Profile and personal data (T04): edit profile, change password, export data, delete account.

Related requirements: UR-01, UR-02, FR-02, FR-18, NFR-06 (GDPR), SEC-02, SEC-12, SEC-13.

Choices we made (to confirm with the team):
- Deleting an account anonymises it right away. There is no admin who could approve a request.
  Papers and reviews stay in the conference, but they are no longer linked to a name or email.
- Changing the email or the password, and deleting the account, need the current password.
"""
import secrets
from datetime import datetime, timezone

import bcrypt
from flask import Blueprint, g, jsonify, request

from audit import log_action
from auth import (EMAIL_RE, LOCK_TIME, MAX_FAILED_LOGINS, MAX_PASSWORD_BYTES, MIN_PASSWORD, as_utc, clean_text,
                  error, hash_password, login_required, profile)
from models import (Assignment, AuditLog, ConferenceRole, Decision, Invitation, Review, Submission,
                    SubmissionAuthor, User, UserSession, db, now)

profile_bp = Blueprint("profile", __name__, url_prefix="/auth/me")


def password_is_right(user, password):
    """Check the current password. Wrong guesses count like failed logins, so this route
    cannot be used to guess the password (SEC-12). Returns (ok, error_response)."""
    if user.locked_until and as_utc(user.locked_until) > now():
        return False, error("Too many failed attempts, try again later", 429)
    if not isinstance(password, str) or len(password.encode()) > MAX_PASSWORD_BYTES \
            or not bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = now() + LOCK_TIME
            log_action(user.id, "account locked after repeated failed logins")
        log_action(user.id, "failed password check")
        db.session.commit()
        return False, error("The current password is not correct", 403)
    user.failed_logins, user.locked_until = 0, None
    return True, None


# ---- edit profile (FR-02) ----

@profile_bp.patch("")
@login_required
def edit_profile():
    """Change name, affiliation and/or email. Anything else in the request is ignored."""
    data = request.get_json(silent=True) or {}
    user = g.user

    if "email" in data:
        email = clean_text(data["email"], 255, required=True)
        if email is None or not EMAIL_RE.match(email):
            return error("A valid email address is required", 400)
        email = email.lower()
        if email != user.email:
            ok, problem = password_is_right(user, data.get("current_password"))
            if not ok:
                return problem
            if User.query.filter(User.email == email, User.id != user.id).first():
                return error("An account with this email already exists", 409)
            user.email = email
    if "name" in data:
        name = clean_text(data["name"], 120, required=True)
        if name is None:
            return error("A name is required (max 120 characters)", 400)
        user.name = name
    if "affiliation" in data:
        affiliation = clean_text(data["affiliation"], 255)
        if affiliation is None:
            return error("Affiliation must be text of at most 255 characters", 400)
        user.affiliation = affiliation or None

    log_action(user.id, "edited profile")
    db.session.commit()
    return jsonify(profile(user))


@profile_bp.post("/password")
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    new = data.get("new_password")
    if not isinstance(new, str) or len(new) < MIN_PASSWORD:
        return error(f"Password must be at least {MIN_PASSWORD} characters", 400)
    if len(new.encode()) > MAX_PASSWORD_BYTES:
        return error(f"Password is too long (max {MAX_PASSWORD_BYTES} bytes)", 400)
    ok, problem = password_is_right(g.user, data.get("current_password"))
    if not ok:
        return problem

    g.user.password_hash = hash_password(new)
    # every other login of this user stops working, only the current one stays
    UserSession.query.filter(UserSession.user_id == g.user.id,
                             UserSession.token_hash != g.session.token_hash).delete()
    log_action(g.user.id, "changed password")
    db.session.commit()
    return jsonify(status="password changed")


# ---- export personal data (UR-02, FR-18, NFR-06) ----

@profile_bp.get("/export")
@login_required
def export_data():
    """Everything we store about the logged-in user, and nothing about anybody else."""
    user = g.user
    submissions = (db.session.query(Submission, SubmissionAuthor)
                   .join(SubmissionAuthor, SubmissionAuthor.submission_id == Submission.id)
                   .filter(SubmissionAuthor.user_id == user.id).all())
    decisions = {}
    for submission, _ in submissions:
        decision = Decision.query.filter_by(submission_id=submission.id, released=True).first()
        if decision:
            decisions[submission.id] = decision.outcome
    reviews = (db.session.query(Review, Assignment, Submission)
               .join(Assignment, Review.assignment_id == Assignment.id)
               .join(Submission, Assignment.submission_id == Submission.id)
               .filter(Assignment.reviewer_id == user.id).all())

    data = {
        "exported_at": now().isoformat(),
        "profile": profile(user),
        "roles": [{"conference_id": r.conference_id, "role": r.role}
                  for r in ConferenceRole.query.filter_by(user_id=user.id)],
        "invitations": [{"conference_id": i.conference_id, "status": i.status}
                        for i in Invitation.query.filter_by(user_id=user.id)],
        "submissions_as_author": [
            {"id": s.id, "conference_id": s.conference_id, "title": s.title, "abstract": s.abstract,
             "status": s.status, "is_corresponding_author": a.is_corresponding,
             "released_decision": decisions.get(s.id)} for s, a in submissions],
        "reviews_written": [
            {"submission_id": s.id, "conference_id": s.conference_id, "overall_score": r.overall_score,
             "confidence": r.confidence, "comments": r.comments, "updated_at": as_utc(r.updated_at).isoformat()}
            for r, _, s in reviews],
        "activity_log": [{"action": a.action, "timestamp": as_utc(a.timestamp).isoformat()}
                         for a in AuditLog.query.filter_by(user_id=user.id).order_by(AuditLog.id)],
    }
    log_action(user.id, "exported personal data")
    db.session.commit()
    response = jsonify(data)
    response.headers["Content-Disposition"] = 'attachment; filename="my-personal-data.json"'
    return response


# ---- delete account (FR-18, NFR-06) ----

@profile_bp.delete("")
@login_required
def delete_account():
    """Anonymise the account. The password is needed, so a stolen session cannot do this alone."""
    user = g.user
    ok, problem = password_is_right(user, (request.get_json(silent=True) or {}).get("current_password"))
    if not ok:
        return problem

    user.name = "Deleted user"
    user.email = f"deleted-{user.id}@deleted.invalid"        # still unique, can never receive mail
    user.affiliation = None
    user.password_hash = hash_password(secrets.token_urlsafe(32))   # nobody knows this password
    user.failed_logins, user.locked_until = 0, None
    UserSession.query.filter_by(user_id=user.id).delete()    # also ends the current login
    Invitation.query.filter_by(user_id=user.id, status="pending").delete()
    log_action(user.id, "account deleted (anonymised)")
    db.session.commit()
    return jsonify(status="account deleted")
