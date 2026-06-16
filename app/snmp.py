from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections import defaultdict
from typing import Any


UNKNOWN_LABEL = "Unknown"

SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"

IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"
IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

DOT1D_BASE_PORT_IF_INDEX = "1.3.6.1.2.1.17.1.4.1.2"
DOT1D_TP_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"

POE_PORT_POWER = "1.3.6.1.2.1.105.1.1.1.3"
POE_PORT_STATUS = "1.3.6.1.2.1.105.1.1.1.6"


def snmp_settings(config: dict[str, Any]) -> dict[str, Any]:
    configured = config.get("snmp") or {}
    community_env = str(configured.get("community_env") or "ABOUTUS_SNMP_COMMUNITY")
    community = os.getenv(community_env) or os.getenv("ABOUTUS_SNMP_COMMUNITY") or configured.get("community")
    enabled = bool(configured.get("enabled", False) or community)
    timeout_seconds = float(os.getenv("ABOUTUS_SNMP_TIMEOUT_SECONDS") or configured.get("timeout_seconds") or 2)
    retries = int(os.getenv("ABOUTUS_SNMP_RETRIES") or configured.get("retries") or 0)
    return {
        "enabled": enabled,
        "version": str(configured.get("version") or os.getenv("ABOUTUS_SNMP_VERSION") or "2c"),
        "community": str(community or ""),
        "community_env": community_env,
        "timeout_seconds": max(0.5, timeout_seconds),
        "retries": max(0, retries),
        "max_parallel_hosts": max(1, int(configured.get("max_parallel_hosts") or 2)),
        "poll_interfaces": configured.get("poll_interfaces", True) is not False,
        "poll_fdb": configured.get("poll_fdb", True) is not False,
        "poll_poe": bool(configured.get("poll_poe", False)),
        "trusted_edge_ports": configured.get("trusted_edge_ports") or {},
        "uplink_ports": configured.get("uplink_ports") or {},
    }


def _known(value: Any) -> bool:
    if value is None:
        return False
    prepared = str(value).strip()
    return bool(prepared) and prepared.lower() not in {"unknown", "none", "null", "n/a"}


def _clean_value(value: str) -> str:
    prepared = value.strip()
    if " = " in prepared:
        prepared = prepared.split(" = ", 1)[1].strip()
    if ": " in prepared:
        prefix, rest = prepared.split(": ", 1)
        if prefix.strip().isupper() or prefix.strip() in {"STRING", "INTEGER", "Counter32", "Counter64", "Gauge32", "Timeticks"}:
            prepared = rest
    prepared = prepared.strip().strip('"')
    if prepared.startswith("No Such"):
        return UNKNOWN_LABEL
    return prepared or UNKNOWN_LABEL


def _safe_int(value: Any) -> int | None:
    if not _known(value):
        return None
    match = re.search(r"-?\d+", str(value))
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _status_name(value: Any) -> str:
    numeric = _safe_int(value)
    return {
        1: "up",
        2: "down",
        3: "testing",
        4: "unknown",
        5: "dormant",
        6: "notPresent",
        7: "lowerLayerDown",
    }.get(numeric or -1, str(value or UNKNOWN_LABEL))


def _uptime_seconds(value: Any) -> int | None:
    ticks = _safe_int(value)
    if ticks is None:
        return None
    return int(ticks / 100)


def _uptime_text(seconds: int | None) -> str:
    if seconds is None:
        return UNKNOWN_LABEL
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _normalize_mac(value: str) -> str:
    parts = value.replace("-", ".").replace(":", ".").split(".")
    if len(parts) == 6 and all(part.isdigit() for part in parts):
        return ":".join(f"{int(part):02x}" for part in parts)
    prepared = value.strip().lower().replace("-", ":")
    if re.fullmatch(r"[0-9a-f]{12}", prepared):
        return ":".join(prepared[index : index + 2] for index in range(0, 12, 2))
    return prepared


def _port_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _configured_ports(settings: dict[str, Any], switch_id: Any, switch_name: Any, key: str) -> set[str]:
    configured = settings.get(key) or {}
    values: list[Any] = []
    for lookup in (switch_id, switch_name):
        if lookup in configured:
            values.extend(configured.get(lookup) or [])
        lookup_text = str(lookup or "")
        if lookup_text in configured:
            values.extend(configured.get(lookup_text) or [])
    return {_port_key(value) for value in values}


async def _run_command(command: list[str], timeout_seconds: float) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds + 2)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("SNMP command timed out.")

    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip() or stdout.decode(errors="replace").strip()
        raise RuntimeError(message or f"SNMP command exited with {process.returncode}.")
    return stdout.decode(errors="replace")


