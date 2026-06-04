"""Tests for the thin Home Assistant REST fetch layer."""

import json
import ssl
import urllib.request

import pytest

from meshmon.env import get_config
from meshmon.ha_source import fetch_states


class FakeResponse:
    """Minimal stand-in for the urlopen context manager."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_fetch_states_returns_list(ha_env, monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        captured["context"] = context
        return FakeResponse(json.dumps([{"entity_id": "sensor.x", "state": "1"}]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    cfg = get_config()
    result = fetch_states(cfg)

    assert result[0]["entity_id"] == "sensor.x"
    assert captured["url"] == "http://ha.local:8123/api/states"
    assert captured["auth"] == "Bearer test-token"
    assert captured["timeout"] == cfg.ha_timeout_s
    assert captured["context"] is None  # plain HTTP, no TLS context


def test_fetch_states_missing_credentials(configured_env, monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "ha")
    import meshmon.env

    meshmon.env._config = None
    cfg = get_config()
    with pytest.raises(RuntimeError, match="HA_URL and HA_TOKEN"):
        fetch_states(cfg)


def test_fetch_states_rejects_non_list(ha_env, monkeypatch):
    def fake_urlopen(request, timeout=None, context=None):
        return FakeResponse(json.dumps({"not": "a list"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cfg = get_config()
    with pytest.raises(RuntimeError, match="Unexpected /api/states"):
        fetch_states(cfg)


def test_fetch_states_insecure_tls(configured_env, monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "ha")
    monkeypatch.setenv("HA_URL", "https://ha.local:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.setenv("HA_VERIFY_TLS", "0")
    import meshmon.env

    meshmon.env._config = None

    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["context"] = context
        return FakeResponse(b"[]")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    cfg = get_config()
    assert fetch_states(cfg) == []
    assert isinstance(captured["context"], ssl.SSLContext)
    assert captured["context"].verify_mode == ssl.CERT_NONE


def test_fetch_states_secure_tls_default(configured_env, monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "ha")
    monkeypatch.setenv("HA_URL", "https://ha.local:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    import meshmon.env

    meshmon.env._config = None

    captured = {}

    def fake_urlopen(request, timeout=None, context=None):
        captured["context"] = context
        return FakeResponse(b"[]")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    cfg = get_config()
    fetch_states(cfg)
    assert captured["context"] is None  # default certificate verification
