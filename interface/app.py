"""Interface server: the website users see. Forwards requests to the orchestrator."""
import os
import requests
from flask import Flask, jsonify

app = Flask(__name__)
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:5001")


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
