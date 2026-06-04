"""Tests for the pure HA entity -> firmware-metric mapping logic."""

from meshmon.ha_source import (
    _common_token_suffix,
    _state_value,
    index_entities_by_key,
    map_companion_metrics,
    map_repeater_metrics,
)
from tests.ha.conftest import (
    COMPANION_PUBKEY,
    REPEATER_PUBKEY,
    companion_entity,
    make_entity,
    repeater_entity,
)


class TestCommonTokenSuffix:
    def test_basic_shared_slug(self):
        remainders = ["bat_my_repeater", "uptime_my_repeater", "nb_recv_my_repeater"]
        assert _common_token_suffix(remainders) == "my_repeater"

    def test_empty_list(self):
        assert _common_token_suffix([]) == ""

    def test_no_common_suffix(self):
        assert _common_token_suffix(["bat_alpha", "uptime_beta"]) == ""

    def test_single_token_slug(self):
        assert _common_token_suffix(["bat_node", "uptime_node"]) == "node"

    def test_full_match_single_element(self):
        # A lone remainder is entirely "common" with itself.
        assert _common_token_suffix(["bat_node"]) == "bat_node"


class TestStateValue:
    def test_numeric_string(self):
        assert _state_value({"state": "12.5"}) == 12.5

    def test_integer_string(self):
        assert _state_value({"state": "221311"}) == 221311.0

    def test_negative(self):
        assert _state_value({"state": "-63"}) == -63.0

    def test_unknown(self):
        assert _state_value({"state": "unknown"}) is None

    def test_unavailable(self):
        assert _state_value({"state": "unavailable"}) is None

    def test_empty(self):
        assert _state_value({"state": ""}) is None

    def test_missing(self):
        assert _state_value({}) is None

    def test_non_numeric(self):
        assert _state_value({"state": "open"}) is None


class TestIndexEntitiesByKey:
    def test_filters_and_strips_slug(self, all_states):
        idx = index_entities_by_key(all_states, REPEATER_PUBKEY)
        # Repeater keys present, slug stripped.
        assert "bat" in idx
        assert "nb_recv" in idx
        assert "recv_errors" in idx
        # The derived sensor remains a distinct key (does not overwrite nb_recv).
        assert "nb_recv_rate" in idx
        assert idx["nb_recv"]["entity_id"].endswith("_nb_recv_my_repeater")
        assert idx["nb_recv_rate"]["entity_id"].endswith("_nb_recv_rate_my_repeater")
        # Companion-only keys are absent from the repeater index.
        assert "node_count" not in idx
        # No key still carries the slug.
        assert not any(k.endswith("my_repeater") for k in idx)

    def test_other_device_not_selected(self, all_states):
        idx = index_entities_by_key(all_states, REPEATER_PUBKEY)
        for entity in idx.values():
            assert "99887766aa" not in entity["entity_id"]

    def test_empty_prefix(self, all_states):
        assert index_entities_by_key(all_states, None) == {}
        assert index_entities_by_key(all_states, "") == {}

    def test_no_match(self, all_states):
        assert index_entities_by_key(all_states, "deadbeef") == {}

    def test_short_prefix_matches(self, all_states):
        # A 6-char prefix still selects the 10-char device segment.
        idx = index_entities_by_key(all_states, "a1b2c3")
        assert "bat" in idx

    def test_ambiguous_prefix_picks_largest(self):
        states = [
            make_entity("sensor.meshcore_a1aaaaaaaa_bat_one", "4.0", raw_millivolts=4000),
            make_entity("sensor.meshcore_a1aaaaaaaa_uptime_one", "1.0", raw_seconds=60),
            make_entity("sensor.meshcore_a1bbbbbbbb_bat_two", "3.5", raw_millivolts=3500),
        ]
        # Prefix "a1" matches both; the device with more sensors wins.
        idx = index_entities_by_key(states, "a1")
        assert "bat" in idx and "uptime" in idx
        assert idx["bat"]["entity_id"].endswith("_one")

    def test_no_common_suffix_keeps_remainder(self):
        states = [
            make_entity("sensor.meshcore_aaaaaaaaaa_bat_x", "4.0", raw_millivolts=4000),
            make_entity("sensor.meshcore_aaaaaaaaaa_uptime_y", "1.0", raw_seconds=60),
        ]
        idx = index_entities_by_key(states, "aaaaaaaaaa")
        # No shared slug -> keys retain their full remainder.
        assert set(idx) == {"bat_x", "uptime_y"}

    def test_companion_outlier_does_not_break_slug(self, companion_entities):
        # The slug-less `companion_prefix` entity must not defeat slug detection
        # for the rest of the device (this was the real-world companion bug).
        idx = index_entities_by_key(companion_entities, COMPANION_PUBKEY)
        assert "battery_voltage" in idx
        assert "node_count" in idx
        # The slug-less special sensor is kept under its own (unstripped) key.
        assert "companion_prefix" in idx
        assert not any(k.endswith("home_node") for k in idx)

    def test_entity_equal_to_slug_is_skipped(self):
        states = [
            make_entity("sensor.meshcore_bbbbbbbbbb_bat_node", "4.0", raw_millivolts=4000),
            make_entity("sensor.meshcore_bbbbbbbbbb_uptime_node", "1.0", raw_seconds=60),
            # remainder equals the derived slug -> empty key -> dropped.
            make_entity("sensor.meshcore_bbbbbbbbbb_node", "ignored"),
        ]
        idx = index_entities_by_key(states, "bbbbbbbbbb")
        assert set(idx) == {"bat", "uptime"}