def _base_command(tool: str, ip_address: str, settings: dict[str, Any]) -> list[str]:
    return [
        tool,
        "-v",
        settings["version"],
        "-c",
        settings["community"],
        "-t",
        str(settings["timeout_seconds"]),
        "-r",
        str(settings["retries"]),
        "-On",
        ip_address,
    ]


async def _snmp_get(ip_address: str, settings: dict[str, Any], oids: list[str]) -> dict[str, str]:
    command = [*_base_command("snmpget", ip_address, settings), *oids]
    output = await _run_command(command, settings["timeout_seconds"])
    result: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        oid, _, value = line.partition(" = ")
        clean_oid = oid.strip().lstrip(".")
        result[clean_oid] = _clean_value(value)
    return result


def _parse_walk(output: str, base_oid: str) -> dict[str, str]:
    result: dict[str, str] = {}
    normalized_base = base_oid.strip().lstrip(".")
    for line in output.splitlines():
        if not line.strip():
            continue
        oid, _, value = line.partition(" = ")
        clean_oid = oid.strip().lstrip(".")
        if not clean_oid.startswith(f"{normalized_base}."):
            continue
        suffix = clean_oid[len(normalized_base) + 1 :]
        result[suffix] = _clean_value(value)
    return result


async def _snmp_walk(ip_address: str, settings: dict[str, Any], oid: str) -> dict[str, str]:
    command = [*_base_command("snmpwalk", ip_address, settings), oid]
    output = await _run_command(command, settings["timeout_seconds"])
    return _parse_walk(output, oid)


