"""Small helper to write a line to the audit log (SEC-13)."""
from flask import has_request_context, request

from models import AuditLog, db


def log_action(user_id, action):
    """Add one line to the audit log. Don't put passwords or tokens in `action`.

    When the AI assistant made the request (it sends the header X-Via: ai-assistant) the line says so.
    """
    if has_request_context() and request.headers.get("X-Via") == "ai-assistant":
        action += " [via AI assistant]"
    db.session.add(AuditLog(user_id=user_id, action=action))
    # no commit here, the caller does that
