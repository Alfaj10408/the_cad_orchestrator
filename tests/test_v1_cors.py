# tests/test_v1_cors.py
import sys, os, importlib
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))

def _reload(origins):
    """Reload config and main with V1_CORS_ORIGINS set or unset."""
    if origins is None: os.environ.pop("V1_CORS_ORIGINS", None)
    else: os.environ["V1_CORS_ORIGINS"] = origins
    # F9 startup guard: set non-default API_KEY_SALT before reload when ADMIN_API_KEY is set
    os.environ["API_KEY_SALT"] = "test-cors-salt"
    os.environ["API_RATE_LIMIT_ENABLED"] = "0"
    os.environ["API_RETENTION_ENABLED"] = "0"
    os.environ["API_REAP_ORPHAN_CLAUDE"] = "0"
    from app.core import config as cfg; importlib.reload(cfg)
    from app import main as m; importlib.reload(m)
    return m

def test_v1_cors_enabled(tmp_path, monkeypatch):
    """Test CORS middleware is installed and allow-origin header is sent when configured."""
    monkeypatch.setenv("API_DB_PATH", str(tmp_path/"api.db"))
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")

    m = _reload("https://app.example.com")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as c:
        resp = c.get("/v1/healthz", headers={"Origin": "https://app.example.com"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "https://app.example.com"

def test_v1_cors_disabled(tmp_path, monkeypatch):
    """Test CORS middleware is NOT installed when V1_CORS_ORIGINS is empty."""
    monkeypatch.setenv("API_DB_PATH", str(tmp_path/"api.db"))
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")

    m = _reload(None)  # unset V1_CORS_ORIGINS
    from fastapi.testclient import TestClient
    with TestClient(m.app) as c:
        resp = c.get("/v1/healthz", headers={"Origin": "https://disallowed.example.com"})
        assert resp.status_code == 200
        # CORS not enabled, so no allow-origin header
        assert "access-control-allow-origin" not in resp.headers

def test_v1_cors_multiple_origins(tmp_path, monkeypatch):
    """Test multiple CORS origins are allowed."""
    monkeypatch.setenv("API_DB_PATH", str(tmp_path/"api.db"))
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")

    m = _reload("https://app1.example.com,https://app2.example.com")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as c:
        resp1 = c.get("/v1/healthz", headers={"Origin": "https://app1.example.com"})
        assert resp1.status_code == 200
        assert resp1.headers.get("access-control-allow-origin") == "https://app1.example.com"

        resp2 = c.get("/v1/healthz", headers={"Origin": "https://app2.example.com"})
        assert resp2.status_code == 200
        assert resp2.headers.get("access-control-allow-origin") == "https://app2.example.com"
