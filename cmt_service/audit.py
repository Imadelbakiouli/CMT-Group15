"""Audit log helper (SEC-13): write important actions to the AuditLog table."""
from models import AuditLog, db


def log_action(user_id, action):
    """Add one line to the audit log. Never put passwords or tokens in `action`."""
    db.session.add(AuditLog(user_id=user_id, action=action))
    # no commit here: the caller commits together with its own changes
