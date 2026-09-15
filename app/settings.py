"""Editable settings: the subset of ``network.yaml`` exposed to the Settings page."""

from __future__ import annotations

import re
from typing import Any

from .config import DEFAULT_MONITORING, as_int, as_int_list, expand_port_keys, load_config, port_profiles, save_config, snmp_version


def _clean_text(value: Any, fallback: str = "") -> str:
    text = str(value if value is not None else "").strip()
    return text if text else fallback


def _clean_id(value: Any, fallback: str) -> str:
    text = _clean_text(value, fallback).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


def _clean_ip(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", text) and all(0 <= int(part) <= 255 for part in text.split(".")):
        return text
    raise ValueError(f"Invalid IPv4 address: {text}")


def _clean_url(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if not re.match(r"^https?://", text):
        text = "http://" + text
    return text


def _device_payload(payload: dict[str, Any], current: dict[str, Any], fallback_id: str) -> dict[str, Any]:
    snmp_payload = payload.get("snmp") if isinstance(payload.get("snmp"), dict) else {}
    current_snmp = current.get("snmp") if isinstance(current.get("snmp"), dict) else {}
    result = {
        **current,
        "id": _clean_id(payload.get("id"), str(current.get("id") or fallback_id)),
        "name": _clean_text(payload.get("name"), str(current.get("name") or fallback_id)),
        "hostname": _clean_text(payload.get("hostname"), "") if "hostname" in payload else current.get("hostname", ""),
        "vendor": _clean_text(payload.get("vendor"), "") if "vendor" in payload else current.get("vendor", ""),
        "model": _clean_text(payload.get("model"), "") if "model" in payload else current.get("model", ""),
        "location": _clean_text(payload.get("location"), "") if "location" in payload else current.get("location", ""),
        "ip_address": _clean_ip(payload.get("ip_address")) if "ip_address" in payload else current.get("ip_address"),
        "web_url": _clean_url(payload.get("web_url")) if "web_url" in payload else current.get("web_url"),
        "vlan": as_int(payload.get("vlan"), as_int(current.get("vlan"), 99)),
        "enabled": bool(payload.get("enabled", current.get("enabled", True))),
        "planned": bool(payload.get("planned", current.get("planned", False))),
        "optional": bool(payload.get("optional", current.get("optional", False))),
        "snmp_enabled": bool(payload.get("snmp_enabled", current.get("snmp_enabled", True))),
        "snmp": {
            "version": snmp_version(snmp_payload.get("version"), str(current_snmp.get("version") or "2c")),
            "community": _clean_text(snmp_payload.get("community"), "") if "community" in snmp_payload else str(current_snmp.get("community") or ""),
            "community_env": _clean_text(snmp_payload.get("community_env"), "") if "community_env" in snmp_payload else str(current_snmp.get("community_env") or ""),
        },
    }
    if not result["snmp"]["community"]:
        result["snmp"].pop("community", None)
    if not result["snmp"]["community_env"]:
        result["snmp"].pop("community_env", None)
    if not result["web_url"] and result.get("ip_address"):
        result["web_url"] = f"http://{result['ip_address']}"
    if "management_vlan" in payload:
        result["management_vlan"] = as_int(payload.get("management_vlan"), as_int(result.get("vlan"), 99))
    elif "management_vlan" in current:
        result["management_vlan"] = as_int(current.get("management_vlan"), as_int(result.get("vlan"), 99))
    for field in ("expected_switch_id", "expected_switch_port_label", "expected_sys_name", "expected_sys_location"):
        if field in payload:
            value = _clean_text(payload.get(field))
            if value:
                result[field] = value
            else:
                result.pop(field, None)
    if "expected_switch_port" in payload:
        value = as_int(payload.get("expected_switch_port"))
        if value is None:
            result.pop("expected_switch_port", None)
        else:
            result["expected_switch_port"] = value
    return result


def _ports_payload(ports: Any, port_count: int, profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Accept ``{"1-8": "CONTROL", "23": {profile, label, neighbor, notes}}`` or a list of per-port objects."""
    result: dict[str, Any] = {}
    if isinstance(ports, list):
        ports = {str(entry.get("number") or index): entry for index, entry in enumerate(ports, start=1) if isinstance(entry, dict)}
    if not isinstance(ports, dict):
        return result
    for key, spec in ports.items():
        numbers = [number for number in expand_port_keys(key) if 1 <= number <= port_count]
        if not numbers:
            continue
        if isinstance(spec, str):
            profile = spec.strip().upper()
            if profile in profiles:
                result[str(key).strip()] = profile
            continue
        if not isinstance(spec, dict):
            continue
        profile = _clean_text(spec.get("profile"), "UNUSED").upper()
        if profile not in profiles:
            profile = "UNUSED"
        if profile == "UNUSED" and not any(spec.get(field) for field in ("label", "notes", "neighbor")):
            continue
        entry: dict[str, Any] = {"profile": profile}
        for field in ("label", "notes", "neighbor"):
            value = _clean_text(spec.get(field))
            if value:
                entry[field] = value
        if spec.get("vlan") not in (None, ""):
            entry["vlan"] = as_int(spec.get("vlan"))
        if spec.get("tagged") not in (None, ""):
            entry["tagged"] = as_int_list(spec.get("tagged"))
        if spec.get("native") not in (None, ""):
            entry["native"] = as_int(spec.get("native"))
        result[str(key).strip()] = entry
    return result


def settings_view() -> dict[str, Any]:
    config = load_config()
    infrastructure = config.get("infrastructure") if isinstance(config.get("infrastructure"), dict) else {}
    monitoring = {key: as_int((config.get("monitoring") or {}).get(key), value) for key, value in DEFAULT_MONITORING.items()}
    return {
        "station": config.get("station") or {},
        "router": infrastructure.get("router") or {},
        "access_points": infrastructure.get("access_points") or [],
        "switches": [{**switch, "ports": switch.get("ports") or {}} for switch in config.get("switches") or [] if isinstance(switch, dict)],
        "vlans": config.get("vlans") or [],
        "policy": config.get("policy") or {},
        "port_profiles": port_profiles(config),
        "default_access_layout": config.get("default_access_layout") or {},
        "monitoring": monitoring,
        "snmp": {key: value for key, value in (config.get("snmp") or {}).items()},
        "discovery": config.get("discovery") or {},
        "internet": config.get("internet") or {},
        "inventory": config.get("inventory") or [],
        "check_defaults": config.get("check_defaults") or {},
    }


def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Settings payload must be an object.")
    config = load_config()
    profiles = port_profiles(config)
    infrastructure = config.get("infrastructure") if isinstance(config.get("infrastructure"), dict) else {}

    if isinstance(payload.get("station"), dict):
        station = payload["station"]
        current = config.get("station") or {}
        config["station"] = {
            **current,
            "id": _clean_id(station.get("id"), str(current.get("id") or "aboutus-pi")),
            "name": _clean_text(station.get("name"), str(current.get("name") or "aboutus-net")),
            "hostname": _clean_text(station.get("hostname"), str(current.get("hostname") or "aboutus-net")),
            "ip_address": _clean_ip(station.get("ip_address")) or current.get("ip_address") or "192.168.99.2",
            "vlan": as_int(station.get("vlan"), as_int(current.get("vlan"), 99)),
            "role": "monitor",
            "model": _clean_text(station.get("model"), str(current.get("model") or "Raspberry Pi")),
            "web_url": _clean_url(station.get("web_url")) if "web_url" in station else current.get("web_url"),
        }

    if isinstance(payload.get("router"), dict):
        infrastructure["router"] = _device_payload(payload["router"], infrastructure.get("router") or {}, "lancom-router")
        if "uplink_port" in payload["router"]:
            infrastructure["router"]["uplink_port"] = _clean_text(payload["router"].get("uplink_port"))
    if isinstance(payload.get("access_points"), list):
        current_aps = {str(item.get("id")): item for item in infrastructure.get("access_points") or [] if isinstance(item, dict)}
        aps = []
        for index, item in enumerate(payload["access_points"], start=1):
            if not isinstance(item, dict) or item.get("_remove"):
                continue
            entry = _device_payload(item, current_aps.get(str(item.get("id"))) or {}, f"ap-{index}")
            entry.setdefault("snmp_enabled", False)
            aps.append(entry)
        infrastructure["access_points"] = aps
    config["infrastructure"] = infrastructure

    if isinstance(payload.get("switches"), list):
        current_switches = {str(item.get("id")): item for item in config.get("switches") or [] if isinstance(item, dict)}
        switches = []
        used: set[str] = set()
        for index, item in enumerate(payload["switches"], start=1):
            if not isinstance(item, dict) or item.get("_remove"):
                continue
            current = current_switches.get(str(item.get("id"))) or {}
            entry = _device_payload(item, current, f"switch-{index}")
            base_id = entry["id"]
            suffix = 2
            while entry["id"] in used:
                entry["id"] = f"{base_id}-{suffix}"
                suffix += 1
            used.add(entry["id"])
            port_count = max(1, min(128, as_int(item.get("port_count"), as_int(current.get("port_count"), 28)) or 28))
            layout = item.get("layout") if isinstance(item.get("layout"), dict) else (current.get("layout") or {})
            entry.update(
                {
                    "role": _clean_text(item.get("role"), str(current.get("role") or "switch")),
                    "port_count": port_count,
                    "layout": {"columns": max(1, min(64, as_int(layout.get("columns"), 14 if port_count <= 28 else 24) or 14)), "rows": max(1, min(8, as_int(layout.get("rows"), 2) or 2))},
                    "sfp_ports": as_int_list(item.get("sfp_ports")) if "sfp_ports" in item else as_int_list(current.get("sfp_ports")),
                    "rstp_root_expected": bool(item.get("rstp_root_expected", current.get("rstp_root_expected", False))),
                    "dante_capable": bool(item.get("dante_capable", current.get("dante_capable", "dell" in str(entry.get("vendor") or "").lower()))),
                    "dante_qos_verified": bool(item.get("dante_qos_verified", current.get("dante_qos_verified", False))),
                    "eee_disabled_verified": bool(item.get("eee_disabled_verified", current.get("eee_disabled_verified", False))),
                    "ports": _ports_payload(item.get("ports"), port_count, profiles) if "ports" in item else (current.get("ports") or {}),
                }
            )
            switches.append(entry)
        config["switches"] = switches

    if isinstance(payload.get("vlans"), list):
        vlans = []
        for item in payload["vlans"]:
            if not isinstance(item, dict) or item.get("_remove"):
                continue
            vlan_id = as_int(item.get("id"))
            if vlan_id is None or vlan_id < 1 or vlan_id > 4094:
                continue
            subnet = _clean_text(item.get("subnet")) or None
            if subnet and not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}", subnet):
                raise ValueError(f"Invalid subnet for VLAN {vlan_id}: {subnet}")
            vlans.append(
                {
                    "id": vlan_id,
                    "name": _clean_text(item.get("name"), f"VLAN{vlan_id}").upper(),
                    "description": _clean_text(item.get("description")),
                    "subnet": subnet,
                    "gateway": _clean_ip(item.get("gateway")),
                    "color": _clean_text(item.get("color"), "unused"),
                    "monitor": bool(item.get("monitor", bool(subnet))),
                    "isolated": bool(item.get("isolated", False)),
                }
            )
        if vlans:
            config["vlans"] = vlans

    if isinstance(payload.get("policy"), dict):
        policy = dict(config.get("policy") or {})
        incoming = payload["policy"]
        for key in ("trunk_vlans", "forbidden_vlans", "forbidden_trunk_vlans"):
            if key in incoming:
                policy[key] = as_int_list(incoming.get(key))
        if "deprecated_names" in incoming:
            names = incoming["deprecated_names"]
            if isinstance(names, str):
                names = [part for part in re.split(r"[,\s]+", names) if part]
            policy["deprecated_names"] = [str(name).upper() for name in names or []]
        if isinstance(incoming.get("ap_trunk"), dict):
            policy["ap_trunk"] = {"native_vlan": as_int(incoming["ap_trunk"].get("native_vlan"), 99), "tagged_vlans": as_int_list(incoming["ap_trunk"].get("tagged_vlans")) or [10, 20, 30, 40]}
        if "rstp_root_switch" in incoming:
            policy["rstp_root_switch"] = _clean_text(incoming.get("rstp_root_switch"), "dell-foh")
        if "dante_switches" in incoming:
            value = incoming["dante_switches"]
            if isinstance(value, str):
                value = [part for part in re.split(r"[,\s]+", value) if part]
            policy["dante_switches"] = [str(item) for item in value or []]
        if "dante_vlan" in incoming:
            policy["dante_vlan"] = as_int(incoming.get("dante_vlan"), 20)
        config["policy"] = policy

    if isinstance(payload.get("port_profiles"), dict):
        current_profiles = dict(config.get("port_profiles") or {})
        for name, spec in payload["port_profiles"].items():
            key = _clean_text(name).upper()
            if not key:
                continue
            if spec is None or (isinstance(spec, dict) and spec.get("_remove")):
                current_profiles.pop(key, None)
                continue
            if not isinstance(spec, dict):
                continue
            ptype = _clean_text(spec.get("type"), "access").lower()
            if ptype not in {"access", "trunk", "ap_trunk", "wan_pass", "unused"}:
                ptype = "access"
            entry: dict[str, Any] = {"type": ptype, "color": _clean_text(spec.get("color"), "unused"), "label": _clean_text(spec.get("label"), key)}
            if spec.get("vlan") not in (None, ""):
                entry["vlan"] = as_int(spec.get("vlan"))
            if spec.get("tagged") not in (None, ""):
                entry["tagged"] = as_int_list(spec.get("tagged"))
            if spec.get("native") not in (None, ""):
                entry["native"] = as_int(spec.get("native"))
            current_profiles[key] = entry
        config["port_profiles"] = current_profiles

    if isinstance(payload.get("monitoring"), dict):
        monitoring = dict(config.get("monitoring") or {})
        for key in DEFAULT_MONITORING:
            if key in payload["monitoring"]:
                number = as_int(payload["monitoring"].get(key))
                if number is not None:
                    monitoring[key] = max(0, number)
        config["monitoring"] = monitoring

    if isinstance(payload.get("snmp"), dict):
        snmp = dict(config.get("snmp") or {})
        incoming = payload["snmp"]
        if "enabled" in incoming:
            snmp["enabled"] = bool(incoming["enabled"])
        if "version" in incoming:
            snmp["version"] = snmp_version(incoming.get("version"), "2c")
        if "timeout_seconds" in incoming:
            snmp["timeout_seconds"] = max(0.5, min(10.0, float(incoming.get("timeout_seconds") or 2)))
        if "retries" in incoming:
            snmp["retries"] = max(0, min(3, as_int(incoming.get("retries"), 1) or 0))
        if "max_parallel_hosts" in incoming:
            snmp["max_parallel_hosts"] = max(1, min(8, as_int(incoming.get("max_parallel_hosts"), 3) or 3))
        if "community" in incoming:
            community = _clean_text(incoming.get("community"))
            if community:
                snmp["community"] = community
            else:
                snmp.pop("community", None)
        config["snmp"] = snmp

    if isinstance(payload.get("discovery"), dict):
        discovery = dict(config.get("discovery") or {})
        incoming = payload["discovery"]
        if "enabled" in incoming:
            discovery["enabled"] = bool(incoming["enabled"])
        if "tool" in incoming:
            tool = _clean_text(incoming.get("tool"), "fping").lower()
            discovery["tool"] = tool if tool in {"fping", "nmap"} else "fping"
        if "timeout_seconds_per_subnet" in incoming:
            discovery["timeout_seconds_per_subnet"] = max(5, min(120, as_int(incoming.get("timeout_seconds_per_subnet"), 20) or 20))
        if "max_parallel_scans" in incoming:
            discovery["max_parallel_scans"] = max(1, min(6, as_int(incoming.get("max_parallel_scans"), 2) or 2))
        config["discovery"] = discovery

    if isinstance(payload.get("inventory"), list):
        inventory = []
        for index, item in enumerate(payload["inventory"], start=1):
            if not isinstance(item, dict) or item.get("_remove"):
                continue
            ip_address = _clean_ip(item.get("ip_address") or item.get("ip"))
            mac = _clean_text(item.get("mac_address") or item.get("mac")).lower() or None
            if not ip_address and not mac:
                continue
            inventory.append({"id": _clean_id(item.get("id"), f"inventory-{index}"), "name": _clean_text(item.get("name"), ip_address or mac or f"Device {index}"), "ip_address": ip_address, "mac_address": mac, "vlan": as_int(item.get("vlan")), "role": _clean_text(item.get("role")), "owner": _clean_text(item.get("owner")), "notes": _clean_text(item.get("notes")), "expected": bool(item.get("expected", True)), "web_url": _clean_url(item.get("web_url"))})
        config["inventory"] = inventory

    if isinstance(payload.get("internet"), dict) and isinstance(payload["internet"].get("probes"), list):
        probes = []
        for probe in payload["internet"]["probes"]:
            if not isinstance(probe, dict) or probe.get("_remove"):
                continue
            target = _clean_text(probe.get("target"))
            if not target:
                continue
            ptype = _clean_text(probe.get("type"), "ping").lower()
            probes.append({"name": _clean_text(probe.get("name"), target), "type": ptype if ptype in {"ping", "https", "http", "tcp"} else "ping", "target": target})
        config["internet"] = {"probes": probes}

    save_config(config)
    return settings_view()
