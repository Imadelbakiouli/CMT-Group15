"""First tests: check that every server answers on /health."""
import importlib.util
import pathlib
import sys

import pytest

# Root folder of the project (one level above tests/)
ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_app(service):
    """Load the Flask app from <service>/app.py so we can test it without starting a server."""
    service_dir = ROOT / service
    sys.path.insert(0, str(service_dir))  # lets app.py find its own files (e.g. models.py)
    try:
        spec = importlib.util.spec_from_file_location(f"{service}_app", service_dir / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(service_dir))
    return module.app


# Run the same test once for each of the four servers
@pytest.mark.parametrize("service", ["interface", "orchestrator", "cmt_service", "ai_agent"])
def test_health(service):
    client = load_app(service).test_client()   # fake browser for testing
    response = client.get("/health")           # call the /health route
    assert response.status_code == 200        # 200 = OK
    assert response.get_json()["status"] == "ok"
