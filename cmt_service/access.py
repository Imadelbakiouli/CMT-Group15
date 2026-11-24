"""Role checks per conference (T05, SEC-03). The backend decides, never the interface or the AI agent.

Use on any route that has <int:conference_id> in its path:

    @conf_bp.get("/conferences/<int:conference_id>/submissions")
    @conference_role_required("chair")
    def list_submissions(conference_id): ...

Deny by default: a route without this decorator is open to every logged-in user, so every
new route that touches conference data needs one (or its own check, like T10 has).
A role in one conference gives nothing in another one.
"""
from functools import wraps

from flask import g

from audit import log_action
from auth import error, login_required
from models import Conference, ConferenceRole, db


def roles_in(user_id, conference_id):
    """The set of roles this user has in this conference, e.g. {"chair", "author"}."""
    rows = ConferenceRole.query.filter_by(user_id=user_id, conference_id=conference_id).all()
    return {r.role for r in rows}


def conference_role_required(*allowed):
    """Let the request through only if the user has at least one of the allowed roles here."""
    if not allowed:
        raise ValueError("Name at least one role, a decorator without roles would let nobody in")

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(conference_id, *args, **kwargs):
            if db.session.get(Conference, conference_id) is None:
                return error("Conference not found", 404)
            roles = roles_in(g.user.id, conference_id)
            if not roles & set(allowed):
                log_action(g.user.id, f"denied: {'/'.join(allowed)} action on conference {conference_id}")
                db.session.commit()
                return error("You do not have the right role in this conference", 403)
            g.roles = roles
            return view(conference_id, *args, **kwargs)
        return wrapper
    return decorator
