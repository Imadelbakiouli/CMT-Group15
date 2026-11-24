"""Chair tools for assigning reviewers (T09): overview of submissions, assign, unassign, review progress.

Related requirements: UR-04, UR-05, FR-05, FR-06, SEC-04, SEC-13.

All routes are for the chair of the conference only (role check from access.py, SEC-03).

Conflict of interest (SEC-04, abuse scenarios A1 and A3):
- a reviewer can never be assigned to a paper they (co-)wrote  -> refused
- a chair who wrote a paper cannot assign reviewers to it, and gets no review details of it
- a reviewer who has the same affiliation as an author -> allowed, but the answer has a warning (our addition)
"""
from flask import Blueprint, g, jsonify, request
from sqlalchemy import func

from access import conference_role_required
from audit import log_action
from auth import error
from conferences import as_utc
from models import (Assignment, Conference, ConferenceRole, Review, Submission, SubmissionAuthor, User,
                    db, now)

chair_bp = Blueprint("chair_assignments", __name__)
chair_only = conference_role_required("chair")


def conference_submissions(conference_id):
    return Submission.query.filter_by(conference_id=conference_id).order_by(Submission.id).all()


def authors_by_submission(conference_id):
    """{submission id: [(SubmissionAuthor, User), ...]} for the whole conference in one query."""
    rows = (db.session.query(SubmissionAuthor, User)
            .join(User, User.id == SubmissionAuthor.user_id)
            .join(Submission, Submission.id == SubmissionAuthor.submission_id)
            .filter(Submission.conference_id == conference_id)
            .order_by(SubmissionAuthor.is_corresponding.desc(), User.id).all())
    result = {}
    for link, user in rows:
        result.setdefault(link.submission_id, []).append((link, user))
    return result


def counts(conference_id):
    """({submission id: assigned}, {submission id: reviews done}) in two queries."""
    assigned = dict(db.session.query(Assignment.submission_id, func.count(Assignment.id))
                    .join(Submission, Submission.id == Assignment.submission_id)
                    .filter(Submission.conference_id == conference_id).group_by(Assignment.submission_id).all())
    done = dict(db.session.query(Assignment.submission_id, func.count(Review.id))
                .join(Review, Review.assignment_id == Assignment.id)
                .join(Submission, Submission.id == Assignment.submission_id)
                .filter(Submission.conference_id == conference_id).group_by(Assignment.submission_id).all())
    return assigned, done


def chair_wrote(authors):
    return any(user.id == g.user.id for _, user in authors)


# ---- overview of submissions (FR-05) ----

@chair_bp.get("/conferences/<int:conference_id>/submissions")
@chair_only
def list_submissions(conference_id):
    authors = authors_by_submission(conference_id)
    assigned, done = counts(conference_id)
    result = []
    for s in conference_submissions(conference_id):
        item = {"id": s.id, "title": s.title, "status": s.status}
        if chair_wrote(authors.get(s.id, [])):
            # the chair's own paper: no abstract, no reviewers, no progress (SEC-04)
            item["chair_is_author"] = True
        else:
            item.update(abstract=s.abstract, chair_is_author=False,
                        authors=[{"name": u.name, "email": u.email, "affiliation": u.affiliation}
                                 for _, u in authors.get(s.id, [])],
                        reviewers_assigned=assigned.get(s.id, 0), reviews_done=done.get(s.id, 0))
        result.append(item)
    return jsonify(result)


# ---- the programme committee (UR-04) ----

@chair_bp.get("/conferences/<int:conference_id>/committee")
@chair_only
def committee(conference_id):
    """Reviewers of this conference and how many papers each has to review."""
    members = (db.session.query(User).join(ConferenceRole, ConferenceRole.user_id == User.id)
               .filter(ConferenceRole.conference_id == conference_id, ConferenceRole.role == "reviewer")
               .order_by(User.id).all())
    workload = dict(db.session.query(Assignment.reviewer_id, func.count(Assignment.id))
                    .join(Submission, Submission.id == Assignment.submission_id)
                    .filter(Submission.conference_id == conference_id).group_by(Assignment.reviewer_id).all())
    return jsonify([{"id": u.id, "name": u.name, "email": u.email, "affiliation": u.affiliation,
                     "assigned": workload.get(u.id, 0)} for u in members])


# ---- assign and unassign (UR-04, FR-05, SEC-04) ----

def find_submission(conference_id, submission_id):
    submission = db.session.get(Submission, submission_id)
    if submission is None or submission.conference_id != conference_id:
        return None
    return submission


