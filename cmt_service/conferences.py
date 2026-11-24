"""Conferences and programme committee (T06).

Related requirements: UR-03, UR-04, UR-07, FR-03, FR-04, FR-10.
Permissions (SEC-03) are checked here in the backend: no role in the conference, no access.

Open points, not in the requirements, to agree on with the team:
- anyone who is logged in can create a conference and becomes its chair
- you can only invite someone who already has an account (we look them up by email)
- deadlines in the past are allowed
"""
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from access import conference_role_required
from audit import log_action
from auth import clean_text, error, login_required
from models import Conference, ConferenceRole, Invitation, User, db

conf_bp = Blueprint("conferences", __name__)

DEADLINES = ("submission_deadline", "review_deadline", "notification_deadline")


def as_utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def parse_deadline(value):
    """Read a date like 2028-03-01T12:00:00Z. Gives None if it is not a valid date. No timezone means UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return as_utc(parsed)


def deadlines_in_order(sub, rev, notif):
    """Submissions close first, then reviews, then the authors hear the result."""
    return sub < rev < notif


def conference_json(conf, user_id):
    roles = ConferenceRole.query.filter_by(conference_id=conf.id, user_id=user_id).all()
    return {
        "id": conf.id, "name": conf.name, "description": conf.description,
        **{d: as_utc(getattr(conf, d)).isoformat() for d in DEADLINES},
        "my_roles": sorted(r.role for r in roles),
    }


# Only a chair of this conference gets through (role check from access.py, T05).
# Unknown conference: 404, not a chair: 403 and a line in the audit log.
chair_required = conference_role_required("chair")


# ---- conferences (UR-03, FR-03) ----

@conf_bp.post("/conferences")
@login_required
def create_conference():
    data = request.get_json(silent=True) or {}
    name = clean_text(data.get("name"), 255, required=True)
    description = clean_text(data.get("description"), 5000)
    dates = [parse_deadline(data.get(d)) for d in DEADLINES]
    if name is None:
        return error("A name is required (max 255 characters)", 400)
    if description is None:
        return error("Description must be text of at most 5000 characters", 400)
    if None in dates:
        return error("submission_deadline, review_deadline and notification_deadline are required (ISO 8601 dates)", 400)
    if not deadlines_in_order(*dates):
        return error("Deadlines must be in order: submission, then review, then notification", 400)

    conf = Conference(name=name, description=description or None,
                      submission_deadline=dates[0], review_deadline=dates[1], notification_deadline=dates[2])
    db.session.add(conf)
    db.session.flush()
    db.session.add(ConferenceRole(user_id=g.user.id, conference_id=conf.id, role="chair"))
    log_action(g.user.id, f"created conference {conf.id}")
    db.session.commit()
    return jsonify(conference_json(conf, g.user.id)), 201


@conf_bp.get("/conferences")
@login_required
def list_conferences():
    """Everyone who is logged in can see the basic info (authors need it to submit a paper)."""
    confs = Conference.query.order_by(Conference.id).all()
    return jsonify([conference_json(c, g.user.id) for c in confs])


@conf_bp.get("/conferences/<int:conference_id>")
@login_required
def get_conference(conference_id):
    conf = db.session.get(Conference, conference_id)
    if conf is None:
        return error("Conference not found", 404)
    return jsonify(conference_json(conf, g.user.id))


@conf_bp.patch("/conferences/<int:conference_id>")
@chair_required
def update_conference(conference_id):
    data = request.get_json(silent=True) or {}
    conf = db.session.get(Conference, conference_id)
    if "name" in data:
        name = clean_text(data["name"], 255, required=True)
        if name is None:
            return error("A name is required (max 255 characters)", 400)
        conf.name = name
    if "description" in data:
        description = clean_text(data["description"], 5000)
        if description is None:
            return error("Description must be text of at most 5000 characters", 400)
        conf.description = description or None
    new_dates = {d: as_utc(getattr(conf, d)) for d in DEADLINES}
    for d in DEADLINES:
        if d in data:
            parsed = parse_deadline(data[d])
            if parsed is None:
                return error(f"{d} must be an ISO 8601 date", 400)
            new_dates[d] = parsed
    if not deadlines_in_order(*new_dates.values()):
        return error("Deadlines must be in order: submission, then review, then notification", 400)
    for d, value in new_dates.items():
        setattr(conf, d, value)
    log_action(g.user.id, f"updated conference {conference_id}")
    db.session.commit()
    return jsonify(conference_json(conf, g.user.id))


# ---- invitations: chair side (UR-04, FR-04) ----

@conf_bp.post("/conferences/<int:conference_id>/invitations")
@chair_required
def invite_reviewer(conference_id):
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    if not isinstance(email, str) or not email.strip():
        return error("An email address is required", 400)
    user = User.query.filter_by(email=email.strip().lower()).first()
    if user is None:
        return error("No account found with this email; the person must register first", 404)

    if ConferenceRole.query.filter_by(conference_id=conference_id, user_id=user.id, role="reviewer").first():
        return error("This person is already a reviewer in this conference", 409)
    invitation = Invitation.query.filter_by(conference_id=conference_id, user_id=user.id).first()
    if invitation and invitation.status == "pending":
        return error("This person already has a pending invitation", 409)
    if invitation:                      # declined before, so invite again
        invitation.status = "pending"
    else:
        invitation = Invitation(conference_id=conference_id, user_id=user.id)
        db.session.add(invitation)
    db.session.flush()
    log_action(g.user.id, f"invited user {user.id} as reviewer for conference {conference_id}")
    db.session.commit()
    return jsonify(invitation_json(invitation, include_user=True)), 201


@conf_bp.get("/conferences/<int:conference_id>/invitations")
@chair_required
def list_conference_invitations(conference_id):
    invitations = Invitation.query.filter_by(conference_id=conference_id).order_by(Invitation.id).all()
    return jsonify([invitation_json(i, include_user=True) for i in invitations])


# ---- invitations: reviewer side (UR-07, FR-10) ----

def invitation_json(inv, include_user=False):
    result = {"id": inv.id, "conference_id": inv.conference_id, "status": inv.status}
    if include_user:
        user = db.session.get(User, inv.user_id)
        result["user"] = {"id": user.id, "name": user.name, "email": user.email}
    else:
        result["conference_name"] = db.session.get(Conference, inv.conference_id).name
    return result


@conf_bp.get("/invitations")
@login_required
def my_invitations():
    """Only your own invitations."""
    invitations = Invitation.query.filter_by(user_id=g.user.id).order_by(Invitation.id).all()
    return jsonify([invitation_json(i) for i in invitations])


def answer_invitation(invitation_id, accept):
    inv = db.session.get(Invitation, invitation_id)
    if inv is None or inv.user_id != g.user.id:     # someone else's invitation gives the same answer as one that does not exist
        return error("Invitation not found", 404)
    if inv.status != "pending":
        return error(f"This invitation was already {inv.status}", 409)
    if accept:
        inv.status = "accepted"
        has_role = ConferenceRole.query.filter_by(
            conference_id=inv.conference_id, user_id=g.user.id, role="reviewer").first()
        if not has_role:
            db.session.add(ConferenceRole(user_id=g.user.id, conference_id=inv.conference_id, role="reviewer"))
    else:
        inv.status = "declined"
    log_action(g.user.id, f"{inv.status} invitation {inv.id} for conference {inv.conference_id}")
    db.session.commit()
    return jsonify(invitation_json(inv))


@conf_bp.post("/invitations/<int:invitation_id>/accept")
@login_required
def accept_invitation(invitation_id):
    return answer_invitation(invitation_id, accept=True)


@conf_bp.post("/invitations/<int:invitation_id>/decline")
@login_required
def decline_invitation(invitation_id):
    return answer_invitation(invitation_id, accept=False)
