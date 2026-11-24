"""50 test instructions for the AI assistant: (what the user types, the tool it should lead to, the arguments).

A tool of None means the assistant should only answer with text. The same list can be used to measure a real
language model later (NFR-10 asks for at least 90% correct on 50 instructions): see `accuracy()`.
"""
CASES = [
    # (instruction, tool, arguments as the planner returns them)
    ("show conferences", "list_conferences", {}),
    ("list all conferences", "list_conferences", {}),
    ("Please show me my conferences.", "list_conferences", {}),
    ("what are the conferences?", "list_conferences", {}),
    ("Can you list the conferences?", "list_conferences", {}),
    ("show all conferences", "list_conferences", {}),
    ("show conference 3", "show_conference", {"conference_id": "3"}),
    ("get the details of conference 12", "show_conference", {"conference_id": "12"}),
    ("Could you open conference 7 please", "show_conference", {"conference_id": "7"}),
    ("show me the info on conference 2", "show_conference", {"conference_id": "2"}),
    ("show my papers", "list_my_submissions", {}),
    ("list all my submissions", "list_my_submissions", {}),
    ("what are my submissions", "list_my_submissions", {}),
    ("show my assigned papers", "list_my_assignments", {}),
    ("list my assignments", "list_my_assignments", {}),
    ("what do I have to review?", "list_my_assignments", {}),
    ("show me my reviews to do", "list_my_assignments", {}),
    ("show my invitations", "list_my_invitations", {}),
    ("list all invitations", "list_my_invitations", {}),
    ("accept invitation 5", "accept_invitation", {"invitation_id": "5"}),
    ("please accept the invitation 12", "accept_invitation", {"invitation_id": "12"}),
    ("I want to accept invitation 3", "accept_invitation", {"invitation_id": "3"}),
    ("decline invitation 5", "decline_invitation", {"invitation_id": "5"}),
    ("decline the invitation 2", "decline_invitation", {"invitation_id": "2"}),
    ("show the result of paper 4", "show_my_result", {"submission_id": "4"}),
    ("get me the decision for my submission 9", "show_my_result", {"submission_id": "9"}),
    ("show result of submission 3", "show_my_result", {"submission_id": "3"}),
    ("show the submissions of conference 1", "list_conference_submissions", {"conference_id": "1"}),
    ("list all papers in conference 2", "list_conference_submissions", {"conference_id": "2"}),
    ("show me all the submissions for conference 5", "list_conference_submissions", {"conference_id": "5"}),
    ("show the review progress of conference 1", "review_progress", {"conference_id": "1"}),
    ("get progress for conference 4", "review_progress", {"conference_id": "4"}),
    ("show me the progress of conference 3", "review_progress", {"conference_id": "3"}),
    ("show the committee of conference 1", "list_committee", {"conference_id": "1"}),
    ("list the reviewers in conference 2", "list_committee", {"conference_id": "2"}),
    ("show the results of conference 1", "conference_results", {"conference_id": "1"}),
    ("get me the results for conference 6", "conference_results", {"conference_id": "6"}),
    ("assign anna@example.org to paper 4 in conference 1", "assign_reviewer",
     {"reviewer_email": "anna@example.org", "submission_id": "4", "conference_id": "1"}),
    ("assign reviewer bob@uni.nl to submission 12 of conference 3", "assign_reviewer",
     {"reviewer_email": "bob@uni.nl", "submission_id": "12", "conference_id": "3"}),
    ("Could you assign Carl@Example.com to paper 7 in conference 2?", "assign_reviewer",
     {"reviewer_email": "carl@example.com", "submission_id": "7", "conference_id": "2"}),
    ("accept paper 4 in conference 1", "record_decision", {"outcome": "accept", "submission_id": "4", "conference_id": "1"}),
    ("reject submission 5 of conference 2", "record_decision", {"outcome": "reject", "submission_id": "5", "conference_id": "2"}),
    ("please reject the paper 9 in conference 3", "record_decision", {"outcome": "reject", "submission_id": "9", "conference_id": "3"}),
    ("release the decision of paper 4 in conference 1", "release_decision", {"submission_id": "4", "conference_id": "1"}),
    ("release decision for submission 8 of conference 2", "release_decision", {"submission_id": "8", "conference_id": "2"}),
    ("withdraw paper 4", "withdraw_submission", {"submission_id": "4"}),
    ("withdraw my submission 11", "withdraw_submission", {"submission_id": "11"}),
    ("please withdraw the paper 3", "withdraw_submission", {"submission_id": "3"}),
    ("help", None, {}),
    ("what can you do?", None, {}),
]


def accuracy(plan_function, cases=CASES):
    """Share of instructions that lead to the right tool with the right arguments. `plan_function(text)` must
    return (tool name or None, arguments as strings). Use it for the rule-based planner and for a language model."""
    right = 0
    for text, tool, args in cases:
        got_tool, got_args = plan_function(text)
        right += (got_tool == tool and (tool is None or got_args == args))
    return right / len(cases)
