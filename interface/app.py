"""Interface server: the website users see. Forwards requests to the orchestrator."""
import os
import re

import requests
from flask import Flask, Response, jsonify, request

from web_security import init_security  # CSRF protection and security headers (T05)

app = Flask(__name__)
init_security(app)
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:5001")
app.config["MAX_CONTENT_LENGTH"] = 21 * 1024 * 1024   # a 20 MB PDF plus the rest of the form

SAFE_PATH = re.compile(r"[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*")   # same rule as the orchestrator
PASS_ON_REQUEST_HEADERS = ("Authorization", "Content-Type", "Accept")
PASS_ON_RESPONSE_HEADERS = ("Content-Type", "Content-Disposition")


@app.route("/")
def index():
    return "CMT Group 15 - interface server is running"


@app.route("/health")
def health():
    return jsonify(service="interface", status="ok")


@app.route("/health/all")
def health_all():
    """Checks whether the orchestrator (and through it the other services) can be reached."""
    try:
        r = requests.get(f"{ORCHESTRATOR_URL}/health/all", timeout=3)
        return jsonify(interface="ok", backend=r.json())
    except requests.RequestException:
        return jsonify(interface="ok", backend="unreachable"), 503


def to_orchestrator(target, path):
    """Pass an API request on to the orchestrator and give the answer back unchanged."""
    if not SAFE_PATH.fullmatch(path):
        return jsonify(error="Bad request"), 400
    headers = {h: request.headers[h] for h in PASS_ON_REQUEST_HEADERS if h in request.headers}
    try:
        answer = requests.request(request.method, f"{ORCHESTRATOR_URL}/api/{target}/{path}",
                                  params=request.args, data=request.get_data(), headers=headers, timeout=20)
    except requests.Timeout:
        return jsonify(error="The server took too long to answer"), 504
    except requests.RequestException:
        return jsonify(error="The server cannot be reached"), 502
    response = Response(answer.content, status=answer.status_code)
    for name in PASS_ON_RESPONSE_HEADERS:
        if name in answer.headers:
            response.headers[name] = answer.headers[name]
    return response


METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@app.route("/api/cmt/<path:path>", methods=METHODS)
def api_cmt(path):
    return to_orchestrator("cmt", path)


@app.route("/api/ai/<path:path>", methods=METHODS)
def api_ai(path):
    return to_orchestrator("ai", path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
