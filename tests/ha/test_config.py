"""Tests for the HA-related configuration fields."""

from meshmon.env import get_config


def _reset():
    import meshmon.env

    meshmon.env._config = None


def test_default_data_source_is_device():
    assert get_config().data_source == "device"


def test_data_source_normalized(monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "  HA  ")
    _reset()
    assert get_config().data_source == "ha"


def test_ha_defaults():
    cfg = get_config()
    assert cfg.ha_url is None
    assert cfg.ha_token is None
    assert cfg.ha_timeout_s == 10
    assert cfg.ha_verify_tls is True


def test_ha_repeater_pubkey_explicit(monkeypatch):
    monkeypatch.setenv("HA_REPEATER_PUBKEY", "abc123")
    _reset()
    assert get_config().ha_repeater_pubkey == "abc123"


def test_ha_repeater_pubkey_falls_back_to_pubkey_prefix(monkeypatch):
    monkeypatch.setenv("REPEATER_PUBKEY_PREFIX", "deadbe")
    _reset()
    assert get_config().ha_repeater_pubkey == "deadbe"


def test_ha_repeater_pubkey_falls_back_to_key_prefix(monkeypatch):
    monkeypatch.setenv("REPEATER_KEY_PREFIX", "feed01")
    _reset()
    assert get_config().ha_repeater_pubkey == "feed01"


def test_ha_companion_pubkey_falls_back_to_pubkey_prefix(monkeypatch):
    monkeypatch.setenv("COMPANION_PUBKEY_PREFIX", "cafe22")
    _reset()
    assert get_config().ha_companion_pubkey == "cafe22"


def test_ha_verify_tls_can_be_disabled(monkeypatch):
    monkeypatch.setenv("HA_VERIFY_TLS", "0")
    _reset()
    assert get_config().ha_verify_tls is False
