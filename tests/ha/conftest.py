"""Fixtures for Home Assistant data source tests.

Builds realistic ``/api/states`` payloads as produced by the meshcore-ha
integration, so the mapping logic can be exercised without a live HA.
"""

import pytest

REPEATER_PUBKEY = "a1b2c3d4e5"
REPEATER_SLUG = "my_repeater"
# The configured companion prefix can be longer than the segment meshcore-ha
# puts in the local-node entity_id (it uses pubkey[:6] for the local node).
COMPANION_PUBKEY = "f7f57b8e54"
COMPANION_SEGMENT = "f7f57b"
COMPANION_SLUG = "home_node"


def make_entity(entity_id: str, state, **attributes) -> dict:
    """Build a single HA state object."""
    return {
        "entity_id": entity_id,
        "state": str(state),
        "attributes": attributes,
        "last_changed": "2026-06-04T12:00:00+00:00",
        "last_updated": "2026-06-04T12:00:00+00:00",
        "context": {"id": "01ABCDEF"},
    }


def repeater_entity(key: str, state, **attrs) -> dict:
    return make_entity(
        f"sensor.meshcore_{REPEATER_PUBKEY}_{key}_{REPEATER_SLUG}", state, **attrs
    )


def companion_entity(key: str, state, **attrs) -> dict:
    return make_entity(
        f"sensor.meshcore_{COMPANION_SEGMENT}_{key}_{COMPANION_SLUG}", state, **attrs
    )


@pytest.fixture
def repeater_entities() -> list[dict]:
    """meshcore-ha repeater sensors (with raw_* attributes for lossless values)."""
    return [
        repeater_entity("bat", "4.047", unit_of_measurement="V", raw_millivolts=4047),
        repeater_entity("battery_percentage", "78", unit_of_measurement="%"),
        repeater_entity("uptime", "24033.3", unit_of_measurement="min", raw_seconds=1441998),
        repeater_entity("airtime", "1074.4", unit_of_measurement="min", raw_seconds=64461),
        repeater_entity("rx_airtime", "2443.8", unit_of_measurement="min", raw_seconds=146626),
        repeater_entity("last_rssi", "-63"),
        repeater_entity("last_snr", "12.5"),
        repeater_entity("noise_floor", "-118"),
        repeater_entity("tx_queue_len", "0"),
        repeater_entity("nb_recv", "221311"),
        repeater_entity("nb_sent", "93993"),
        repeater_entity("sent_flood", "92207"),
        repeater_entity("sent_direct", "1786"),
        repeater_entity("recv_flood", "216960"),
        repeater_entity("recv_direct", "4328"),
        repeater_entity("flood_dups", "59799"),
        repeater_entity("direct_dups", "8"),
        repeater_entity("recv_errors", "3"),
        # Environmental telemetry (charted via telemetry.* auto-discovery).
        repeater_entity("ch1_temperature", "21.5", unit_of_measurement="°C"),
        repeater_entity("ch1_voltage", "4.0", unit_of_measurement="V"),
        # Derived sensors that share a base name; must NOT clobber the base metric.
        repeater_entity("nb_recv_rate", "12.0", unit_of_measurement="msg/min"),
        repeater_entity("airtime_utilization", "4.5", unit_of_measurement="%"),
    ]


@pytest.fixture
def companion_entities() -> list[dict]:
    """meshcore-ha local/companion sensors (no raw_* attrs -> fallback transforms)."""
    return [
        companion_entity("battery_voltage", "3.895", unit_of_measurement="V"),
        companion_entity("battery_percentage", "85", unit_of_measurement="%"),
        companion_entity("uptime", "2.1", unit_of_measurement="min"),
        companion_entity("tx_queue_len", "0"),
        companion_entity("noise_floor", "-113"),
        companion_entity("last_rssi", "-123"),
        companion_entity("last_snr", "-8.5"),
        companion_entity("tx_airtime", "5.0", unit_of_measurement="min"),
        companion_entity("rx_airtime", "10.0", unit_of_measurement="min"),
        companion_entity("nb_recv", "20"),
        companion_entity("nb_sent", "0"),
        companion_entity("sent_flood", "0"),
        companion_entity("sent_direct", "0"),
        companion_entity("recv_flood", "20"),
        companion_entity("recv_direct", "0"),
        companion_entity("node_count", "6"),
        companion_entity("tx_power", "22", unit_of_measurement="dBm"),
        companion_entity("frequency", "869.525", unit_of_measurement="MHz"),
        # Special local-node sensor with NO node-name slug; must not break
        # slug detection for the rest of the device's entities.
        make_entity(f"sensor.meshcore_{COMPANION_SEGMENT}_companion_prefix", "f7f57b"),
    ]


@pytest.fixture
def noise_entities() -> list[dict]:
    """Unrelated entities and a second MeshCore device that must be ignored."""
    return [
        make_entity("sensor.living_room_temperature", "21.5", unit_of_measurement="C"),
        make_entity("light.kitchen", "on"),
        # binary_sensor.* must not match the sensor.* regex.
        make_entity(
            f"binary_sensor.meshcore_{REPEATER_PUBKEY}_node_status_{REPEATER_SLUG}", "on"
        ),
        # A different MeshCore device (different pubkey) must not be selected.
        make_entity("sensor.meshcore_99887766aa_bat_other_repeater", "3.9", raw_millivolts=3900),
        make_entity("sensor.meshcore_99887766aa_uptime_other_repeater", "100.0", raw_seconds=6000),
    ]


@pytest.fixture
def all_states(repeater_entities, companion_entities, noise_entities) -> list[dict]:
    return [*repeater_entities, *companion_entities, *noise_entities]


@pytest.fixture
def ha_env(configured_env, monkeypatch):
    """Configure the environment for the HA data source against a temp DB."""
    monkeypatch.setenv("DATA_SOURCE", "ha")
    monkeypatch.setenv("HA_URL", "http://ha.local:8123")
    monkeypatch.setenv("HA_TOKEN", "test-token")
    monkeypatch.setenv("HA_REPEATER_PUBKEY", REPEATER_PUBKEY)
    monkeypatch.setenv("HA_COMPANION_PUBKEY", COMPANION_PUBKEY)

    import meshmon.env

    meshmon.env._config = None
    return configured_env
