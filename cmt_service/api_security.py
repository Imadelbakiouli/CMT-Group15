"""Web security basics for the CMT service (T05).

Related requirements: SEC-03, SEC-09, SEC-12 (the role checks themselves are in access.py).

What this file does for every request, whatever the route:
- refuses bodies that are not JSON or a file upload (415), and JSON that is not an object (400),
  so a body like [1] gives a clean error instead of crashing a route with a 500
- refuses requests that are bigger than 20 MB plus form overhead (413, NFR-03 / SEC-10)
- always answers in JSON, also for 404, 405 and 500, and never shows a stack trace
- adds security headers, so a browser never treats an API answer as a web page (XSS)

Output escaping: we store text exactly as typed and send it as JSON. Whoever shows it on a
web page must insert it as text (textContent, or a template with autoescape), never as HTML.
"""
from flask import abort, jsonify, request
from werkzeug.exceptions import HTTPException

MAX_REQUEST_BYTES = 21 * 1024 * 1024          # a 20 MB PDF plus the rest of the form
BODY_TYPES = ("application/json", "multipart/form-data")
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

MESSAGES = {
    400: "Bad request",
    404: "Not found",
    405: "Method not allowed",
    413: "Request too large (a PDF can be 20 MB at most)",
    415: "Send JSON, or a file as multipart/form-data",
}

HEADERS = {
    "X-Content-Type-Options": "nosniff",                 # never guess that JSON is HTML
    "X-Frame-Options": "DENY",                           # cannot be shown in someone else's frame
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",                         # papers and reviews are not cached
    "Cross-Origin-Resource-Policy": "same-origin",
}


def check_request():
    """Runs before every route. Returning something stops the request."""
    if request.content_length and request.content_length > MAX_REQUEST_BYTES:
        abort(413)
    if request.method in UNSAFE_METHODS and request.content_length and request.mimetype:
        if request.mimetype not in BODY_TYPES:
            abort(415)                                   # e.g. a classic HTML form post
    if request.mimetype == "application/json" and request.content_length:
        body = request.get_json(silent=True)             # None means broken JSON: the route answers 400
        if body is not None and not isinstance(body, dict):
            return jsonify(error="The JSON body must be an object"), 400
    return None


def add_security_headers(response):
    for name, value in HEADERS.items():
        response.headers[name] = value
    return response


def json_error(e):
    """Same shape as the rest of the API: {"error": "..."}. 500 gives no details."""
    code = e.code or 500
    message = MESSAGES.get(code, "Internal server error" if code >= 500 else e.name)
    return jsonify(error=message), code


def init_security(app):
    """Call once in app.py, after the Flask app is created."""
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    app.before_request(check_request)
    app.after_request(add_security_headers)
    app.register_error_handler(HTTPException, json_error)