def _port_rows(walks: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    names = walks.get(IF_NAME) or {}
    descriptions = walks.get(IF_DESCR) or {}
    aliases = walks.get(IF_ALIAS) or {}
    admin = walks.get(IF_ADMIN_STATUS) or {}
    oper = walks.get(IF_OPER_STATUS) or {}
    speeds = walks.get(IF_HIGH_SPEED) or {}
    in_octets = walks.get(IF_HC_IN_OCTETS) or walks.get(IF_IN_OCTETS) or {}
    out_octets = walks.get(IF_HC_OUT_OCTETS) or walks.get(IF_OUT_OCTETS) or {}
    in_errors = walks.get(IF_IN_ERRORS) or {}
    out_errors = walks.get(IF_OUT_ERRORS) or {}
    poe_power = walks.get(POE_PORT_POWER) or {}
    poe_status = walks.get(POE_PORT_STATUS) or {}

    indexes = sorted(
        set().union(names, descriptions, aliases, admin, oper, speeds, in_octets, out_octets, in_errors, out_errors),
        key=lambda value: int(value) if value.isdigit() else value,
    )
    ports: list[dict[str, Any]] = []
    for index in indexes:
        port_name = names.get(index) or descriptions.get(index) or f"ifIndex {index}"
        ports.append(
            {
                "if_index": index,
                "name": port_name,
                "description": descriptions.get(index) or UNKNOWN_LABEL,
                "alias": aliases.get(index) or UNKNOWN_LABEL,
                "admin_status": _status_name(admin.get(index)),
                "oper_status": _status_name(oper.get(index)),
                "speed_mbps": _safe_int(speeds.get(index)),
                "in_octets": _safe_int(in_octets.get(index)),
                "out_octets": _safe_int(out_octets.get(index)),
                "in_errors": _safe_int(in_errors.get(index)),
                "out_errors": _safe_int(out_errors.get(index)),
                "poe_power": poe_power.get(index) or UNKNOWN_LABEL,
                "poe_status": poe_status.get(index) or UNKNOWN_LABEL,
            }
        )
    return ports


def _fdb_observations(
    item: dict[str, Any],
    settings: dict[str, Any],
    ports: list[dict[str, Any]],
    walks: dict[str, dict[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    fdb = walks.get(DOT1D_TP_FDB_PORT) or {}
    bridge_map = walks.get(DOT1D_BASE_PORT_IF_INDEX) or {}
    ports_by_index = {str(port["if_index"]): port for port in ports}
    trusted_ports = _configured_ports(settings, item.get("id"), item.get("name"), "trusted_edge_ports")
    uplink_ports = _configured_ports(settings, item.get("id"), item.get("name"), "uplink_ports")

    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mac_suffix, bridge_port_value in fdb.items():
        bridge_port = str(_safe_int(bridge_port_value) or bridge_port_value).strip()
        if_index = str(_safe_int(bridge_map.get(bridge_port)) or bridge_map.get(bridge_port) or "")
        port = ports_by_index.get(if_index, {})
        port_name = port.get("name") or f"bridge port {bridge_port}"
        port_identifiers = {_port_key(port_name), _port_key(if_index), _port_key(bridge_port)}
        is_trusted_edge = bool(trusted_ports and port_identifiers.intersection(trusted_ports))
        is_uplink = bool(port_identifiers.intersection(uplink_ports))
        observations[_normalize_mac(mac_suffix)].append(
            {
                "switch_id": item.get("id"),
                "switch_name": item.get("name"),
                "switch_ip": item.get("ip_address"),
                "port": port_name,
                "if_index": if_index or UNKNOWN_LABEL,
                "bridge_port": bridge_port,
                "port_state": port.get("oper_status") or UNKNOWN_LABEL,
                "source": "snmp_fdb",
                "trusted_edge": is_trusted_edge,
                "uplink": is_uplink,
            }
        )
    return observations


async def _poll_host(item: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    ip_address = str(item.get("ip_address") or "")
    gets = await _snmp_get(ip_address, settings, [SYS_DESCR, SYS_UPTIME, SYS_NAME])

    walk_oids = [IF_NAME, IF_DESCR, IF_ALIAS, IF_ADMIN_STATUS, IF_OPER_STATUS, IF_HIGH_SPEED]
    walk_oids.extend([IF_HC_IN_OCTETS, IF_HC_OUT_OCTETS, IF_IN_ERRORS, IF_OUT_ERRORS])
    if settings["poll_fdb"]:
        walk_oids.extend([DOT1D_BASE_PORT_IF_INDEX, DOT1D_TP_FDB_PORT])
    if settings["poll_poe"]:
        walk_oids.extend([POE_PORT_POWER, POE_PORT_STATUS])

    walks: dict[str, dict[str, str]] = {}
    if settings["poll_interfaces"]:
        results = await asyncio.gather(
            *(_snmp_walk(ip_address, settings, oid) for oid in walk_oids),
            return_exceptions=True,
        )
        for oid, result in zip(walk_oids, results):
            if not isinstance(result, Exception):
                walks[oid] = result

    uptime_seconds = _uptime_seconds(gets.get(SYS_UPTIME))
    ports = _port_rows(walks) if settings["poll_interfaces"] else []
    return {
        "id": item.get("id"),
        "ip_address": ip_address,
        "status": "ok",
        "sys_name": gets.get(SYS_NAME) or UNKNOWN_LABEL,
        "sys_descr": gets.get(SYS_DESCR) or UNKNOWN_LABEL,
        "uptime_seconds": uptime_seconds,
        "uptime": _uptime_text(uptime_seconds),
        "ports": ports,
        "port_count": len(ports),
        "ports_up": sum(1 for port in ports if port.get("oper_status") == "up"),
        "fdb_observations": _fdb_observations(item, settings, ports, walks) if settings["poll_fdb"] else {},
    }


def _exact_locations(observations: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    exact: dict[str, dict[str, Any]] = {}
    for mac, entries in observations.items():
        trusted = [entry for entry in entries if entry.get("trusted_edge") and not entry.get("uplink")]
        if len(trusted) == 1:
            exact[mac] = {**trusted[0], "confidence": "snmp_trusted_edge"}
    return exact


async def poll_snmp(config: dict[str, Any], infrastructure: list[dict[str, Any]]) -> dict[str, Any]:
    settings = snmp_settings(config)
    if not settings["enabled"]:
        return {"enabled": False, "status": "disabled", "errors": [], "devices": {}, "mac_locations": {}}
    if not settings["community"]:
        return {
            "enabled": True,
            "status": "community_missing",
            "errors": [f"SNMP enabled but {settings['community_env']} is not set."],
            "devices": {},
            "mac_locations": {},
        }
    if not shutil.which("snmpget") or not shutil.which("snmpwalk"):
        return {
            "enabled": True,
            "status": "missing_tools",
            "errors": ["SNMP tools are not installed. Install the local snmp package to enable polling."],
            "devices": {},
            "mac_locations": {},
        }

    pollable = [item for item in infrastructure if _known(item.get("ip_address"))]
    semaphore = asyncio.Semaphore(settings["max_parallel_hosts"])

    async def guarded(item: dict[str, Any]) -> tuple[str, dict[str, Any] | Exception]:
        async with semaphore:
            try:
                return str(item.get("id")), await _poll_host(item, settings)
            except Exception as exc:
                return str(item.get("id")), exc

    results = await asyncio.gather(*(guarded(item) for item in pollable))
    devices: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    all_observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for key, result in results:
        if isinstance(result, Exception):
            errors.append(f"{key}: {result}")
            devices[key] = {"status": "error", "error": str(result)}
            continue
        devices[key] = result
        for mac, observations in (result.get("fdb_observations") or {}).items():
            all_observations[mac].extend(observations)

    status = "ok"
    if errors and devices:
        status = "partial"
    if errors and not any(device.get("status") == "ok" for device in devices.values()):
        status = "unavailable"

    return {
        "enabled": True,
        "status": status,
        "errors": errors,
        "devices": devices,
        "mac_locations": _exact_locations(all_observations),
        "mac_observation_count": sum(len(entries) for entries in all_observations.values()),
    }
