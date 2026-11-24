"""Turning a sentence of the user into one tool call (T13).

`Planner.plan(message)` gets ONLY the sentence the user typed. It never sees what the system answers or what is
written in papers and reviews, so text in a PDF cannot steer it (SEC-08). It returns at most one action.

`RuleBasedPlanner` works without a language model or an API key: it understands a fixed set of sentences. It is the
planner for tests and for a demo. A language model can be plugged in later by writing another class with the same
`plan` method that is given `agent_tools.TOOLS` as its tools; the result is checked by the same validation anyway.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    kind: str                  # "tool" (do this) or "reply" (just answer with text)
    tool: str = ""
    args: dict = None
    text: str = ""


HELP_TEXT = ("I can do one thing at a time. For example: " + "; ".join([
    "show conferences", "show conference 1", "show my papers", "show my assignments", "show my invitations",
    "accept invitation 3", "show the result of paper 4", "show the submissions of conference 1",
    "show the review progress of conference 1", "show the committee of conference 1", "show the results of conference 1",
    "assign name@example.org to paper 4 in conference 1", "accept paper 4 in conference 1",
    "release the decision of paper 4 in conference 1", "withdraw paper 4"]) + ".")

NOT_UNDERSTOOD = "I did not understand that, so I did nothing. Ask for one thing at a time, or say 'help'."

PAPER = r"(?:submission|paper)"
N = r"(\d+)"
# (tool, sentence pattern with named groups). The whole sentence must match: extra text means no action.
PATTERNS = [
    ("list_conferences", r"(?:show|list|what are)(?: me)?(?: all| the)?(?: my)? conferences"),
    ("show_conference", rf"(?:show|get|open)(?: me)?(?: the)?(?: details of| info on)? conference (?P<conference_id>\d+)"),
    ("list_my_submissions", r"(?:show|list|what are)(?: me)?(?: all)? my (?:submissions|papers)"),
    ("list_my_assignments", r"(?:show|list|what are)(?: me)?(?: all)?(?: my)? (?:assigned (?:papers|submissions)|assignments|reviews to do)"
                            r"|what do i (?:have to|need to) review"),
    ("list_my_invitations", r"(?:show|list)(?: me)?(?: all)?(?: my)? invitations"),
    ("accept_invitation", r"accept(?: the)? invitation (?P<invitation_id>\d+)"),
    ("decline_invitation", r"decline(?: the)? invitation (?P<invitation_id>\d+)"),
    ("show_my_result", rf"(?:show|get)(?: me)?(?: the)?(?: result| decision)(?: of| for| on)?(?: my)? {PAPER} (?P<submission_id>\d+)"),
    ("list_conference_submissions", rf"(?:show|list)(?: me)?(?: all)?(?: the)? (?:submissions|papers)(?: of| in| for) conference (?P<conference_id>\d+)"),
    ("review_progress", r"(?:show|get)(?: me)?(?: the)? (?:review )?progress(?: of| in| for)? conference (?P<conference_id>\d+)"),
    ("list_committee", r"(?:show|list)(?: me)?(?: the)? (?:committee|reviewers)(?: of| in| for)? conference (?P<conference_id>\d+)"),
    ("conference_results", r"(?:show|get)(?: me)?(?: the)? results(?: of| in| for)? conference (?P<conference_id>\d+)"),
    ("assign_reviewer", rf"assign(?: reviewer)? (?P<reviewer_email>\S+@\S+) to {PAPER} (?P<submission_id>\d+)(?: in| of) conference (?P<conference_id>\d+)"),
    ("record_decision", rf"(?P<outcome>accept|reject)(?: the)? {PAPER} (?P<submission_id>\d+)(?: in| of) conference (?P<conference_id>\d+)"),
    ("release_decision", rf"release(?: the)? decision(?: of| for| on)? {PAPER} (?P<submission_id>\d+)(?: in| of) conference (?P<conference_id>\d+)"),
    ("withdraw_submission", rf"withdraw(?: my)?(?: the)? {PAPER} (?P<submission_id>\d+)"),
]
COMPILED = [(tool, re.compile(pattern)) for tool, pattern in PATTERNS]
POLITE_START = re.compile(r"^(?:(?:please|hey|hi|hello|ok|okay)\b[ ,]*|(?:can|could|would) you(?: please)? |"
                          r"(?:i want to|i would like to|i'd like to|i need to|let's|let us) )+")


def normalize(message):
    text = " ".join(message.lower().split())
    text = POLITE_START.sub("", text)
    text = re.sub(r"(?: please)?[ .!?]*$", "", text)
    return text


class RuleBasedPlanner:
    def plan(self, message):
        text = normalize(message)
        if re.fullmatch(r"help|what can you do", text):
            return Plan("reply", text=HELP_TEXT)
        for tool, pattern in COMPILED:
            found = pattern.fullmatch(text)
            if found:
                return Plan("tool", tool=tool, args=found.groupdict())
        return Plan("reply", text=NOT_UNDERSTOOD)