class TestMapRepeater:
    def test_maps_all_status_fields(self, repeater_entities):
        idx = index_entities_by_key(repeater_entities, REPEATER_PUBKEY)
        metrics = map_repeater_metrics(idx)

        # Lossless via raw_* attributes.
        assert metrics["bat"] == 4047.0
        assert metrics["uptime"] == 1441998.0
        assert metrics["airtime"] == 64461.0
        assert metrics["rx_airtime"] == 146626.0
        # Direct counters / radio values.
        assert metrics["last_rssi"] == -63.0
        assert metrics["last_snr"] == 12.5
        assert metrics["noise_floor"] == -118.0
        assert metrics["nb_recv"] == 221311.0
        assert metrics["nb_sent"] == 93993.0
        assert metrics["sent_flood"] == 92207.0
        assert metrics["recv_direct"] == 4328.0
        assert metrics["flood_dups"] == 59799.0
        assert metrics["recv_errors"] == 3.0

    def test_maps_telemetry_channels(self, repeater_entities):
        idx = index_entities_by_key(repeater_entities, REPEATER_PUBKEY)
        metrics = map_repeater_metrics(idx)
        assert metrics["telemetry.temperature.1"] == 21.5
        assert metrics["telemetry.voltage.1"] == 4.0

    def test_derived_sensors_not_mapped(self, repeater_entities):
        idx = index_entities_by_key(repeater_entities, REPEATER_PUBKEY)
        metrics = map_repeater_metrics(idx)
        # battery_percentage and *_rate / *_utilization are not firmware metrics.
        assert "battery_percentage" not in metrics
        # nb_recv reflects the counter, not the rate sensor's 12.0.
        assert metrics["nb_recv"] == 221311.0

    def test_fallback_without_raw_attrs(self):
        entities = [
            repeater_entity("bat", "4.0"),
            repeater_entity("uptime", "10.0"),
            repeater_entity("airtime", "2.5"),
        ]
        idx = index_entities_by_key(entities, REPEATER_PUBKEY)
        metrics = map_repeater_metrics(idx)
        assert metrics["bat"] == 4000.0  # volts -> millivolts
        assert metrics["uptime"] == 600.0  # minutes -> seconds
        assert metrics["airtime"] == 150.0

    def test_skips_unknown_state(self):
        entities = [
            repeater_entity("bat", "unavailable"),
            repeater_entity("nb_recv", "100"),
        ]
        idx = index_entities_by_key(entities, REPEATER_PUBKEY)
        metrics = map_repeater_metrics(idx)
        assert "bat" not in metrics
        assert metrics["nb_recv"] == 100.0

    def test_skips_unknown_secs_and_direct(self):
        entities = [
            repeater_entity("uptime", "unknown"),  # secs transform, no value
            repeater_entity("noise_floor", "unavailable"),  # direct, no value
            repeater_entity("nb_recv", "5"),
        ]
        idx = index_entities_by_key(entities, REPEATER_PUBKEY)
        metrics = map_repeater_metrics(idx)
        assert "uptime" not in metrics
        assert "noise_floor" not in metrics
        assert metrics["nb_recv"] == 5.0


class TestMapCompanion:
    def test_translates_to_companion_firmware_names(self, companion_entities):
        idx = index_entities_by_key(companion_entities, COMPANION_PUBKEY)
        metrics = map_companion_metrics(idx)

        assert metrics["battery_mv"] == 3895.0
        assert metrics["uptime_secs"] == 126.0  # 2.1 min -> 126 s
        assert metrics["queue_len"] == 0.0
        assert metrics["noise_floor"] == -113.0
        assert metrics["last_snr"] == -8.5
        assert metrics["tx_air_secs"] == 300.0  # 5 min
        assert metrics["rx_air_secs"] == 600.0  # 10 min
        assert metrics["recv"] == 20.0
        assert metrics["sent"] == 0.0
        assert metrics["flood_tx"] == 0.0
        assert metrics["flood_rx"] == 20.0
        assert metrics["direct_rx"] == 0.0
        # node_count includes self; contact count excludes it.
        assert metrics["contacts"] == 5.0

    def test_skips_unknown_node_count(self):
        entities = [
            companion_entity("node_count", "unknown"),  # count_minus_one, no value
            companion_entity("nb_recv", "3"),
        ]
        idx = index_entities_by_key(entities, COMPANION_PUBKEY)
        metrics = map_companion_metrics(idx)
        assert "contacts" not in metrics
        assert metrics["recv"] == 3.0

    def test_empty_entities(self):
        assert map_companion_metrics({}) == {}
        assert map_repeater_metrics({}) == {}
