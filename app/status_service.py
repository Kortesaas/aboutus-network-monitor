from __future__ import annotations

import asyncio
import ipaddress
import time
from typing import Any

from .checks import OFFLINE, ONLINE, UNKNOWN, run_check, utc_now
from .config import load_config
from .discovery import discover_devices, discovery_cache_ttl


UNKNOWN_LABEL = "Unknown"
_SNAPSHOT_CACHE: dict[str, Any] | None = None
_SNAPSHOT_EXPIRES_AT = 0.0
_SNAPSHOT_LOCK = asyncio.Lock()


def _known(value: Any, fallback: str = UNKNOWN_LABEL) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str) and not value.strip():
        return fallback
    return value


def _status_rank(status: str) -> int:
    return {ONLINE: 3, OFFLINE: 2, UNKNOWN: 1}.get(status, 0)


def _unique_sources(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result or ["manual_inventory"]


def _overall_status(results: list[dict[str, Any]]) -> str:
    if any(result["status"] == ONLINE for result in results):
        return ONLINE
    if any(result["status"] == OFFLINE for result in results):
        return OFFLINE
    return UNKNOWN


def _slug(value: Any) -> str:
    return str(value or UNKNOWN_LABEL).strip().lower().replace(" ", "-")


def _ip_sort_key(value: Any) -> tuple[int, int | str]:
    try:
        return (0, int(ipaddress.ip_address(str(value))))
    except ValueError:
        return (1, str(value or ""))


def _normalize_ip(entry: dict[str, Any]) -> str | None:
    ip_address = entry.get("ip_address") or entry.get("ip")
    return str(ip_address) if ip_address else None


def _normalize_name(entry: dict[str, Any]) -> str | None:
    return entry.get("display_name") or entry.get("name")


def _vlan_lookup(vlans: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[tuple[ipaddress._BaseNetwork, dict[str, Any]]]]:
    by_name: dict[str, dict[str, Any]] = {}
    networks: list[tuple[ipaddress._BaseNetwork, dict[str, Any]]] = []

    for vlan in vlans:
        name = str(vlan.get("name") or "").upper()
        if name:
            by_name[name] = vlan
        if vlan.get("id") is not None:
            by_name[str(vlan["id"])] = vlan
        subnet = vlan.get("subnet")
        if subnet:
            try:
                networks.append((ipaddress.ip_network(str(subnet), strict=False), vlan))
            except ValueError:
                continue

    return by_name, networks


def _find_vlan_for_device(
    device: dict[str, Any],
    by_name: dict[str, dict[str, Any]],
    networks: list[tuple[ipaddress._BaseNetwork, dict[str, Any]]],
) -> dict[str, Any] | None:
    vlan_value = device.get("vlan") or device.get("vlan_name") or device.get("vlan_id")
    if vlan_value is not None:
        by_key = by_name.get(str(vlan_value).upper()) or by_name.get(str(vlan_value))
        if by_key:
            return by_key

    ip_address = _normalize_ip(device)
    if not ip_address:
        return None
    try:
        parsed = ipaddress.ip_address(ip_address)
    except ValueError:
        return None

    for network, vlan in networks:
        if parsed in network:
            return vlan
    return None


async def _infrastructure_status(entry: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    ip_address = entry.get("ip_address")
    check = await run_check(entry.get("check"), default_target=ip_address, defaults=defaults)
    return {
        "id": _known(entry.get("id")),
        "name": _known(entry.get("name")),
        "role": _known(entry.get("role")),
        "model": _known(entry.get("model")),
        "ip_address": _known(ip_address),
        "vlan": _known(entry.get("vlan")),
        "status": check["status"],
        "check": check,
    }


async def _vlan_status(entry: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    gateway = entry.get("gateway")
    check = await run_check(entry.get("gateway_check"), default_target=gateway, defaults=defaults)
    return {
        "id": _known(entry.get("id")),
        "name": _known(entry.get("name")),
        "subnet": _known(entry.get("subnet")),
        "gateway": _known(gateway),
        "gateway_status": check["status"],
        "gateway_check": check,
    }


def _inventory_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entry in config.get("inventory") or []:
        prepared = dict(entry)
        prepared["source_key"] = "inventory"
        entries.append(prepared)
    for entry in config.get("manual_inventory") or []:
        prepared = dict(entry)
        prepared["source_key"] = "manual_inventory"
        entries.append(prepared)
    return entries


async def _manual_device_status(
    entry: dict[str, Any],
    defaults: dict[str, Any],
    by_name: dict[str, dict[str, Any]],
    networks: list[tuple[ipaddress._BaseNetwork, dict[str, Any]]],
) -> dict[str, Any]:
    ip_address = _normalize_ip(entry)
    check_config = entry.get("status_check") or entry.get("check")
    if check_config is None and ip_address:
        check_config = {"type": "ping"}
    check = await run_check(check_config, default_target=ip_address, defaults=defaults)
    vlan = _find_vlan_for_device(entry, by_name, networks) or {}
    configured_sources = entry.get("discovery_sources") or [entry.get("source_key") or "manual_inventory"]
    sources = [str(source) for source in configured_sources]
    if check["status"] != UNKNOWN:
        sources.append(str(check["source"]))

    connection = entry.get("connection") or {}
    connected_switch = entry.get("connected_switch") or connection.get("switch_name")
    connected_port = entry.get("connected_port") or connection.get("switch_port")

    return {
        "id": _known(entry.get("id") or _slug(ip_address or _normalize_name(entry))),
        "name": _known(_normalize_name(entry) or ip_address),
        "display_name": _known(_normalize_name(entry) or ip_address),
        "role": _known(entry.get("role")),
        "expected": bool(entry.get("expected", False)),
        "vlan": _known(vlan.get("name") or entry.get("vlan")),
        "vlan_name": _known(vlan.get("name") or entry.get("vlan")),
        "vlan_id": _known(vlan.get("id") or entry.get("vlan_id")),
        "subnet": _known(vlan.get("subnet")),
        "ip_address": _known(ip_address),
        "mac_address": _known(entry.get("mac_address") or entry.get("mac")),
        "hostname": _known(entry.get("hostname")),
        "vendor": _known(entry.get("vendor")),
        "status": check["status"],
        "last_seen": check["checked_at"] if check["status"] == ONLINE else _known(entry.get("last_seen")),
        "discovery_sources": _unique_sources(sources),
        "connected_switch": _known(connected_switch),
        "connected_port": _known(connected_port),
        "mac_source": _known(entry.get("mac_source")),
        "switch_port_confidence": _known(entry.get("switch_port_confidence")),
        "connection": {
            "switch_name": _known(connected_switch),
            "switch_port": _known(connected_port),
            "port_state": _known(connection.get("port_state")),
        },
        "check": check,
    }


def _merge_device(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    base_sources = list(base.get("discovery_sources", []))
    incoming_sources = list(incoming.get("discovery_sources", []))

    manual_preferred = {
        "name",
        "display_name",
        "role",
        "expected",
        "connected_switch",
        "connected_port",
        "switch_port_confidence",
    }
    for key, value in incoming.items():
        if key in {"id", "ip_address", "discovery_sources"}:
            continue
        if key in manual_preferred and _known(merged.get(key)) != UNKNOWN_LABEL:
            continue
        if _known(value) != UNKNOWN_LABEL or _known(merged.get(key)) == UNKNOWN_LABEL:
            merged[key] = value

    if _status_rank(incoming.get("status", UNKNOWN)) > _status_rank(merged.get("status", UNKNOWN)):
        merged["status"] = incoming["status"]
        merged["last_seen"] = incoming.get("last_seen") or merged.get("last_seen")

    merged["discovery_sources"] = _unique_sources([*base_sources, *incoming_sources])
    merged["connection"] = {
        "switch_name": _known(merged.get("connected_switch")),
        "switch_port": _known(merged.get("connected_port")),
        "port_state": _known((merged.get("connection") or {}).get("port_state")),
    }
    return merged


def _normalize_discovered_device(
    entry: dict[str, Any],
    by_name: dict[str, dict[str, Any]],
    networks: list[tuple[ipaddress._BaseNetwork, dict[str, Any]]],
) -> dict[str, Any]:
    vlan = _find_vlan_for_device(entry, by_name, networks) or {}
    ip_address = _normalize_ip(entry)
    return {
        "id": _known(entry.get("id") or f"discovered-{ip_address}"),
        "name": _known(entry.get("name") or entry.get("hostname") or ip_address),
        "display_name": _known(entry.get("display_name") or entry.get("name") or entry.get("hostname") or ip_address),
        "role": _known(entry.get("role") or "discovered"),
        "expected": bool(entry.get("expected", False)),
        "vlan": _known(vlan.get("name") or entry.get("vlan")),
        "vlan_name": _known(vlan.get("name") or entry.get("vlan")),
        "vlan_id": _known(vlan.get("id") or entry.get("vlan_id")),
        "subnet": _known(vlan.get("subnet") or entry.get("subnet")),
        "ip_address": _known(ip_address),
        "mac_address": _known(entry.get("mac_address") or entry.get("mac")),
        "hostname": _known(entry.get("hostname")),
        "vendor": _known(entry.get("vendor")),
        "status": entry.get("status") or ONLINE,
        "last_seen": _known(entry.get("last_seen")),
        "discovery_sources": _unique_sources([str(source) for source in entry.get("discovery_sources", ["nmap"])]),
        "connected_switch": _known(entry.get("connected_switch")),
        "connected_port": _known(entry.get("connected_port")),
        "mac_source": _known(entry.get("mac_source")),
        "switch_port_confidence": _known(entry.get("switch_port_confidence")),
        "connection": {
            "switch_name": _known(entry.get("connected_switch")),
            "switch_port": _known(entry.get("connected_port")),
            "port_state": UNKNOWN_LABEL,
        },
        "check": {
            "status": entry.get("status") or ONLINE,
            "source": "nmap",
            "target": ip_address,
            "latency_ms": None,
            "message": "Discovered by safe nmap ping sweep.",
            "checked_at": entry.get("last_seen") or utc_now(),
        },
    }


async def _devices_status(
    config: dict[str, Any],
    defaults: dict[str, Any],
    discovery: dict[str, Any],
) -> list[dict[str, Any]]:
    vlans = config.get("vlans", [])
    by_name, networks = _vlan_lookup(vlans)
    manual_devices = await asyncio.gather(
        *(
            _manual_device_status(entry, defaults, by_name, networks)
            for entry in _inventory_entries(config)
        )
    )
    discovered_devices = [
        _normalize_discovered_device(entry, by_name, networks)
        for entry in discovery.get("devices", [])
    ]

    merged_by_ip: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []
    for device in [*manual_devices, *discovered_devices]:
        key = str(device.get("ip_address") or device.get("id"))
        if key not in merged_by_ip:
            merged_by_ip[key] = device
            ordered_keys.append(key)
        else:
            merged_by_ip[key] = _merge_device(merged_by_ip[key], device)

    devices = [merged_by_ip[key] for key in ordered_keys]
    return sorted(
        devices,
        key=lambda device: (
            int(device["vlan_id"]) if str(device.get("vlan_id", "")).isdigit() else 999,
            _ip_sort_key(device.get("ip_address")),
            str(device.get("display_name") or ""),
        ),
    )


def _group_devices_by_vlan(vlans: list[dict[str, Any]], devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for vlan in vlans:
        vlan_id = vlan.get("id")
        vlan_name = vlan.get("name")
        members = [
            device
            for device in devices
            if device.get("vlan_id") == vlan_id or device.get("vlan_name") == vlan_name or device.get("vlan") == vlan_name
        ]
        counts = {
            "known": len(members),
            "online": sum(1 for device in members if device.get("status") == ONLINE),
            "offline": sum(1 for device in members if device.get("status") == OFFLINE),
            "unknown": sum(1 for device in members if device.get("status") == UNKNOWN),
        }
        groups.append(
            {
                "id": vlan_id,
                "name": _known(vlan_name),
                "subnet": _known(vlan.get("subnet")),
                "gateway": _known(vlan.get("gateway")),
                "counts": counts,
                "devices": members,
            }
        )
    return groups


def _apply_infrastructure_status(
    devices: list[dict[str, Any]],
    infrastructure: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    infrastructure_by_ip = {
        str(item["ip_address"]): item
        for item in infrastructure
        if _known(item.get("ip_address")) != UNKNOWN_LABEL
    }

    updated: list[dict[str, Any]] = []
    for device in devices:
        prepared = dict(device)
        infra = infrastructure_by_ip.get(str(device.get("ip_address")))
        if infra:
            prepared["discovery_sources"] = _unique_sources(
                [
                    *prepared.get("discovery_sources", []),
                    "infrastructure",
                    str(infra.get("check", {}).get("source") or ""),
                ]
            )
            if _status_rank(infra.get("status", UNKNOWN)) > _status_rank(prepared.get("status", UNKNOWN)):
                prepared["status"] = infra["status"]
                prepared["last_seen"] = (
                    infra.get("check", {}).get("checked_at")
                    if infra["status"] == ONLINE
                    else prepared.get("last_seen")
                )
                prepared["check"] = infra.get("check") or prepared.get("check")
        updated.append(prepared)

    return updated


async def _internet_status(config: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    probes = config.get("internet", {}).get("probes") or []
    if not probes:
        return {"status": UNKNOWN, "probes": []}

    async def run_probe(probe: dict[str, Any]) -> dict[str, Any]:
        check = await run_check(probe, defaults=defaults)
        return {
            "name": _known(probe.get("name") or probe.get("target")),
            "status": check["status"],
            "check": check,
        }

    probe_results = await asyncio.gather(*(run_probe(probe) for probe in probes))
    return {
        "status": _overall_status([probe["check"] for probe in probe_results]),
        "probes": probe_results,
    }


def _build_warnings(
    infrastructure: list[dict[str, Any]],
    internet: dict[str, Any],
    vlans: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    discovery: dict[str, Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if internet.get("status") != ONLINE:
        warnings.append({"severity": "critical", "message": "Internet probes are not reporting online."})

    for item in infrastructure:
        if item.get("status") != ONLINE:
            warnings.append({"severity": "critical", "message": f"{item.get('name')} is {item.get('status')}."})

    for vlan in vlans:
        if vlan.get("gateway_status") != ONLINE:
            warnings.append(
                {
                    "severity": "warning",
                    "message": f"{vlan.get('name')} gateway {vlan.get('gateway')} is {vlan.get('gateway_status')}.",
                }
            )

    for error in discovery.get("errors", []):
        warnings.append({"severity": "warning", "message": f"Discovery: {error}"})

    for device in devices:
        if device.get("expected") and device.get("status") != ONLINE:
            warnings.append(
                {
                    "severity": "warning",
                    "message": f"Expected device {device.get('display_name')} is {device.get('status')}.",
                }
            )

    return warnings


def _build_topology(
    infrastructure: list[dict[str, Any]],
    internet: dict[str, Any],
    vlan_groups: list[dict[str, Any]],
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {
            "id": "internet",
            "label": "Internet / 5G Router",
            "type": "wan",
            "status": internet.get("status", UNKNOWN),
        }
    ]
    nodes.extend(
        {
            "id": str(item.get("id")),
            "label": str(item.get("name")),
            "type": "infrastructure",
            "role": item.get("role"),
            "status": item.get("status", UNKNOWN),
            "ip_address": item.get("ip_address"),
        }
        for item in infrastructure
    )
    nodes.extend(
        {
            "id": f"vlan-{group['id']}",
            "label": f"{group['name']} / VLAN {group['id']}",
            "type": "vlan",
            "status": ONLINE if group["counts"]["offline"] == 0 else "warning",
            "subnet": group["subnet"],
            "counts": group["counts"],
        }
        for group in vlan_groups
    )

    infra_ids = [str(item.get("id")) for item in infrastructure]
    links: list[dict[str, str]] = []
    if infra_ids:
        links.append({"source": "internet", "target": infra_ids[0], "label": "WAN"})
    for source, target in zip(infra_ids, infra_ids[1:]):
        links.append({"source": source, "target": target, "label": "uplink"})
    if infra_ids:
        parent = infra_ids[-1]
        links.extend({"source": parent, "target": f"vlan-{group['id']}", "label": "VLAN"} for group in vlan_groups)

    return {"nodes": nodes, "links": links, "warnings": warnings}


async def _build_snapshot_uncached() -> dict[str, Any]:
    config = load_config()
    defaults = config.get("check_defaults") or {}

    infrastructure, vlans, internet, discovery = await asyncio.gather(
        asyncio.gather(*(_infrastructure_status(entry, defaults) for entry in config.get("infrastructure", []))),
        asyncio.gather(*(_vlan_status(entry, defaults) for entry in config.get("vlans", []))),
        _internet_status(config, defaults),
        discover_devices(config),
    )
    devices = await _devices_status(config, defaults, discovery)
    devices = _apply_infrastructure_status(devices, infrastructure)
    devices_by_vlan = _group_devices_by_vlan(config.get("vlans", []), devices)
    warnings = _build_warnings(infrastructure, internet, vlans, devices, discovery)
    topology = _build_topology(infrastructure, internet, devices_by_vlan, warnings)

    return {
        "generated_at": utc_now(),
        "station": config.get("station") or {},
        "infrastructure": infrastructure,
        "internet": internet,
        "vlans": vlans,
        "devices": devices,
        "devices_by_vlan": devices_by_vlan,
        "discovery": {
            key: value
            for key, value in discovery.items()
            if key != "devices"
        },
        "topology": topology,
        "warnings": warnings,
    }


async def build_snapshot(force_refresh: bool = False) -> dict[str, Any]:
    global _SNAPSHOT_CACHE, _SNAPSHOT_EXPIRES_AT

    now = time.monotonic()
    if not force_refresh and _SNAPSHOT_CACHE and now < _SNAPSHOT_EXPIRES_AT:
        return _SNAPSHOT_CACHE

    async with _SNAPSHOT_LOCK:
        now = time.monotonic()
        if not force_refresh and _SNAPSHOT_CACHE and now < _SNAPSHOT_EXPIRES_AT:
            return _SNAPSHOT_CACHE

        config = load_config()
        ttl = discovery_cache_ttl(config)
        snapshot = await _build_snapshot_uncached()
        _SNAPSHOT_CACHE = snapshot
        _SNAPSHOT_EXPIRES_AT = time.monotonic() + ttl
        return snapshot


async def build_status() -> dict[str, Any]:
    return await build_snapshot()


async def build_devices() -> dict[str, Any]:
    snapshot = await build_snapshot()
    return {
        "generated_at": snapshot["generated_at"],
        "devices": snapshot["devices"],
        "devices_by_vlan": snapshot["devices_by_vlan"],
        "discovery": snapshot["discovery"],
    }


async def build_topology() -> dict[str, Any]:
    snapshot = await build_snapshot()
    return {
        "generated_at": snapshot["generated_at"],
        "topology": snapshot["topology"],
    }
