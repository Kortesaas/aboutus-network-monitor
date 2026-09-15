"""Configuration loading, normalisation and saving.

The YAML file in ``config/network.yaml`` is the persistent backend
configuration. :func:`load_config` returns the raw mapping; :func:`normalized`
returns a fully expanded view (port ranges expanded, profiles resolved,
defaults applied) that the rest of the backend works with.
"""

from __future__ import annotations

import copy
import os
import re
import threading
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "network.yaml"
CONFIG_ENV_VAR = "ABOUTUS_MONITOR_CONFIG"

_LOCK = threading.Lock()


class ConfigError(RuntimeError):
    """Raised when the monitor configuration cannot be loaded."""


DEFAULT_MONITORING = {
    "fast_poll_interval_seconds": 12,
    "full_scan_interval_seconds": 300,
    "full_scan_min_interval_seconds": 60,
    "refresh_cooldown_seconds": 5,
    "static_tables_every_cycles": 5,
    "stale_after_seconds": 45,
    "relocating_seconds": 40,
    "offline_after_seconds": 240,
    "targeted_refresh_cooldown_seconds": 15,
    "port_error_threshold": 1,
}

DEFAULT_POLICY = {
    "trunk_vlans": [10, 20, 30, 40, 99],
    "forbidden_vlans": [50],
    "forbidden_trunk_vlans": [50, 90],
    "deprecated_names": ["LASER", "LIGHTING"],
    "ap_trunk": {"native_vlan": 99, "tagged_vlans": [10, 20, 30, 40]},
    "rstp_root_switch": "dell-foh",
    "dante_switches": [],
    "dante_vlan": 20,
}

DEFAULT_PORT_PROFILES = {
    "CONTROL": {"type": "access", "vlan": 10, "color": "control", "label": "CONTROL"},
    "AUDIO": {"type": "access", "vlan": 20, "color": "audio", "label": "AUDIO"},
    "LIGHT": {"type": "access", "vlan": 30, "color": "light", "label": "LIGHT"},
    "VIDEO": {"type": "access", "vlan": 40, "color": "video", "label": "VIDEO"},
    "MGMT": {"type": "access", "vlan": 99, "color": "mgmt", "label": "MGMT"},
    "TRUNK": {"type": "trunk", "tagged": [10, 20, 30, 40, 99], "native": None, "color": "trunk", "label": "TRUNK"},
    "AP_TRUNK": {"type": "ap_trunk", "tagged": [10, 20, 30, 40], "native": 99, "color": "ap", "label": "AP"},
    "WAN_PASS": {"type": "wan_pass", "vlan": 90, "color": "wan", "label": "WAN"},
    "UNUSED": {"type": "unused", "color": "unused", "label": ""},
}

DEFAULT_COLORS = {
    "control": {"color": "#2f80ff", "dark": "#1a4fb0", "text": "#ffffff"},
    "audio": {"color": "#22c55e", "dark": "#15803d", "text": "#062b14"},
    "light": {"color": "#ef4444", "dark": "#991b1b", "text": "#ffffff"},
    "video": {"color": "#a855f7", "dark": "#6b21a8", "text": "#ffffff"},
    "mgmt": {"color": "#e5e7eb", "dark": "#9ca3af", "text": "#111827"},
    "trunk": {"color": "#111111", "dark": "#000000", "text": "#facc15", "accent": "#facc15"},
    "ap": {"color": "#0ea5e9", "dark": "#0369a1", "text": "#03202e", "accent": "#facc15"},
    "wan": {"color": "#f97316", "dark": "#9a3412", "text": "#111111", "accent": "#111111"},
    "unused": {"color": "#2a3140", "dark": "#1b202b", "text": "#8b95a7"},
}


def get_config_path() -> Path:
    configured_path = os.getenv(CONFIG_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser()
    return DEFAULT_CONFIG_PATH


def load_config() -> dict[str, Any]:
    config_path = get_config_path()
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")
    with _LOCK:
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ConfigError("Configuration root must be a mapping.")
    return loaded


def save_config(config: dict[str, Any]) -> Path:
    if not isinstance(config, dict):
        raise ConfigError("Configuration root must be a mapping.")
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    with _LOCK:
        with tmp_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False, allow_unicode=True, width=120)
        os.replace(tmp_path, config_path)
    return config_path


# --------------------------------------------------------------------------- helpers


