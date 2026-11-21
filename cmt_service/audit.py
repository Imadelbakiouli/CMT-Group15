"""Small helper to write a line to the audit log (SEC-13)."""
from models import AuditLog, db


def log_action(user_id, action):
    """Add one line to the audit log. Don't put passwords or tokens in `action`."""
    db.session.add(AuditLog(user_id=user_id, action=action))
    # no commit here, the caller does that
