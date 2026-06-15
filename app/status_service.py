from __future__ import annotations

import asyncio
from typing import Any

from .checks import OFFLINE, ONLINE, UNKNOWN, run_check, utc_now
from .config import load_config


UNKNOWN_LABEL = "Unknown"


def _known(value: Any, fallback: str = UNKNOWN_LABEL) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str) and not value.strip():
        return fallback
    return value


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


async def _manual_device_status(entry: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    ip_address = entry.get("ip_address")
    check_config = entry.get("status_check")
    check = await run_check(check_config, default_target=ip_address, defaults=defaults)
    connection = entry.get("connection") or {}
    configured_sources = entry.get("discovery_sources") or ["manual_inventory"]
    sources = [str(source) for source in configured_sources]
    if check["status"] != UNKNOWN:
        sources.append(str(check["source"]))

    return {
        "id": _known(entry.get("id")),
        "display_name": _known(entry.get("display_name") or entry.get("name")),
        "vlan": _known(entry.get("vlan")),
        "ip_address": _known(ip_address),
        "mac_address": _known(entry.get("mac_address")),
        "hostname": _known(entry.get("hostname")),
        "vendor": _known(entry.get("vendor")),
        "status": check["status"],
        "last_seen": check["checked_at"] if check["status"] == ONLINE else _known(entry.get("last_seen")),
        "discovery_sources": _unique_sources(sources),
        "connection": {
            "switch_name": _known(connection.get("switch_name")),
            "switch_port": _known(connection.get("switch_port")),
            "port_state": _known(connection.get("port_state")),
        },
        "check": check,
    }


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


async def build_status() -> dict[str, Any]:
    config = load_config()
    defaults = config.get("check_defaults") or {}

    infrastructure = await asyncio.gather(
        *(_infrastructure_status(entry, defaults) for entry in config.get("infrastructure", []))
    )
    vlans = await asyncio.gather(*(_vlan_status(entry, defaults) for entry in config.get("vlans", [])))
    devices = await asyncio.gather(
        *(_manual_device_status(entry, defaults) for entry in config.get("manual_inventory", []))
    )
    internet = await _internet_status(config, defaults)

    return {
        "generated_at": utc_now(),
        "station": config.get("station") or {},
        "infrastructure": infrastructure,
        "internet": internet,
        "vlans": vlans,
        "devices": devices,
    }