def as_int(value: Any, fallback: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return fallback


def as_int_list(values: Any) -> list[int]:
    if values is None:
        return []
    if isinstance(values, (str, int)):
        values = [values]
    result: list[int] = []
    for value in values:
        number = as_int(value)
        if number is not None and number not in result:
            result.append(number)
    return result


def expand_port_keys(key: Any) -> list[int]:
    """Expand ``"1-8"``, ``"21"``, ``"1,3,5-7"`` or ``7`` into port numbers."""
    text = str(key).strip()
    ports: list[int] = []
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                start, end = end, start
            ports.extend(range(start, end + 1))
            continue
        number = as_int(part)
        if number is not None:
            ports.append(number)
    return ports


def vlan_id_text(value: Any) -> str:
    number = as_int(value)
    return str(number) if number is not None else str(value or "").strip()


def snmp_version(value: Any, fallback: str = "2c") -> str:
    text = str(value or fallback).strip().lower()
    if text.startswith("v") and text in {"v1", "v2c"}:
        text = text[1:]
    return text or fallback


# --------------------------------------------------------------------------- normalisation


def monitoring_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(DEFAULT_MONITORING)
    configured = config.get("monitoring") or {}
    if isinstance(configured, dict):
        for key, fallback in DEFAULT_MONITORING.items():
            number = as_int(configured.get(key), None)
            if number is not None:
                settings[key] = max(0, number)
    settings["fast_poll_interval_seconds"] = max(3, settings["fast_poll_interval_seconds"])
    settings["full_scan_interval_seconds"] = max(30, settings["full_scan_interval_seconds"])
    settings["full_scan_min_interval_seconds"] = max(15, settings["full_scan_min_interval_seconds"])
    settings["static_tables_every_cycles"] = max(1, settings["static_tables_every_cycles"])
    return settings


def policy_settings(config: dict[str, Any]) -> dict[str, Any]:
    policy = copy.deepcopy(DEFAULT_POLICY)
    configured = config.get("policy") or {}
    if isinstance(configured, dict):
        for key in ("trunk_vlans", "forbidden_vlans", "forbidden_trunk_vlans"):
            if key in configured:
                policy[key] = as_int_list(configured.get(key))
        if "deprecated_names" in configured:
            policy["deprecated_names"] = [str(item).upper() for item in configured.get("deprecated_names") or []]
        ap_trunk = configured.get("ap_trunk")
        if isinstance(ap_trunk, dict):
            policy["ap_trunk"] = {
                "native_vlan": as_int(ap_trunk.get("native_vlan"), 99),
                "tagged_vlans": as_int_list(ap_trunk.get("tagged_vlans")) or [10, 20, 30, 40],
            }
        if configured.get("rstp_root_switch"):
            policy["rstp_root_switch"] = str(configured["rstp_root_switch"])
        if "dante_switches" in configured:
            policy["dante_switches"] = [str(item) for item in configured.get("dante_switches") or []]
        if "dante_vlan" in configured:
            policy["dante_vlan"] = as_int(configured.get("dante_vlan"), 20)
    return policy


def port_profiles(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    profiles = copy.deepcopy(DEFAULT_PORT_PROFILES)
    configured = config.get("port_profiles") or {}
    if isinstance(configured, dict):
        for name, profile in configured.items():
            if not isinstance(profile, dict):
                continue
            key = str(name).upper()
            merged = dict(profiles.get(key) or {})
            merged.update(profile)
            profiles[key] = merged
    normalized: dict[str, dict[str, Any]] = {}
    for name, profile in profiles.items():
        ptype = str(profile.get("type") or "access").lower()
        entry = {
            "name": name,
            "type": ptype,
            "color": str(profile.get("color") or ("trunk" if ptype == "trunk" else "unused")),
            "label": str(profile.get("label") if profile.get("label") is not None else name),
            "vlan": as_int(profile.get("vlan")),
            "tagged": as_int_list(profile.get("tagged")),
            "native": as_int(profile.get("native")),
        }
        if ptype == "access" and entry["vlan"] is not None:
            entry["native"] = entry["vlan"]
        normalized[name] = entry
    return normalized


def vlan_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in config.get("vlans") or []:
        if not isinstance(raw, dict):
            continue
        vlan_id = as_int(raw.get("id"))
        if vlan_id is None:
            continue
        subnet = raw.get("subnet")
        result.append(
            {
                "id": vlan_id,
                "name": str(raw.get("name") or f"VLAN {vlan_id}").upper(),
                "description": str(raw.get("description") or ""),
                "subnet": str(subnet) if subnet else None,
                "gateway": str(raw.get("gateway")) if raw.get("gateway") else None,
                "color": str(raw.get("color") or "unused"),
                "monitor": bool(raw.get("monitor", bool(subnet))),
                "isolated": bool(raw.get("isolated", False)),
            }
        )
    return result


def ui_colors(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    colors = copy.deepcopy(DEFAULT_COLORS)
    configured = (config.get("ui") or {}).get("colors") or {}
    if isinstance(configured, dict):
        for name, value in configured.items():
            if isinstance(value, dict):
                colors[str(name)] = {**colors.get(str(name), {}), **{k: str(v) for k, v in value.items()}}
    return colors


def _normalize_device(raw: dict[str, Any], kind: str, fallback_id: str) -> dict[str, Any]:
    ip_address = raw.get("ip_address") or raw.get("ip")
    snmp = raw.get("snmp") if isinstance(raw.get("snmp"), dict) else {}
    management_vlan = as_int(raw.get("management_vlan"), as_int(raw.get("vlan"), 99))
    return {
        "id": str(raw.get("id") or fallback_id),
        "kind": kind,
        "enabled": raw.get("enabled", True) is not False,
        "planned": bool(raw.get("planned", False)),
        "optional": bool(raw.get("optional", False)),
        "name": str(raw.get("name") or fallback_id),
        "hostname": str(raw.get("hostname") or ""),
        "vendor": str(raw.get("vendor") or ""),
        "model": str(raw.get("model") or ""),
        "role": str(raw.get("role") or kind),
        "location": str(raw.get("location") or ""),
        "ip_address": str(ip_address) if ip_address else None,
        "web_url": str(raw.get("web_url") or (f"http://{ip_address}" if ip_address else "")) or None,
        "vlan": as_int(raw.get("vlan"), 99),
        "management_vlan": management_vlan,
        "snmp_enabled": raw.get("snmp_enabled", kind != "ap") is not False,
        "snmp": {
            "version": snmp_version(snmp.get("version"), "2c"),
            "community": str(snmp.get("community") or ""),
            "community_env": str(snmp.get("community_env") or ""),
        },
        "uplink_port": str(raw.get("uplink_port") or ""),
        "expected_switch_id": str(raw.get("expected_switch_id") or raw.get("uplink_switch_id") or raw.get("switch_id") or "") or None,
        "expected_switch_port": as_int(raw.get("expected_switch_port") or raw.get("uplink_port_number") or raw.get("switch_port")),
        "expected_switch_port_label": str(raw.get("expected_switch_port_label") or raw.get("uplink_port_label") or "") or None,
        "expected_sys_name": str(raw.get("expected_sys_name") or raw.get("name") or fallback_id),
        "expected_sys_location": str(raw.get("expected_sys_location") or raw.get("location") or ""),
        "mac_address": str(raw.get("mac_address") or raw.get("mac") or "").lower() or None,
    }


def _expand_switch_ports(
    raw_ports: Any,
    port_count: int,
    profiles: dict[str, dict[str, Any]],
    default_layout: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    ports: dict[int, dict[str, Any]] = {}

    def assign(number: int, spec: Any) -> None:
        if number < 1 or number > port_count:
            return
        if isinstance(spec, str):
            spec = {"profile": spec}
        if not isinstance(spec, dict):
            return
        profile_name = str(spec.get("profile") or spec.get("role") or "UNUSED").upper()
        profile = profiles.get(profile_name)
        if profile is None:
            profile = {**profiles["UNUSED"], "name": profile_name, "label": profile_name}
        entry = {
            "number": number,
            "profile": profile["name"],
            "type": profile["type"],
            "color": str(spec.get("color") or profile["color"]),
            "label": str(spec.get("label") if spec.get("label") is not None else profile["label"]),
            "vlan": as_int(spec.get("vlan"), profile.get("vlan")),
            "tagged": as_int_list(spec.get("tagged")) if spec.get("tagged") is not None else list(profile.get("tagged") or []),
            "native": as_int(spec.get("native"), profile.get("native")),
            "neighbor": str(spec.get("neighbor") or "") or None,
            "notes": str(spec.get("notes") or ""),
            "configured": True,
        }
        if entry["type"] == "access" and entry["vlan"] is not None:
            entry["native"] = entry["vlan"]
        ports[number] = entry

    # ``ports: default`` or ``ports: {_default: true, ...}`` prefills the
    # standard access layout (1-8 CONTROL, 9-12 AUDIO, 13-16 LIGHT, 17-20 VIDEO).
    use_default = raw_ports == "default" or (isinstance(raw_ports, dict) and bool(raw_ports.get("_default")))
    if use_default and isinstance(default_layout, dict):
        for key, spec in default_layout.items():
            for number in expand_port_keys(key):
                assign(number, spec)
    if isinstance(raw_ports, dict):
        for key, spec in raw_ports.items():
            if str(key).startswith("_"):
                continue
            for number in expand_port_keys(key):
                assign(number, spec)
    elif isinstance(raw_ports, list):
        for index, spec in enumerate(raw_ports, start=1):
            assign(index, spec)

    for number in range(1, port_count + 1):
        if number not in ports:
            ports[number] = {
                "number": number,
                "profile": "UNUSED",
                "type": "unused",
                "color": "unused",
                "label": "",
                "vlan": None,
                "tagged": [],
                "native": None,
                "neighbor": None,
                "notes": "",
                "configured": False,
            }
    return ports


def switch_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = port_profiles(config)
    default_layout = config.get("default_access_layout") or {}
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(config.get("switches") or [], start=1):
        if not isinstance(raw, dict):
            continue
        entry = _normalize_device(raw, "switch", f"switch-{index}")
        port_count = max(1, min(128, as_int(raw.get("port_count"), 28) or 28))
        layout = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}
        entry.update(
            {
                "port_count": port_count,
                "layout": {
                    "columns": max(1, min(64, as_int(layout.get("columns"), 14 if port_count <= 28 else 24) or 14)),
                    "rows": max(1, min(8, as_int(layout.get("rows"), 2) or 2)),
                },
                "sfp_ports": as_int_list(raw.get("sfp_ports")),
                "rstp_root_expected": bool(raw.get("rstp_root_expected", False)),
                "dante_capable": bool(raw.get("dante_capable", "dell" in str(raw.get("vendor") or "").lower())),
                "ports": _expand_switch_ports(raw.get("ports"), port_count, profiles, default_layout),
                "raw_ports": raw.get("ports") if isinstance(raw.get("ports"), dict) else {},
            }
        )
        result.append(entry)
    return result


def infrastructure_entries(config: dict[str, Any]) -> dict[str, Any]:
    """Return ``{"router": {...}|None, "access_points": [...], "station": {...}}``."""
    configured = config.get("infrastructure") or {}
    router = None
    access_points: list[dict[str, Any]] = []
    if isinstance(configured, dict):
        if isinstance(configured.get("router"), dict):
            router = _normalize_device(configured["router"], "router", "router")
        for index, raw in enumerate(configured.get("access_points") or [], start=1):
            if isinstance(raw, dict):
                access_points.append(_normalize_device(raw, "ap", f"ap-{index}"))
    elif isinstance(configured, list):
        # Legacy list shape: pick the router by role.
        for index, raw in enumerate(configured, start=1):
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "").lower()
            if "router" in role and router is None:
                router = _normalize_device(raw, "router", "router")
            elif "ap" in role or "access point" in role:
                access_points.append(_normalize_device(raw, "ap", f"ap-{index}"))
    station_raw = config.get("station") or {}
    station = _normalize_device(
        {
            **station_raw,
            "id": station_raw.get("id") or "aboutus-pi",
            "name": station_raw.get("name") or station_raw.get("hostname") or "aboutus-net",
            "role": "monitor",
            "snmp_enabled": False,
        },
        "monitor",
        "aboutus-pi",
    )
    return {"router": router, "access_points": access_points, "station": station}


def inventory_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(config.get("inventory") or [], start=1):
        if not isinstance(raw, dict):
            continue
        ip_address = raw.get("ip_address") or raw.get("ip")
        result.append(
            {
                "id": str(raw.get("id") or f"inventory-{index}"),
                "name": str(raw.get("name") or ip_address or f"Device {index}"),
                "ip_address": str(ip_address) if ip_address else None,
                "mac_address": str(raw.get("mac_address") or raw.get("mac") or "").lower() or None,
                "vlan": as_int(raw.get("vlan")),
                "role": str(raw.get("role") or ""),
                "owner": str(raw.get("owner") or ""),
                "notes": str(raw.get("notes") or ""),
                "expected": bool(raw.get("expected", True)),
                "web_url": str(raw.get("web_url") or "") or None,
            }
        )
    return result


def normalized(config: dict[str, Any]) -> dict[str, Any]:
    infra = infrastructure_entries(config)
    return {
        "raw": config,
        "station": infra["station"],
        "router": infra["router"],
        "access_points": infra["access_points"],
        "switches": switch_entries(config),
        "vlans": vlan_entries(config),
        "policy": policy_settings(config),
        "port_profiles": port_profiles(config),
        "monitoring": monitoring_settings(config),
        "check_defaults": config.get("check_defaults") or {},
        "discovery": config.get("discovery") or {},
        "snmp": config.get("snmp") or {},
        "internet": config.get("internet") or {},
        "inventory": inventory_entries(config),
        "colors": ui_colors(config),
    }


def load_normalized() -> dict[str, Any]:
    return normalized(load_config())
