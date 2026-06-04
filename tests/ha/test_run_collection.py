"""Tests for run_ha_collection and the collector-script HA routing."""

import pytest

from meshmon import ha_source
from meshmon.db import get_latest_metrics, init_db
from meshmon.ha_source import run_ha_collection
from tests.scripts.conftest import load_script_module


def test_run_repeater_success(ha_env, all_states):
    init_db()
    assert run_ha_collection("repeater", states=all_states) == 0
    latest = get_latest_metrics("repeater")
    assert latest["bat"] == 4047.0
    assert latest["nb_recv"] == 221311.0
    assert latest["uptime"] == 1441998.0


def test_run_companion_success(ha_env, all_states):
    init_db()
    assert run_ha_collection("companion", states=all_states) == 0
    latest = get_latest_metrics("companion")
    assert latest["battery_mv"] == 3895.0
    assert latest["recv"] == 20.0
    assert latest["contacts"] == 5.0


def test_run_no_pubkey_configured(configured_env, monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "ha")
    monkeypatch.setenv("HA_URL", "http://ha.local:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    import meshmon.env

    meshmon.env._config = None
    init_db()
    assert run_ha_collection("repeater", states=[]) == 1


def test_run_no_entities_found(ha_env):
    init_db()
    assert run_ha_collection("repeater", states=[]) == 1


def test_run_no_mappable_metrics(ha_env):
    init_db()
    states = [
        {
            "entity_id": "sensor.meshcore_a1b2c3d4e5_battery_percentage_my_repeater",
            "state": "80",
            "attributes": {},
        },
        {
            "entity_id": "sensor.meshcore_a1b2c3d4e5_frequency_my_repeater",
            "state": "869.5",
            "attributes": {},
        },
    ]
    assert run_ha_collection("repeater", states=states) == 1


def test_run_fetch_error_returns_one(ha_env, monkeypatch):
    init_db()

    def boom(cfg):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ha_source, "fetch_states", boom)
    assert run_ha_collection("repeater") == 1


def test_run_insert_error_returns_one(ha_env, all_states, monkeypatch):
    init_db()

    def boom(**kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ha_source, "insert_metrics", boom)
    assert run_ha_collection("repeater", states=all_states) == 1


def test_run_fetches_when_states_none(ha_env, all_states, monkeypatch):
    init_db()
    monkeypatch.setattr(ha_source, "fetch_states", lambda cfg: all_states)
    assert run_ha_collection("companion") == 0
    assert get_latest_metrics("companion")["recv"] == 20.0


def test_companion_collector_main_routes_to_ha(ha_env, all_states, monkeypatch):
    monkeypatch.setattr(ha_source, "fetch_states", lambda cfg: all_states)
    module = load_script_module("collect_companion.py")
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 0
    assert get_latest_metrics("companion")["battery_mv"] == 3895.0


def test_repeater_collector_main_routes_to_ha(ha_env, all_states, monkeypatch):
    monkeypatch.setattr(ha_source, "fetch_states", lambda cfg: all_states)
    module = load_script_module("collect_repeater.py")
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 0
    assert get_latest_metrics("repeater")["bat"] == 4047.0
