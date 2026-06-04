"""Home Assistant data source for MeshCore Stats.

When ``DATA_SOURCE=ha`` the collectors read metrics from a Home Assistant
instance running the `meshcore-ha <https://github.com/meshcore-dev/meshcore-ha>`_
integration instead of talking to the radio directly. HA owns the serial
device; this project just polls the entity states over the REST API.

meshcore-ha exposes one ``sensor`` entity per metric, named:

    sensor.meshcore_<pubkey[:10]>_<sensor_key>_<node_name_slug>

The sensor *keys* mirror the raw firmware field names for repeaters
(``bat``, ``uptime``, ``nb_recv`` …) and a normalized superset for the
local/companion node. This module maps those entity states back onto the
firmware field names that the database and chart renderers expect, reversing
the unit conversions meshcore-ha applies (volts→millivolts, minutes→seconds)
and preferring the lossless ``raw_millivolts`` / ``raw_seconds`` attributes
when present.

The HTTP fetch is deliberately thin (stdlib ``urllib``); the mapping logic is
pure so it can be unit-tested with fixture data and without a live HA.
"""

import json
import re
import ssl
import time
import urllib.request
from collections import defaultdict
from typing import Any

from . import log
from .db import insert_metrics
from .env import Config, get_config

# entity_id pattern: sensor.meshcore_<pubkey>_<rest>. The pubkey segment is the
# device public key truncated to 10 hex chars by meshcore-ha.
_ENTITY_RE = re.compile(r"^sensor\.meshcore_([0-9a-f]+)_(.+)$")

# State strings that mean "no usable value".
_EMPTY_STATES = frozenset({"", "unknown", "unavailable", "none"})

# Value transforms, keyed by "kind":
#   direct          -> store the numeric state as-is
#   mv              -> meshcore-ha state is volts; DB wants millivolts
#   secs            -> meshcore-ha state is minutes; DB wants seconds
#   count_minus_one -> node_count includes self; DB contact count excludes it

# meshcore-ha repeater sensor key -> (db_metric_name, kind). Keys match the raw
# firmware field names from req_status_sync().
REPEATER_KEY_MAP: dict[str, tuple[str, str]] = {
    "bat": ("bat", "mv"),
    "uptime": ("uptime", "secs"),
    "airtime": ("airtime", "secs"),
    "rx_airtime": ("rx_airtime", "secs"),
    "last_rssi": ("last_rssi", "direct"),
    "last_snr": ("last_snr", "direct"),
    "noise_floor": ("noise_floor", "direct"),
    "tx_queue_len": ("tx_queue_len", "direct"),
    "nb_recv": ("nb_recv", "direct"),
    "nb_sent": ("nb_sent", "direct"),
    "sent_flood": ("sent_flood", "direct"),
    "sent_direct": ("sent_direct", "direct"),
    "recv_flood": ("recv_flood", "direct"),
    "recv_direct": ("recv_direct", "direct"),
    "flood_dups": ("flood_dups", "direct"),
    "direct_dups": ("direct_dups", "direct"),
    "recv_errors": ("recv_errors", "direct"),
}

# meshcore-ha local/companion sensor key -> (db_metric_name, kind). meshcore-ha
# normalizes the companion's firmware fields onto the repeater naming scheme, so
# we translate them back to the companion firmware names the DB stores.
COMPANION_KEY_MAP: dict[str, tuple[str, str]] = {
    "battery_voltage": ("battery_mv", "mv"),
    "uptime": ("uptime_secs", "secs"),
    "tx_queue_len": ("queue_len", "direct"),
    "noise_floor": ("noise_floor", "direct"),
    "last_rssi": ("last_rssi", "direct"),
    "last_snr": ("last_snr", "direct"),
    "tx_airtime": ("tx_air_secs", "secs"),
    "rx_airtime": ("rx_air_secs", "secs"),
    "nb_recv": ("recv", "direct"),
    "nb_sent": ("sent", "direct"),
    "sent_flood": ("flood_tx", "direct"),
    "sent_direct": ("direct_tx", "direct"),
    "recv_flood": ("flood_rx", "direct"),
    "recv_direct": ("direct_rx", "direct"),
    "node_count": ("contacts", "count_minus_one"),
}


