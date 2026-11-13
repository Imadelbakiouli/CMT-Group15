"""Database tables for the CMT (see the ER diagram in Task 2, section 4.2)."""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def now():
    return datetime.now(timezone.utc)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    affiliation = db.Column(db.String(255))
    password_hash = db.Column(db.String(255), nullable=False)  # never the plain password (SEC-02)


class Conference(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    submission_deadline = db.Column(db.DateTime(timezone=True), nullable=False)
    review_deadline = db.Column(db.DateTime(timezone=True), nullable=False)
    notification_deadline = db.Column(db.DateTime(timezone=True), nullable=False)


class ConferenceRole(db.Model):
    """Which role a user has in which conference (SEC-03, SEC-04)."""
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conference.id"), primary_key=True)
    role = db.Column(db.String(20), primary_key=True)  # "chair", "reviewer" or "author"


class Invitation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conference.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)  # pending, accepted, declined


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conference_id = db.Column(db.Integer, db.ForeignKey("conference.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    abstract = db.Column(db.Text, nullable=False)
    pdf_path = db.Column(db.String(255))  # random file name, never the original (SEC-10)
    status = db.Column(db.String(20), default="submitted", nullable=False)
    # submitted, under review, accepted, rejected, withdrawn


class SubmissionAuthor(db.Model):
    """Authors of a submission; used to block conflicts of interest (FR-05, SEC-04)."""
    submission_id = db.Column(db.Integer, db.ForeignKey("submission.id"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    is_corresponding = db.Column(db.Boolean, default=False, nullable=False)


class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submission.id"), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    __table_args__ = (db.UniqueConstraint("submission_id", "reviewer_id"),)


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignment.id"), unique=True, nullable=False)
    overall_score = db.Column(db.Integer, nullable=False)  # 1-5
    confidence = db.Column(db.Integer, nullable=False)  # 1-5
    comments = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=now, onupdate=now)


class Decision(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submission.id"), unique=True, nullable=False)
    outcome = db.Column(db.String(10), nullable=False)  # "accept" or "reject"
    released = db.Column(db.Boolean, default=False, nullable=False)  # authors only see it when True (FR-15)


class AuditLog(db.Model):
    """Important actions, to detect misuse (SEC-13)."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=now, nullable=False)
