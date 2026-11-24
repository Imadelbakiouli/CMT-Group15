"""CSRF protection and security headers for the interface server (T05, SEC-12, SEC-09).

Why here: the browser talks to this server, so this is where cookies live. The CMT service uses
a Bearer token in a header, which a browser never adds by itself, but as soon as the interface
keeps the login token in a cookie, a hostile website could make the browser send it. So:

1. Every unsafe request (POST, PUT, PATCH, DELETE) must carry the header X-CSRF-Token,
   and it must match the csrf_token cookie (double-submit cookie).
2. The token is signed with a server secret, so a cookie planted by another site is refused.
3. If the browser sends an Origin (or Referer) header, it must be this site.
4. The cookie is SameSite=Strict, so browsers do not even send it from other sites.

How the chat page uses it: any GET (for example the page itself) sets the cookie. JavaScript reads
the cookie and sends its value in the X-CSRF-Token header on every POST/PUT/PATCH/DELETE,
including login. That cookie is deliberately readable by JavaScript; keep the *login token*
in a separate HttpOnly cookie.

Settings (environment): CSRF_SECRET (long random string; if empty a random one is made at start,
so tokens stop working after a restart), COOKIE_SECURE (default true; set false only to demo
over plain http), ALLOWED_ORIGINS (extra origins, comma separated).
"""
import hashlib
import hmac
import os
import secrets
from urllib.parse import urlparse

from flask import jsonify, request

COOKIE_NAME = "csrf_token"
HEADER_NAME = "X-CSRF-Token"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
NO_COOKIE_PATHS = ("/health",)           # monitoring calls do not need a token

_FALLBACK_SECRET = secrets.token_hex(32)

# This server only sends its own pages; scripts and styles come from this site and nowhere else.
HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; "
                                "img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _secret():
    return (os.environ.get("CSRF_SECRET") or _FALLBACK_SECRET).encode()


def _sign(value):
    return hmac.new(_secret(), value.encode(), hashlib.sha256).hexdigest()


def new_token():
    value = secrets.token_urlsafe(32)
    return f"{value}.{_sign(value)}"


def valid_token(token):
    """True only for a token this server made (right signature)."""
    if not isinstance(token, str) or token.count(".") != 1:
        return False
    value, signature = token.split(".")
    return hmac.compare_digest(signature, _sign(value))


def origin_ok():
    """A browser says where a request comes from. If it does, it must be this site."""
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        return True                          # not a browser (for example curl); the token still counts
    allowed = {request.host}
    allowed.update(o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip())
    allowed.update(urlparse(o).netloc for o in list(allowed))
    return urlparse(source).netloc in allowed


def csrf_protect():
    if request.method in SAFE_METHODS:
        return None
    if not origin_ok():
        return jsonify(error="Request from another site blocked"), 403
    cookie, header = request.cookies.get(COOKIE_NAME), request.headers.get(HEADER_NAME)
    if not cookie or not header or not hmac.compare_digest(cookie, header) or not valid_token(cookie):
        return jsonify(error="CSRF token missing or invalid"), 403
    return None


def add_headers_and_cookie(response):
    for name, value in HEADERS.items():
        response.headers[name] = value
    if not request.path.startswith(NO_COOKIE_PATHS) and not valid_token(request.cookies.get(COOKIE_NAME)):
        response.set_cookie(COOKIE_NAME, new_token(), samesite="Strict", httponly=False,
                            secure=os.environ.get("COOKIE_SECURE", "true").lower() != "false")
    return response


def init_security(app):
    """Call once in app.py. Protects every route, also the ones added later."""
    app.before_request(csrf_protect)
    app.after_request(add_headers_and_cookie)
