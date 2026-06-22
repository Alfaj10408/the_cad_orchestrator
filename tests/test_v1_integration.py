# tests/test_v1_integration.py
import sys, time
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path: sys.path.insert(0, str(_BACKEND))

def test_v1_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("API_DB_PATH", str(tmp_path/"api.db"))
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")
    monkeypatch.setenv("API_KEY_SALT", "test-salt-integration")
    monkeypatch.setenv("API_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("API_RETENTION_ENABLED", "0")
    import importlib
    from app.core import config as cfg; importlib.reload(cfg)
    from app.core import paths as app_paths; importlib.reload(app_paths)  # reset any test contamination
    from app.services import claude_generation, job_service
    async def fake_run(project_id, job_id):
        j = job_service.get_job(job_id); j.status="COMPLETED"; j.stage="COMPLETED"; job_service.save_job(j)
    monkeypatch.setattr(claude_generation, "run", fake_run)
    from app import main as m; importlib.reload(m)
    from fastapi.testclient import TestClient
    with TestClient(m.app) as c:                       # triggers lifespan (worker starts)
        assert c.get("/v1/healthz").status_code == 200
        k = c.post("/v1/admin/keys", json={"user_name":"u"},
                   headers={"Authorization":"Bearer admin-secret"}).json()["key"]
        h = {"Authorization": f"Bearer {k}"}
        jid = c.post("/v1/jobs", json={"prompt":"a 40x30x10 block"}, headers=h).json()["job_id"]
        ok=False
        for _ in range(100):
            if c.get(f"/v1/jobs/{jid}", headers=h).json()["status"]=="completed": ok=True; break
            time.sleep(0.05)
        assert ok
