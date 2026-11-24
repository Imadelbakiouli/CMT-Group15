"""Decisions (T11): the chair reads the reviews, records and releases decisions, authors see the result.

Related requirements: UR-06, UR-11, FR-07, FR-08, FR-09, FR-15, SEC-04, SEC-05, SEC-13.

How it goes:
1. The chair reads the reviews and average scores of a paper.
2. The chair records "accept" or "reject". This can be changed as long as it is not released.
3. The chair releases the decision. Only then the paper gets its final status, the authors can see the
   decision and the reviews, and an email goes to every author with the reviews attached.
   Reviews are always shown WITHOUT the name of the reviewer (SEC-05).

Conflict of interest (SEC-04, abuse scenario A3): a chair who is an author of a paper cannot read its
reviews, record or release its decision. (If the only chair wrote the paper, another chair must be added.)
"""
from statistics import mean

from flask import Blueprint, g, jsonify, request

from access import conference_role_required
from audit import log_action
from auth import error, login_required
from mailer import send_email
from models import (Assignment, Conference, Decision, Review, Submission, SubmissionAuthor, User, db)

decision_bp = Blueprint("decisions", __name__)
chair_only = conference_role_required("chair")
OUTCOMES = ("accept", "reject")


def authors_of(submission_id):
    return (db.session.query(User).join(SubmissionAuthor, SubmissionAuthor.user_id == User.id)
            .filter(SubmissionAuthor.submission_id == submission_id).order_by(User.id).all())


def reviews_of(submission_id):
    """[(Review, reviewer User)] in a fixed order."""
    return (db.session.query(Review, User).join(Assignment, Review.assignment_id == Assignment.id)
            .join(User, User.id == Assignment.reviewer_id)
            .filter(Assignment.submission_id == submission_id).order_by(Review.id).all())


def find_paper(conference_id, submission_id):
    """The paper, or an error response when it is not in this conference or the chair wrote it (SEC-04)."""
    submission = db.session.get(Submission, submission_id)
    if submission is None or submission.conference_id != conference_id:
        return None, error("Submission not found", 404)
    if any(u.id == g.user.id for u in authors_of(submission_id)):
        log_action(g.user.id, f"denied: chair is an author of paper {submission_id}")
        db.session.commit()
        return None, error("You are an author of this paper, so you cannot handle its reviews or decision", 403)
    return submission, None


def averages_of(scores):
    """scores is a list of (overall score, confidence)."""
    if not scores:
        return {"average_overall_score": None, "average_confidence": None}
    return {"average_overall_score": round(mean(o for o, _ in scores), 2),
            "average_confidence": round(mean(c for _, c in scores), 2)}


def averages(reviews):
    return averages_of([(r.overall_score, r.confidence) for r, _ in reviews])


def decision_state(submission_id):
    decision = Decision.query.filter_by(submission_id=submission_id).first()
    if decision is None:
        return decision, "none"
    return decision, "released" if decision.released else "recorded"


# ---- the chair reads reviews (UR-06, FR-07) ----

@decision_bp.get("/conferences/<int:conference_id>/submissions/<int:submission_id>/reviews")
@chair_only
def paper_reviews(conference_id, submission_id):
    submission, problem = find_paper(conference_id, submission_id)
    if problem:
        return problem
    reviews = reviews_of(submission_id)
    assigned = Assignment.query.filter_by(submission_id=submission_id).count()
    _, state = decision_state(submission_id)
    return jsonify(submission_id=submission_id, title=submission.title, status=submission.status,
                   assigned=assigned, completed=len(reviews), decision_state=state, **averages(reviews),
                   reviews=[{"reviewer": {"id": u.id, "name": u.name}, "overall_score": r.overall_score,
                             "confidence": r.confidence, "comments": r.comments} for r, u in reviews])


@decision_bp.get("/conferences/<int:conference_id>/results")
@chair_only
def results(conference_id):
    """Overview: every paper with its average score and the state of its decision.

    Three queries for the whole conference, not three per paper: with 1000 papers the one-by-one way took 7 seconds.
    """
    chair_papers = {row[0] for row in db.session.query(SubmissionAuthor.submission_id)
                    .join(Submission, Submission.id == SubmissionAuthor.submission_id)
                    .filter(Submission.conference_id == conference_id, SubmissionAuthor.user_id == g.user.id)}
    scores = {}
    for submission_id, overall, confidence in (
            db.session.query(Assignment.submission_id, Review.overall_score, Review.confidence)
            .join(Review, Review.assignment_id == Assignment.id)
            .join(Submission, Submission.id == Assignment.submission_id)
            .filter(Submission.conference_id == conference_id)):
        scores.setdefault(submission_id, []).append((overall, confidence))
    decisions = {d.submission_id: d for d in Decision.query.join(Submission, Submission.id == Decision.submission_id)
                 .filter(Submission.conference_id == conference_id)}

    papers = []
    for s in Submission.query.filter_by(conference_id=conference_id).order_by(Submission.id):
        if s.id in chair_papers:
            papers.append({"submission_id": s.id, "title": s.title, "chair_is_author": True})   # no details (SEC-04)
            continue
        decision = decisions.get(s.id)
        state = "none" if decision is None else "released" if decision.released else "recorded"
        papers.append({"submission_id": s.id, "title": s.title, "status": s.status, "chair_is_author": False,
                       "reviews_completed": len(scores.get(s.id, [])), **averages_of(scores.get(s.id, [])),
                       "decision_state": state, "outcome": decision.outcome if decision else None})
    return jsonify(papers)


