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
