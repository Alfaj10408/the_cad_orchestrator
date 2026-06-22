# F10 Admin Key Rotation (P2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Support multiple concurrent admin keys (`ADMIN_API_KEYS`) unioned with the legacy `ADMIN_API_KEY`, for zero-downtime rotation + revocation, plus a gated `GET /v1/admin/keys/info` diagnostics endpoint. No schema change.

**Architecture:** `config.admin_key_set()` derives the effective admin frozenset live from `config.ADMIN_API_KEY` ∪ `config.ADMIN_API_KEYS` (config attrs are set at process start; deriving per call keeps existing tests that monkeypatch `ADMIN_API_KEY` working). `auth._is_admin` compares the bearer against the set with `compare_digest` over all keys (no early return). A gated diagnostics endpoint reports key count + the authenticated key's `sha256[:8]` fingerprint. F9 boot guard + `serve_api.sh` accept either env var.

**Tech Stack:** Python 3.11 (`/root/anaconda3/envs/cadskills/bin/python`), FastAPI, hashlib/hmac, pytest.

## Global Constraints
- Engine frozen: **never** modify `backend/app/services` or `backend/app/orchestrator` — STOP/BLOCKED otherwise. Guard `git diff v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator` must stay empty.
- No CAD/frontend/benchmark/**schema** changes. No `db.*` change.
- Edits ONLY in `backend/app/core/config.py`, `backend/app/v1/auth.py`, `backend/app/v1/routes.py`, `backend/app/main.py`, `scripts/serve_api.sh`, `tests/test_v1_*`.
- Run from product root with cadskills python. Guard-check each commit.
- **Union model:** effective admin set = `ADMIN_API_KEY` (legacy) ∪ `ADMIN_API_KEYS` (comma-split, `.strip()`, drop empties, dedupe).
- **Restart-based + overlap** rotation; derive set from config attrs (NOT `os.environ`) per call.
- Validation compares **all** keys with `hmac.compare_digest`, ORs results (no early-return) — timing hygiene.
- Fingerprint: `hashlib.sha256(key.encode()).hexdigest()[:8]`.
- `ADMIN_KEYS_INFO_ENABLED` (default true) gates `/v1/admin/keys/info`; disabled → 404 (after `require_admin`).
- F9 guard: refuse boot if default salt AND effective admin set non-empty. `serve_api.sh`: require `ADMIN_API_KEY` OR `ADMIN_API_KEYS`.
- `require_admin` contract unchanged (403 on failure). Legacy single-key deployments must keep working.

---

## Task 1 — config `admin_key_set()` + auth set-based validation

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/v1/auth.py`
- Test: `tests/test_v1_admin_keys_unit.py`

**Interfaces — Produces:**
- `config.ADMIN_API_KEYS` (str, default ""), `config.ADMIN_KEYS_INFO_ENABLED` (bool, default True), `config.admin_key_set() -> frozenset[str]`.
- `auth._is_admin(authorization) -> bool` (set-based, compare-all); `auth.admin_fingerprint(token: str) -> str`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_v1_admin_keys_unit.py
import sys, hashlib
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest
from app.core import config as cfg
from app.v1 import auth


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "")
    monkeypatch.setattr(cfg, "ADMIN_API_KEYS", "")
    yield


def test_set_legacy_only(monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "old")
    assert cfg.admin_key_set() == frozenset({"old"})


def test_set_list_only(monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_API_KEYS", "a,b,c")
    assert cfg.admin_key_set() == frozenset({"a", "b", "c"})


def test_set_union_both(monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "old")
    monkeypatch.setattr(cfg, "ADMIN_API_KEYS", "new1,new2")
    assert cfg.admin_key_set() == frozenset({"old", "new1", "new2"})


def test_set_strips_and_drops_empties(monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_API_KEYS", " a , ,b ,, c ")
    assert cfg.admin_key_set() == frozenset({"a", "b", "c"})


def test_set_dedupes(monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "k")
    monkeypatch.setattr(cfg, "ADMIN_API_KEYS", "k,k2")
    assert cfg.admin_key_set() == frozenset({"k", "k2"})


def test_set_empty(monkeypatch):
    assert cfg.admin_key_set() == frozenset()


def _bearer(tok):
    return f"Bearer {tok}"


def test_is_admin_any_key_in_set(monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_API_KEYS", "k1,k2")
    assert auth._is_admin(_bearer("k1")) is True
    assert auth._is_admin(_bearer("k2")) is True


def test_is_admin_legacy_key(monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "legacy")
    assert auth._is_admin(_bearer("legacy")) is True


def test_is_admin_not_in_set(monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_API_KEYS", "k1")
    assert auth._is_admin(_bearer("nope")) is False


def test_is_admin_empty_set(monkeypatch):
    assert auth._is_admin(_bearer("anything")) is False


def test_is_admin_no_bearer(monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_API_KEYS", "k1")
    assert auth._is_admin(None) is False
    assert auth._is_admin("Token x") is False


def test_admin_fingerprint_format():
    fp = auth.admin_fingerprint("secret")
    assert fp == hashlib.sha256(b"secret").hexdigest()[:8]
    assert len(fp) == 8
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_admin_keys_unit.py -v`
Expected: FAIL (`config has no attribute 'admin_key_set'` / `auth has no attribute 'admin_fingerprint'`).

- [ ] **Step 3: Implement config** — in `backend/app/core/config.py`, after the existing `ADMIN_API_KEY` line (71):
```python
ADMIN_API_KEYS = os.environ.get("ADMIN_API_KEYS", "")
ADMIN_KEYS_INFO_ENABLED = _flag("ADMIN_KEYS_INFO_ENABLED", "1")


def admin_key_set() -> frozenset[str]:
    """Effective admin keys: union of ADMIN_API_KEY (legacy) and the
    comma-separated ADMIN_API_KEYS. Derived from config attributes (set at
    process start) so rotation = update env + restart; reading attrs (not
    os.environ) keeps tests that monkeypatch ADMIN_API_KEY working."""
    keys = set()
    if ADMIN_API_KEY:
        keys.add(ADMIN_API_KEY)
    for k in ADMIN_API_KEYS.split(","):
        k = k.strip()
        if k:
            keys.add(k)
    return frozenset(keys)
```
IMPORTANT: `admin_key_set()` must read the **module-level names** `ADMIN_API_KEY`/`ADMIN_API_KEYS` (bare global refs, as written) so `monkeypatch.setattr(config, "ADMIN_API_KEY", ...)` is observed.

- [ ] **Step 4: Implement auth** — in `backend/app/v1/auth.py`, replace `_is_admin` and add `admin_fingerprint`:
```python
def admin_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:8]

def _is_admin(authorization: str | None) -> bool:
    token = _bearer(authorization)
    if not token:
        return False
    ok = False
    for k in config.admin_key_set():
        if hmac.compare_digest(token, k):
            ok = True          # compare all keys, no early return (timing)
    return ok
```
(`hashlib`, `hmac`, `config` already imported in auth.py. `require_admin` unchanged.)

- [ ] **Step 5: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_admin_keys_unit.py -v` → all pass.

- [ ] **Step 6: Regression — existing admin auth tests** —
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_auth.py tests/test_v1_admin.py tests/test_v1_quota_admin.py -q
```
Expected: all pass (legacy `monkeypatch.setattr(config,"ADMIN_API_KEY",...)` now flows through `admin_key_set()`).

- [ ] **Step 7: Engine-freeze guard + commit**
```bash
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # must be empty
git add backend/app/core/config.py backend/app/v1/auth.py tests/test_v1_admin_keys_unit.py
git commit -m "feat(v1): admin key set (union) + set-based validation + fingerprint (F10)"
```

**Success:** union/strip/dedupe/empty parse; any-key-in-set authenticates; legacy key works; not-in-set/empty/no-bearer → False; fingerprint == sha256(key)[:8]; existing admin tests green; guard empty.

---

## Task 2 — gated `GET /v1/admin/keys/info` diagnostics endpoint

**Files:**
- Modify: `backend/app/v1/routes.py`
- Test: `tests/test_v1_admin_keys_api.py`

**Interfaces — Consumes:** `auth.require_admin`, `auth.admin_fingerprint`, `auth._bearer`, `config.admin_key_set`, `config.ADMIN_KEYS_INFO_ENABLED`. **Produces:** `GET /v1/admin/keys/info`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_v1_admin_keys_api.py
import sys, hashlib
from pathlib import Path
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.v1 import db, routes, auth
from app.core import config as cfg


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "API_DB_PATH", str(tmp_path / "r.db"))
    monkeypatch.setattr(cfg, "ADMIN_API_KEY", "")
    monkeypatch.setattr(cfg, "ADMIN_API_KEYS", "adminA,adminB")
    monkeypatch.setattr(cfg, "ADMIN_KEYS_INFO_ENABLED", True)
    c = db.connect(str(tmp_path / "r.db")); db.init_db(c)
    key, _p, _k, _u = auth.mint_key(c, "u1")   # normal user
    c.commit(); c.close()
    app = FastAPI(); app.include_router(routes.router)
    tc = TestClient(app); tc._ukey = key
    return tc


def test_info_count_and_fingerprint(client):
    r = client.get("/v1/admin/keys/info", headers={"Authorization": "Bearer adminA"})
    assert r.status_code == 200
    body = r.json()
    assert body["admin_keys_configured"] == 2
    assert body["authenticated_fingerprint"] == hashlib.sha256(b"adminA").hexdigest()[:8]


def test_info_fingerprint_differs_per_key(client):
    r = client.get("/v1/admin/keys/info", headers={"Authorization": "Bearer adminB"})
    assert r.json()["authenticated_fingerprint"] == hashlib.sha256(b"adminB").hexdigest()[:8]


def test_info_requires_admin(client):
    r = client.get("/v1/admin/keys/info", headers={"Authorization": f"Bearer {client._ukey}"})
    assert r.status_code == 403


def test_info_disabled_404(client, monkeypatch):
    monkeypatch.setattr(cfg, "ADMIN_KEYS_INFO_ENABLED", False)
    r = client.get("/v1/admin/keys/info", headers={"Authorization": "Bearer adminA"})
    assert r.status_code == 404
    # still admin-gated first: non-admin gets 403 even when disabled
    r2 = client.get("/v1/admin/keys/info", headers={"Authorization": f"Bearer {client._ukey}"})
    assert r2.status_code == 403


def test_info_overlap_both_keys_work(client):
    for k in ("adminA", "adminB"):
        assert client.get("/v1/admin/keys/info",
                          headers={"Authorization": f"Bearer {k}"}).status_code == 200
```

- [ ] **Step 2: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_admin_keys_api.py -v`
Expected: FAIL (404 — no route).

- [ ] **Step 3: Implement route** — in `backend/app/v1/routes.py`, near the other `/admin/*` routes, add (note: needs the raw `Authorization` header to fingerprint the calling key — use FastAPI `Header`):
```python
from fastapi import Header   # add to the fastapi import line if not present

@router.get("/admin/keys/info")
def admin_keys_info(_: bool = Depends(auth.require_admin),
                    authorization: str | None = Header(default=None)):
    if not config.ADMIN_KEYS_INFO_ENABLED:
        raise HTTPException(status_code=404, detail="not found")
    token = auth._bearer(authorization) or ""
    return {"admin_keys_configured": len(config.admin_key_set()),
            "authenticated_fingerprint": auth.admin_fingerprint(token)}
```
(`HTTPException`, `Depends`, `config`, `auth` already imported in routes.py. Confirm `Header` is imported — add it to the existing `from fastapi import ...` line if missing.)

- [ ] **Step 4: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_admin_keys_api.py -v` → all pass.

- [ ] **Step 5: Engine-freeze guard + commit**
```bash
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # must be empty
git add backend/app/v1/routes.py tests/test_v1_admin_keys_api.py
git commit -m "feat(v1): gated admin keys/info diagnostics endpoint (F10)"
```

**Success:** count + fingerprint correct; fingerprint per-key; require_admin 403; disabled → 404 (admin-gated first); overlap both keys 200; guard empty.

---

## Task 3 — F9 guard + serve_api.sh + verification

**Files:**
- Modify: `backend/app/main.py`, `scripts/serve_api.sh`
- Test: `tests/test_v1_f9_salt.py` (update to the set-based guard)

**Interfaces — Consumes:** `config.admin_key_set`. **Produces:** F9 guard fires on `ADMIN_API_KEYS` too; shell guard accepts either var.

- [ ] **Step 1: Inspect the existing F9 test** — `cd /root/all_project_models/alfaj/text-to-cad-product && sed -n '1,80p' tests/test_v1_f9_salt.py` to see how it sets env + asserts the boot guard.

- [ ] **Step 2: Add a failing test** — the existing tests fire the guard via the **lifespan** (`with TestClient(m.app):` raises on startup), NOT via `importlib.reload`. Match that exactly. The file already has a `_reload(monkeypatch)`-style helper that sets env + reloads `app.core.config` then `app.main` (see lines 13-14: `importlib.reload(c)`; `importlib.reload(m)`) and returns `m`. Append a case mirroring `test_default_salt_with_admin_refuses_boot` (line 17) but setting `ADMIN_API_KEYS` instead of `ADMIN_API_KEY`:
```python
def test_default_salt_with_admin_keys_refuses_boot(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY_SALT", "dev-salt-change-me")
    monkeypatch.setenv("ADMIN_API_KEY", "")
    monkeypatch.setenv("ADMIN_API_KEYS", "k1,k2")
    m = _reload(monkeypatch)          # use the file's existing reload helper
    with pytest.raises(Exception):
        with TestClient(m.app):       # lifespan startup raises
            pass
```
Use the file's actual helper name/signature as written (adapt the env-setting style to match — some cases use `monkeypatch.setenv`, the helper does the reload). Do NOT assert via `importlib.reload(m)` raising — the guard is in the lifespan, not at import.

- [ ] **Step 3: Run, verify fail** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_f9_salt.py -v`
Expected: the new case FAILS (current guard checks only `config.ADMIN_API_KEY`).

- [ ] **Step 4: Update the F9 guard** — in `backend/app/main.py`, change the guard condition (currently line ~30):
```python
    if config.API_KEY_SALT == "dev-salt-change-me" and config.admin_key_set():
        raise RuntimeError(
            "API_KEY_SALT is the default in a production deployment (admin key(s) set). "
            "Set a strong API_KEY_SALT.")
```

- [ ] **Step 5: Update `serve_api.sh`** — replace the `ADMIN_API_KEY` presence guard (line 4) with an either-var guard:
```bash
: "${ADMIN_API_KEY:=}"
: "${ADMIN_API_KEYS:=}"
if [ -z "$ADMIN_API_KEY" ] && [ -z "$ADMIN_API_KEYS" ]; then
  echo "set ADMIN_API_KEY or ADMIN_API_KEYS" >&2; exit 1
fi
```
(Keep the existing `API_KEY_SALT` non-default guard on the next line unchanged.)

- [ ] **Step 6: Run, verify pass** — `cd /root/all_project_models/alfaj/text-to-cad-product && /root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_f9_salt.py -v` → all pass (legacy cases + new ADMIN_API_KEYS case).

- [ ] **Step 7: /v1 + full suite + guard**
```bash
cd /root/all_project_models/alfaj/text-to-cad-product
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/test_v1_*.py -q       # all /v1
/root/anaconda3/envs/cadskills/bin/python -m pytest tests/ -q                   # full suite
git diff --stat v0.1-benchmark-10of10 -- backend/app/services backend/app/orchestrator   # empty
```
Expected: all pass; guard empty. (Confirms the 8 admin-using tests + F9 tests still green.)

- [ ] **Step 8: Commit**
```bash
git add backend/app/main.py scripts/serve_api.sh tests/test_v1_f9_salt.py
git commit -m "feat(v1): F9 guard + serve_api.sh accept ADMIN_API_KEYS (F10) + verification"
```

**Success:** F9 guard fires on default salt with either env var; shell guard accepts either; legacy single-key path intact; /v1 + full suites green; guard empty.

---

## Self-review
**Spec coverage:** union model (T1 `admin_key_set`) ✓; comma-split/strip/dedupe/empties (T1, tests) ✓; restart-based derive-from-config-attrs (T1, doc) ✓; compare-all no-early-return (T1 `_is_admin`) ✓; fingerprint `sha256(key.encode())[:8]` (T1 `admin_fingerprint`, asserted) ✓; `ADMIN_KEYS_INFO_ENABLED` default true (T1 config) ✓; diagnostics endpoint count+fingerprint, require_admin, disabled→404-after-admin (T2) ✓; overlap both keys (T2 test) ✓; F9 guard on effective set (T3) ✓; serve_api.sh either-var (T3) ✓; legacy compat (T1 step6 + T3 step7 regressions) ✓; no schema/CAD/frontend/benchmark/engine changes ✓.
**Placeholder scan:** none — all code concrete. (T3 step1/step2 instruct inspecting the existing F9 test to match its assertion mechanism; the new case + guard code are fully specified.)
**Type consistency:** `admin_key_set() -> frozenset[str]` used by `_is_admin` (T1), endpoint (T2), F9 guard (T3); `admin_fingerprint(token) -> str` used by endpoint (T2) and asserted (T1); `_is_admin(authorization)->bool` and `require_admin` contract unchanged. `auth._bearer` reused for the endpoint's fingerprint token.