def _state_value(entity: dict[str, Any]) -> float | None:
    """Parse an entity's ``state`` into a float, or None if not numeric."""
    raw = entity.get("state")
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().lower() in _EMPTY_STATES:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _common_token_suffix(remainders: list[str]) -> str:
    """Most frequent ``_``-delimited trailing token sequence (the node slug).

    Every entity for a device ends with the same node-name slug, while the
    metric keys differ — so the shared trailing tokens are the slug, which we
    strip to recover each sensor key. A few special sensors omit the slug
    entirely (e.g. ``companion_prefix``), so we extend the suffix while a strict
    majority of entities agree, rather than requiring *all* of them to match.
    """
    if not remainders:
        return ""
    total = len(remainders)
    token_lists = [r.split("_") for r in remainders]
    suffix: tuple[str, ...] = ()
    depth = 1
    while True:
        counts: dict[tuple[str, ...], int] = {}
        for tokens in token_lists:
            if len(tokens) >= depth:
                candidate = tuple(tokens[-depth:])
                counts[candidate] = counts.get(candidate, 0) + 1
        if not counts:
            break
        best, best_count = max(counts.items(), key=lambda item: item[1])
        if best_count * 2 <= total:  # no strict majority at this depth
            break
        suffix = best
        depth += 1
    return "_".join(suffix)


def _pubkey_matches(segment: str, prefix: str) -> bool:
    """True if the entity pubkey segment and configured prefix share a prefix.

    The configured prefix may be shorter or longer than the 10-hex segment in
    the entity_id, so compare over their common length.
    """
    n = min(len(segment), len(prefix))
    return n > 0 and segment[:n] == prefix[:n]


def index_entities_by_key(
    states: list[dict[str, Any]], pubkey_prefix: str | None
) -> dict[str, dict[str, Any]]:
    """Index a single device's MeshCore sensors by sensor key.

    Args:
        states: Parsed ``/api/states`` response (list of entity dicts).
        pubkey_prefix: Public-key prefix selecting the target device.

    Returns:
        Mapping of sensor key (slug stripped) to the entity dict.
    """
    if not pubkey_prefix:
        return {}
    prefix = pubkey_prefix.strip().lower()

    groups: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for entity in states:
        entity_id = entity.get("entity_id", "")
        match = _ENTITY_RE.match(entity_id)
        if not match:
            continue
        segment, remainder = match.group(1), match.group(2)
        if _pubkey_matches(segment, prefix):
            groups[segment].append((remainder, entity))

    if not groups:
        return {}
    if len(groups) > 1:
        log.warn(
            f"Pubkey prefix '{prefix}' matched {len(groups)} devices; "
            "using the one with the most sensors. Use a longer prefix to disambiguate."
        )
    # Pick the device with the most matching sensors (most likely the real one).
    segment = max(groups, key=lambda s: len(groups[s]))
    group = groups[segment]

    slug = _common_token_suffix([remainder for remainder, _ in group])
    result: dict[str, dict[str, Any]] = {}
    for remainder, entity in group:
        if slug and remainder.endswith(f"_{slug}"):
            key = remainder[: -(len(slug) + 1)]
        elif slug and remainder == slug:
            key = ""
        else:
            key = remainder
        if key:
            result[key] = entity
    return result


def _map_metrics(
    entities: dict[str, dict[str, Any]], key_map: dict[str, tuple[str, str]]
) -> dict[str, float]:
    """Apply a key map to indexed entities, producing firmware-named metrics."""
    metrics: dict[str, float] = {}
    for ha_key, (db_metric, kind) in key_map.items():
        entity = entities.get(ha_key)
        if entity is None:
            continue
        attrs = entity.get("attributes") or {}

        if kind == "mv":
            raw = attrs.get("raw_millivolts")
            if isinstance(raw, (int, float)):
                metrics[db_metric] = float(raw)
                continue
            value = _state_value(entity)
            if value is not None:
                metrics[db_metric] = value * 1000.0
        elif kind == "secs":
            raw = attrs.get("raw_seconds")
            if isinstance(raw, (int, float)):
                metrics[db_metric] = float(raw)
                continue
            value = _state_value(entity)
            if value is not None:
                # HA may auto-convert the display unit (min → d via
                # suggested_unit_of_measurement). Convert back to seconds
                # using whatever unit the entity is actually reporting.
                unit = attrs.get("unit_of_measurement", "min")
                if unit == "d":
                    metrics[db_metric] = value * 86400.0
                elif unit in ("h", "hr"):
                    metrics[db_metric] = value * 3600.0
                elif unit in ("s", "sec"):
                    metrics[db_metric] = value
                else:  # default: minutes
                    metrics[db_metric] = value * 60.0
        elif kind == "count_minus_one":
            value = _state_value(entity)
            if value is not None:
                metrics[db_metric] = max(value - 1.0, 0.0)
        else:  # direct
            value = _state_value(entity)
            if value is not None:
                metrics[db_metric] = value
    return metrics


# meshcore-ha exposes environmental telemetry as per-channel sensors keyed
# ch<channel>_<lpp_type> (e.g. ch1_temperature). The DB/chart layer stores these
# as telemetry.<type>.<channel> and auto-charts them (telemetry.voltage.* and
# telemetry.gps.* are collected but intentionally not charted).
_TELEMETRY_RE = re.compile(r"^ch(\d+)_(.+)$")


