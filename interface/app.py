"""Interface server: the website users see. Forwards requests to the orchestrator."""
import os
import re

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

from web_security import init_security  # CSRF protection and security headers (T05)

# The web pages (HTML, JavaScript, CSS) live in interface/static/
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
init_security(app)
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:5001")
app.config["MAX_CONTENT_LENGTH"] = 21 * 1024 * 1024   # a 20 MB PDF plus the rest of the form

SAFE_PATH = re.compile(r"[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*")   # same rule as the orchestrator
PASS_ON_REQUEST_HEADERS = ("Authorization", "Content-Type", "Accept")
PASS_ON_RESPONSE_HEADERS = ("Content-Type", "Content-Disposition")

# Web pages: the login token is kept in this cookie. HttpOnly = JavaScript can never read it,
# so even a script injected into the page could not steal the login (SEC-01, SEC-09).
SESSION_COOKIE = "cmt_session"


@app.route("/")
def index():
    """The web page (one page with a login part and a dashboard part)."""
    return send_from_directory(STATIC_DIR, "index.html")


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
    if "Authorization" not in headers and request.cookies.get(SESSION_COOKIE):
        headers["Authorization"] = f"Bearer {request.cookies[SESSION_COOKIE]}"   # web page: login from the cookie
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
    if target == "cmt":
        remember_login(path, answer, response)
    return response


def remember_login(path, answer, response):
    """Put the login token in the HttpOnly cookie after logging in, and remove it when the login ends."""
    secure = os.environ.get("COOKIE_SECURE", "true").lower() != "false"
    if path == "auth/login" and answer.status_code == 200:
        try:
            token = answer.json().get("token")
        except ValueError:
            token = None
        if token:
            response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Strict", secure=secure)
    elif answer.status_code == 401 or (path in ("auth/logout", "auth/me") and request.method in ("POST", "DELETE")
                                       and answer.status_code < 300):
        response.delete_cookie(SESSION_COOKIE, samesite="Strict", secure=secure)   # logged out, expired or deleted


METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@app.route("/api/cmt/<path:path>", methods=METHODS)
def api_cmt(path):
    return to_orchestrator("cmt", path)


@app.route("/api/ai/<path:path>", methods=METHODS)
def api_ai(path):
    return to_orchestrator("ai", path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