# ---- record a decision (UR-06, FR-08) ----

@decision_bp.put("/conferences/<int:conference_id>/submissions/<int:submission_id>/decision")
@chair_only
def record_decision(conference_id, submission_id):
    """Record accept or reject. Can be changed until it is released."""
    submission, problem = find_paper(conference_id, submission_id)
    if problem:
        return problem
    outcome = (request.get_json(silent=True) or {}).get("outcome")
    if outcome not in OUTCOMES:
        return error('outcome must be "accept" or "reject"', 400)
    if submission.status == "withdrawn":
        return error("This paper was withdrawn", 409)
    decision, state = decision_state(submission_id)
    if state == "released":
        return error("This decision is already released to the authors and cannot be changed", 409)

    changed = decision is not None
    if decision is None:
        decision = Decision(submission_id=submission_id, outcome=outcome)
        db.session.add(decision)
    else:
        decision.outcome = outcome
    log_action(g.user.id, f"{'changed' if changed else 'recorded'} decision on paper {submission_id}: {outcome}")
    db.session.commit()

    warnings = []
    assigned = Assignment.query.filter_by(submission_id=submission_id).count()
    done = len(reviews_of(submission_id))
    if done == 0:
        warnings.append("There are no reviews for this paper yet")
    elif done < assigned:
        warnings.append(f"{assigned - done} of {assigned} reviews are still missing")
    return jsonify(submission_id=submission_id, outcome=outcome, released=False, warnings=warnings)


# ---- release and notify (UR-06, FR-09, FR-15) ----

def review_text(number, review):
    return (f"Review {number}\n\nOverall score (1-5): {review.overall_score}\n"
            f"Reviewer confidence (1-5): {review.confidence}\n\nComments:\n{review.comments}\n")


def release_one(conference, submission, decision):
    """Make the decision final and email the authors. Returns {"sent": [...], "failed": [...]}."""
    reviews = reviews_of(submission.id)
    decision.released = True
    submission.status = "accepted" if decision.outcome == "accept" else "rejected"
    log_action(g.user.id, f"released decision on paper {submission.id}: {decision.outcome}")
    db.session.commit()

    word = "accepted" if decision.outcome == "accept" else "rejected"
    attachments = [(f"review-{i}.txt", review_text(i, r)) for i, (r, _) in enumerate(reviews, start=1)]
    sent, failed = [], []
    for author in authors_of(submission.id):
        body = (f"Dear {author.name},\n\nYour paper \"{submission.title}\" for {conference.name} has been {word}.\n"
                f"The reviews are attached ({len(attachments)}). They do not show who wrote them.\n\n"
                f"You can also see the result in the conference system.\n")
        try:
            send_email(author.email, f"{conference.name}: decision on your paper", body, attachments)
            sent.append(author.email)
        except Exception:                 # a mail server problem must not undo the decision
            failed.append(author.email)
    if failed:
        log_action(g.user.id, f"email failed for decision on paper {submission.id} ({len(failed)} authors)")
        db.session.commit()
    return {"sent": sent, "failed": failed}


@decision_bp.post("/conferences/<int:conference_id>/submissions/<int:submission_id>/decision/release")
@chair_only
def release_decision(conference_id, submission_id):
    submission, problem = find_paper(conference_id, submission_id)
    if problem:
        return problem
    decision, state = decision_state(submission_id)
    if state == "none":
        return error("Record a decision first", 409)
    if state == "released":
        return error("This decision was already released", 409)
    outcome = decision.outcome
    emails = release_one(db.session.get(Conference, conference_id), submission, decision)
    return jsonify(submission_id=submission_id, outcome=outcome, released=True, emails=emails)


@decision_bp.post("/conferences/<int:conference_id>/decisions/release")
@chair_only
def release_all(conference_id):
    """Release every recorded decision of the conference at once (not for the chair's own papers)."""
    conference = db.session.get(Conference, conference_id)
    released, failed_mail = [], 0
    for s in Submission.query.filter_by(conference_id=conference_id).order_by(Submission.id):
        decision, state = decision_state(s.id)
        if state != "recorded" or s.status == "withdrawn" or any(u.id == g.user.id for u in authors_of(s.id)):
            continue
        result = release_one(conference, s, decision)
        released.append(s.id)
        failed_mail += len(result["failed"])
    return jsonify(released=released, emails_failed=failed_mail)


# ---- the author sees the result (UR-11, FR-15, SEC-05) ----

@decision_bp.get("/submissions/<int:submission_id>/result")
@login_required
def my_result(submission_id):
    """Decision and reviews of my own paper. Nothing before the chair released the decision."""
    submission = db.session.get(Submission, submission_id)
    is_author = submission and SubmissionAuthor.query.filter_by(
        submission_id=submission_id, user_id=g.user.id).first()
    if not is_author:
        return error("Submission not found", 404)
    decision, state = decision_state(submission_id)
    if state != "released":
        return jsonify(submission_id=submission_id, status=submission.status, decision_released=False)
    # reviews without any information about the reviewer; the order is not linked to a person
    return jsonify(submission_id=submission_id, status=submission.status, decision_released=True,
                   decision=decision.outcome,
                   reviews=[{"overall_score": r.overall_score, "confidence": r.confidence, "comments": r.comments}
                            for r, _ in reviews_of(submission_id)])
