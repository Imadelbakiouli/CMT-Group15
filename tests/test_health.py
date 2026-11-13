"""First tests: every server answers on /health."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_app(service):
    spec = importlib.util.spec_from_file_location(f"{service}_app", ROOT / service / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


@pytest.mark.parametrize("service", ["interface", "orchestrator", "cmt_service", "ai_agent"])
def test_health(service):
    client = load_app(service).test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
