"""Calling the CMT service for the assistant (T13). Always with the token of the logged-in user (SEC-07)."""
import os

import requests

from agent_tools import build_request

TIMEOUT = 10


def call(tool, args, token):
    """Do one tool call. Returns (HTTP status, answer as JSON). Never raises for a network problem."""
    method, path, body = build_request(tool, args)
    headers = {"Authorization": f"Bearer {token}", "X-Via": "ai-assistant"}   # the audit log shows it was the assistant
    try:
        answer = requests.request(method, os.environ.get("CMT_SERVICE_URL", "http://localhost:5002") + path,
                                  json=body, headers=headers, timeout=TIMEOUT)
    except requests.Timeout:
        return 504, {"error": "The system took too long to answer"}
    except requests.RequestException:
        return 502, {"error": "The system cannot be reached"}
    try:
        return answer.status_code, answer.json()
    except ValueError:
        return answer.status_code, {"error": "Unexpected answer from the system"}
