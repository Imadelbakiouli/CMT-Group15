"""Orchestrator (T12): routes requests from the interface to the CMT service and the AI agent.

The interface sends everything here. We pass it on to the right component and give the answer back.
  /api/cmt/<path>  ->  CMT service  /<path>
  /api/ai/<path>   ->  AI agent     /<path>

The orchestrator does not log anyone in or check roles. The CMT service does that for every
request (SEC-03), we only pass the Authorization header along.
"""
import os
import re

import requests
from flask import Flask, Response, jsonify, request

app = Flask(__name__)

MAX_REQUEST_BYTES = 21 * 1024 * 1024   # a 20 MB PDF plus the rest of the form
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

SERVICES = {
    "cmt_service": os.environ.get("CMT_SERVICE_URL", "http://localhost:5002"),
    "ai_agent": os.environ.get("AI_AGENT_URL", "http://localhost:5003"),
}

# Deny by default: only these first parts of a path are passed on. A new route in a
# component with a new first part (for example /submissions) must be added here.
ALLOWED_CMT = {"auth", "conferences", "invitations", "assignments", "submissions", "decisions"}
ALLOWED_AI = {"chat"}

SAFE_PATH = re.compile(r"[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*")   # no dots, no //, no scheme
PASS_ON_REQUEST_HEADERS = ("Authorization", "Content-Type", "Accept")
PASS_ON_RESPONSE_HEADERS = ("Content-Type", "Content-Disposition")
TIMEOUT_CMT = 10    # seconds
TIMEOUT_AI = 15     # NFR-02: the AI should answer within 10 seconds for most requests


def json_error(message, status):
    return jsonify(error=message), status


def forward(base_url, path, allowed, timeout):
    """Send the current request to base_url/path and return the answer unchanged."""
    if not SAFE_PATH.fullmatch(path):
        return json_error("Bad request", 400)
    if path.split("/")[0] not in allowed:
        return json_error("Not found", 404)

    headers = {h: request.headers[h] for h in PASS_ON_REQUEST_HEADERS if h in request.headers}
    try:
        answer = requests.request(
            request.method, f"{base_url}/{path}", params=request.args,
            data=request.get_data(), headers=headers, timeout=timeout)
    except requests.Timeout:
        return json_error("The service took too long to answer", 504)
    except requests.RequestException:
        return json_error("The service cannot be reached", 502)

    response = Response(answer.content, status=answer.status_code)
    for name in PASS_ON_RESPONSE_HEADERS:    # nothing else (no cookies, no server info)
        if name in answer.headers:
            response.headers[name] = answer.headers[name]
    return response


METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@app.route("/api/cmt/<path:path>", methods=METHODS)
def to_cmt_service(path):
    return forward(SERVICES["cmt_service"], path, ALLOWED_CMT, TIMEOUT_CMT)


@app.route("/api/ai/<path:path>", methods=METHODS)
def to_ai_agent(path):
    return forward(SERVICES["ai_agent"], path, ALLOWED_AI, TIMEOUT_AI)


@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
def plain_errors(e):
    return json_error(e.name, e.code)


@app.route("/health")
def health():
    return jsonify(service="orchestrator", status="ok")


@app.route("/health/all")
def health_all():
    result = {"orchestrator": "ok"}
    for name, url in SERVICES.items():
        try:
            result[name] = requests.get(f"{url}/health", timeout=3).json().get("status", "unknown")
        except requests.RequestException:
            result[name] = "unreachable"
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)))