def _map_telemetry(entities: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Map ch<N>_<type> telemetry sensors to telemetry.<type>.<N> metrics."""
    metrics: dict[str, float] = {}
    for key, entity in entities.items():
        match = _TELEMETRY_RE.match(key)
        if not match:
            continue
        channel, sensor_type = match.group(1), match.group(2)
        value = _state_value(entity)
        if value is not None:
            metrics[f"telemetry.{sensor_type}.{channel}"] = value
    return metrics


def map_repeater_metrics(entities: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Map indexed repeater entities to repeater firmware field names."""
    metrics = _map_metrics(entities, REPEATER_KEY_MAP)
    metrics.update(_map_telemetry(entities))
    return metrics


def map_companion_metrics(entities: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Map indexed companion entities to companion firmware field names."""
    metrics = _map_metrics(entities, COMPANION_KEY_MAP)
    metrics.update(_map_telemetry(entities))
    return metrics


def fetch_states(cfg: Config) -> list[dict[str, Any]]:
    """Fetch all entity states from the Home Assistant REST API.

    Raises:
        RuntimeError: If HA_URL/HA_TOKEN are missing or the response is invalid.
    """
    if not cfg.ha_url or not cfg.ha_token:
        raise RuntimeError("HA_URL and HA_TOKEN are required for DATA_SOURCE=ha")

    url = cfg.ha_url.rstrip("/") + "/api/states"
    request = urllib.request.Request(  # noqa: S310 - user-configured HA URL
        url,
        headers={
            "Authorization": f"Bearer {cfg.ha_token}",
            "Content-Type": "application/json",
        },
    )

    context = None
    if url.lower().startswith("https://") and not cfg.ha_verify_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    with urllib.request.urlopen(  # noqa: S310 - user-configured HA URL
        request, timeout=cfg.ha_timeout_s, context=context
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not isinstance(payload, list):
        raise RuntimeError("Unexpected /api/states response (expected a JSON list)")
    return payload


# role -> (config attribute holding the pubkey, mapping function, display label)
_ROLE_CONFIG: dict[str, tuple[str, Any, str]] = {
    "companion": ("ha_companion_pubkey", map_companion_metrics, "Companion"),
    "repeater": ("ha_repeater_pubkey", map_repeater_metrics, "Repeater"),
}


def _format_summary(role: str, label: str, metrics: dict[str, float]) -> str:
    """Build a concise one-line collection summary."""
    parts: list[str] = []
    bat_field = "battery_mv" if role == "companion" else "bat"
    if bat_field in metrics:
        parts.append(f"bat={metrics[bat_field] / 1000.0:.2f}V")
    uptime_field = "uptime_secs" if role == "companion" else "uptime"
    if uptime_field in metrics:
        parts.append(f"uptime={int(metrics[uptime_field] // 86400)}d")
    rx_field = "recv" if role == "companion" else "nb_recv"
    if rx_field in metrics:
        parts.append(f"rx={int(metrics[rx_field])}")
    tx_field = "sent" if role == "companion" else "nb_sent"
    if tx_field in metrics:
        parts.append(f"tx={int(metrics[tx_field])}")
    parts.append(f"metrics={len(metrics)}")
    return f"{label} (HA): {', '.join(parts)}"


def run_ha_collection(
    role: str, states: list[dict[str, Any]] | None = None
) -> int:
    """Collect one role's metrics from Home Assistant and store them.

    Args:
        role: "companion" or "repeater".
        states: Optional pre-fetched states (mainly for testing).

    Returns:
        Exit code (0 = success, 1 = error).
    """
    cfg = get_config()
    pubkey_attr, map_fn, label = _ROLE_CONFIG[role]
    pubkey = getattr(cfg, pubkey_attr)
    if not pubkey:
        log.error(
            f"No public key configured for {role}; set HA_{role.upper()}_PUBKEY"
        )
        return 1

    ts = int(time.time())

    if states is None:
        try:
            states = fetch_states(cfg)
        except Exception as exc:
            log.error(f"Failed to fetch states from Home Assistant: {exc}")
            return 1

    entities = index_entities_by_key(states, pubkey)
    if not entities:
        log.error(f"No MeshCore entities found for {role} pubkey '{pubkey}'")
        return 1

    metrics = map_fn(entities)
    if not metrics:
        log.error(
            f"No mappable metrics for {role} "
            f"(matched {len(entities)} entities, none recognized)"
        )
        return 1

    try:
        inserted = insert_metrics(ts=ts, role=role, metrics=metrics)
    except Exception as exc:
        log.error(f"Failed to store {role} metrics: {exc}")
        return 1

    log.info(_format_summary(role, label, metrics))
    log.debug(f"Stored {inserted} {role} metrics from HA (ts={ts})")
    return 0
