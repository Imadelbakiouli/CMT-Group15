"""AI agent (T13): turns what a user types into actions in the CMT, with the user's own rights.

POST /chat, header "Authorization: Bearer <token of the user>", JSON body one of:
    {"message": "withdraw paper 4"}   -> an answer, or a question for confirmation
    {"confirm": "<id>"}               -> do the action that was waiting for a yes
    {"cancel": "<id>"}                -> forget it

Answers always have "type": "reply" (only text), "result" (something was done or looked up, with "data"),
"confirmation_required" (with "action_id" and what will happen) or "error".

Why this is safe (SEC-07, SEC-08, FR-17), see docs/ai-assistant.md:
- every action is an ordinary API call with the user's own token, so the backend decides what is allowed
- one message leads to at most one action, and the answers of the system never go back into the planner, so text
  in a paper, abstract or review (which may be written by an attacker) can not make the assistant do anything
- withdrawing papers, recording and releasing decisions (which sends email) need a "yes" from the same user first
"""
import os

from flask import Flask, jsonify, request

import agent_backend
from agent_confirm import PendingActions, owner_of
from agent_planner import RuleBasedPlanner
from agent_tools import TOOLS, ToolError, confirm_text, validate

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024      # a chat message is short
MAX_MESSAGE = 2000
MAX_ITEMS = 50                                     # long lists are cut, the answer stays small

pending = PendingActions()
planner = RuleBasedPlanner()   # a language model planner can replace this (see docs/ai-assistant.md)
if os.environ.get("AI_PLANNER", "rules") != "rules":
    raise SystemExit("AI_PLANNER: only 'rules' is available yet")


@app.after_request
def headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
def plain_errors(e):
    return jsonify(type="error", message=e.name), e.code


@app.route("/health")
def health():
    return jsonify(service="ai_agent", status="ok")


def error(message, status):
    return jsonify(type="error", message=message), status


def bearer_token():
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") and len(header) > 7 else None


def shorten(data):
    if isinstance(data, list) and len(data) > MAX_ITEMS:
        return {"items": data[:MAX_ITEMS], "shown": MAX_ITEMS, "total": len(data)}
    return data


def run(tool, args, token):
    """Do the call and turn the system's answer into an assistant answer. The data is only shown, never acted on."""
    status, data = agent_backend.call(tool, args, token)
    if 200 <= status < 300:
        return jsonify(type="result", message=tool.description if tool.method == "GET" else "Done.", tool=tool.name,
                       data=shorten(data))
    message = data.get("error", "That did not work") if isinstance(data, dict) else "That did not work"
    if status == 401:
        message = "Your login has expired, please log in again"
    return error(message, status)


@app.post("/chat")
def chat():
    token = bearer_token()
    if token is None:
        return error("Log in first", 401)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Send a JSON object", 400)
    owner = owner_of(token)

    if "cancel" in data:
        pending.cancel(owner, data["cancel"]) if isinstance(data["cancel"], str) else None
        return jsonify(type="reply", message="Cancelled. Nothing was changed.")

    if "confirm" in data:
        entry = pending.take(owner, data["confirm"]) if isinstance(data["confirm"], str) else None
        if entry is None:
            return error("There is nothing to confirm (it may have expired, or was already done)", 410)
        return run(TOOLS[entry["tool"]], entry["args"], token)

    message = data.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE:
        return error(f"Send a message of at most {MAX_MESSAGE} characters", 400)

    plan = planner.plan(message)            # only the user's own sentence goes in, nothing from the system
    if plan.kind == "reply":
        return jsonify(type="reply", message=plan.text)
    tool = TOOLS[plan.tool]
    try:
        args = validate(tool, plan.args)
    except ToolError as e:
        return error(str(e), 400)
    if tool.risky:
        action_id = pending.add(owner, plan.tool, args, confirm_text(tool, args))
        if action_id is None:
            return error("Too many actions are waiting for confirmation", 429)
        return jsonify(type="confirmation_required", action_id=action_id, message=confirm_text(tool, args) + "?",
                       how="Send {\"confirm\": \"<action_id>\"} to do it or {\"cancel\": \"<action_id>\"} to stop")
    return run(tool, args, token)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5003)))
