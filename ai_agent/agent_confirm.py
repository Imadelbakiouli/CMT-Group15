"""Pending actions that wait for the user's "yes" (T13, FR-17).

An important action is not done when it is asked for. It is stored here and the user gets an id. Only the SAME user
(same login token) can confirm it, only once, and only within a few minutes. Kept in memory, so with several agent
processes a confirmation must reach the process that stored it (fine for one container).
"""
import hashlib
import secrets
import time

TTL_SECONDS = 300
MAX_PER_USER = 10
MAX_TOTAL = 5000


def owner_of(token):
    """Who the action belongs to. Only a hash of the token is kept in memory."""
    return hashlib.sha256(token.encode()).hexdigest()


class PendingActions:
    def __init__(self, ttl=TTL_SECONDS, clock=time.monotonic):
        self.ttl, self.clock, self.items = ttl, clock, {}

    def _purge(self):
        now = self.clock()
        for action_id in [i for i, e in self.items.items() if e["expires"] <= now]:
            del self.items[action_id]

    def add(self, owner, tool, args, summary):
        """Returns the id, or None when there are too many waiting actions."""
        self._purge()
        mine = [e for e in self.items.values() if e["owner"] == owner]
        if len(mine) >= MAX_PER_USER or len(self.items) >= MAX_TOTAL:
            return None
        action_id = secrets.token_urlsafe(16)
        self.items[action_id] = {"owner": owner, "tool": tool, "args": args, "summary": summary,
                                 "expires": self.clock() + self.ttl}
        return action_id

    def take(self, owner, action_id):
        """Remove and return the action when it exists, is not expired and belongs to this owner. Otherwise None."""
        self._purge()
        entry = self.items.get(action_id)
        if entry is None or entry["owner"] != owner:
            return None
        return self.items.pop(action_id)

    def cancel(self, owner, action_id):
        return self.take(owner, action_id) is not None
