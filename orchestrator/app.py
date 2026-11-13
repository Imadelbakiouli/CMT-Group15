"""Orchestrator: routes requests from the interface to the right component."""
import os
import requests
from flask import Flask, jsonify

app = Flask(__name__)
SERVICES = {
    "cmt_service": os.environ.get("CMT_SERVICE_URL", "http://localhost:5002"),
    "ai_agent": os.environ.get("AI_AGENT_URL", "http://localhost:5003"),
}


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
