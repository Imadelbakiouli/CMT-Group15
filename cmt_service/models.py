"""Database tables for the CMT (see the ER diagram in Task 2, section 4.2).

Each class = one table. Each db.Column = one column in that table.
"""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

# The database object; app.py connects it to the Flask app
db = SQLAlchemy()


def now():
    """Current time (UTC), used for timestamps."""
    return datetime.now(timezone.utc)


class User(db.Model):
    """A person using the CMT (chair, reviewer and/or author)."""
    id = db.Column(db.Integer, primary_key=True)                # unique number per user
    name = db.Column(db.String(120), nullable=False)            # required
    email = db.Column(db.String(255), unique=True, nullable=False)  # no two accounts with the same email
    affiliation = db.Column(db.String(255))                     # university or company (optional)
    password_hash = db.Column(db.String(255), nullable=False)   # hashed password, never the real one (SEC-02)


class Conference(db.Model):
    """A conference with its three deadlines."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    submission_deadline = db.Column(db.DateTime(timezone=True), nullable=False)
    review_deadline = db.Column(db.DateTime(timezone=True), nullable=False)
    notification_deadline = db.Column(db.DateTime(timezone=True), nullable=False)


class ConferenceRole(db.Model):
    """Which role a user has in which conference (SEC-03, SEC-04).

    Example: Imad can be chair of conference 1 and author in conference 2.
    """
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)              # link to User
    conference_id = db.Column(db.Integer, db.ForeignKey("conference.id"), primary_key=True)  # link to Conference
    role = db.Column(db.String(20), primary_key=True)  # "chair", "reviewer" or "author"


class Invitation(db.Model):
    """An invitation for a user to join the programme committee as reviewer."""
    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conference.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)  # pending, accepted or declined


class Submission(db.Model):
    """A paper submitted to a conference."""
    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conference.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    abstract = db.Column(db.Text, nullable=False)
    pdf_path = db.Column(db.String(255))  # where the PDF is stored (random file name, SEC-10)
    # submitted, under review, accepted, rejected or withdrawn
    status = db.Column(db.String(20), default="submitted", nullable=False)


class SubmissionAuthor(db.Model):
    """Who wrote a submission. Used to stop authors reviewing their own paper (FR-05, SEC-04)."""
    submission_id = db.Column(db.Integer, db.ForeignKey("submission.id"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    is_corresponding = db.Column(db.Boolean, default=False, nullable=False)  # the contact author


class Assignment(db.Model):
    """A reviewer assigned to review a submission."""
    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submission.id"), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    # the same reviewer cannot be assigned to the same paper twice
    __table_args__ = (db.UniqueConstraint("submission_id", "reviewer_id"),)


class Review(db.Model):
    """The review a reviewer writes for an assignment (FR-12)."""
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignment.id"), unique=True, nullable=False)  # one review per assignment
    overall_score = db.Column(db.Integer, nullable=False)  # 1 to 5
    confidence = db.Column(db.Integer, nullable=False)     # 1 to 5
    comments = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=now, onupdate=now)  # last change


class Decision(db.Model):
    """The chair's accept/reject decision for a submission."""
    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submission.id"), unique=True, nullable=False)  # one decision per paper
    outcome = db.Column(db.String(10), nullable=False)  # "accept" or "reject"
    released = db.Column(db.Boolean, default=False, nullable=False)  # authors only see it when True (FR-15)


class AuditLog(db.Model):
    """A log of important actions, to detect misuse (SEC-13)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))  # who did it
    action = db.Column(db.String(255), nullable=False)        # what they did
    timestamp = db.Column(db.DateTime(timezone=True), default=now, nullable=False)  # when