@chair_bp.post("/conferences/<int:conference_id>/submissions/<int:submission_id>/assignments")
@chair_only
def assign_reviewer(conference_id, submission_id):
    submission = find_submission(conference_id, submission_id)
    if submission is None:
        return error("Submission not found", 404)
    authors = [(l, u) for l, u in authors_by_submission(conference_id).get(submission_id, [])]

    if chair_wrote(authors):     # A3: a chair must not steer the review of their own paper
        log_action(g.user.id, f"denied: chair tried to assign reviewers to own paper {submission_id}")
        db.session.commit()
        return error("You are an author of this paper, so you cannot assign reviewers to it", 403)
    if submission.status in ("withdrawn", "accepted", "rejected"):
        return error(f"This paper is {submission.status}", 409)

    data = request.get_json(silent=True) or {}
    if isinstance(data.get("reviewer_id"), int) and not isinstance(data.get("reviewer_id"), bool):
        reviewer = db.session.get(User, data["reviewer_id"])
    elif isinstance(data.get("reviewer_email"), str):
        reviewer = User.query.filter_by(email=data["reviewer_email"].strip().lower()).first()
    else:
        return error("Send reviewer_id or reviewer_email", 400)
    is_member = reviewer and ConferenceRole.query.filter_by(
        user_id=reviewer.id, conference_id=conference_id, role="reviewer").first()
    if not is_member:
        return error("This person is not a reviewer in this conference (invite them first)", 409)

    if any(u.id == reviewer.id for _, u in authors):       # SEC-04 / FR-05
        log_action(g.user.id, f"denied: reviewer {reviewer.id} is an author of paper {submission_id}")
        db.session.commit()
        return error("Conflict of interest: this reviewer is an author of the paper", 409)
    if Assignment.query.filter_by(submission_id=submission_id, reviewer_id=reviewer.id).first():
        return error("This reviewer is already assigned to this paper", 409)

    assignment = Assignment(submission_id=submission_id, reviewer_id=reviewer.id)
    db.session.add(assignment)
    if submission.status == "submitted":
        submission.status = "under review"
    db.session.flush()
    log_action(g.user.id, f"assigned reviewer {reviewer.id} to submission {submission_id}")
    db.session.commit()

    warnings = []
    mine = (reviewer.affiliation or "").strip().lower()
    if mine and any((u.affiliation or "").strip().lower() == mine for _, u in authors):
        warnings.append("This reviewer has the same affiliation as an author of the paper")
    return jsonify(assignment_id=assignment.id, submission_id=submission_id,
                   reviewer={"id": reviewer.id, "name": reviewer.name, "email": reviewer.email},
                   warnings=warnings), 201


@chair_bp.delete("/conferences/<int:conference_id>/submissions/<int:submission_id>/assignments/<int:assignment_id>")
@chair_only
def unassign_reviewer(conference_id, submission_id, assignment_id):
    """Take a reviewer off a paper (for a mistake). Not possible once the review is written."""
    submission = find_submission(conference_id, submission_id)
    assignment = db.session.get(Assignment, assignment_id)
    if submission is None or assignment is None or assignment.submission_id != submission_id:
        return error("Assignment not found", 404)
    if chair_wrote(authors_by_submission(conference_id).get(submission_id, [])):
        return error("You are an author of this paper", 403)
    if Review.query.filter_by(assignment_id=assignment_id).first():
        return error("This reviewer already wrote a review, it cannot be taken away", 409)
    reviewer_id = assignment.reviewer_id
    db.session.delete(assignment)
    db.session.flush()
    if submission.status == "under review" and not Assignment.query.filter_by(submission_id=submission_id).count():
        submission.status = "submitted"
    log_action(g.user.id, f"removed reviewer {reviewer_id} from submission {submission_id}")
    db.session.commit()
    return jsonify(status="removed")


# ---- review progress (UR-05, FR-06) ----

@chair_bp.get("/conferences/<int:conference_id>/review-progress")
@chair_only
def review_progress(conference_id):
    """Per paper: how many reviews are assigned and done, and who still has to write one."""
    conference = db.session.get(Conference, conference_id)
    deadline_passed = now() > as_utc(conference.review_deadline)
    authors = authors_by_submission(conference_id)
    pending = {}
    rows = (db.session.query(Assignment, User)
            .join(User, User.id == Assignment.reviewer_id)
            .join(Submission, Submission.id == Assignment.submission_id)
            .outerjoin(Review, Review.assignment_id == Assignment.id)
            .filter(Submission.conference_id == conference_id, Review.id.is_(None)).all())
    for assignment, user in rows:
        pending.setdefault(assignment.submission_id, []).append(
            {"assignment_id": assignment.id, "reviewer": {"id": user.id, "name": user.name, "email": user.email}})
    assigned, done = counts(conference_id)

    papers = []
    for s in conference_submissions(conference_id):
        if s.status == "withdrawn" or chair_wrote(authors.get(s.id, [])):   # own papers: no details (SEC-04)
            continue
        missing = pending.get(s.id, [])
        papers.append({"submission_id": s.id, "title": s.title, "assigned": assigned.get(s.id, 0),
                       "completed": done.get(s.id, 0), "missing_reviews": missing,
                       "needs_reviewers": assigned.get(s.id, 0) == 0})
    return jsonify(review_deadline=as_utc(conference.review_deadline).isoformat(), deadline_passed=deadline_passed,
                   overdue=[p["submission_id"] for p in papers if deadline_passed and p["missing_reviews"]],
                   papers=papers)
