"""Read-only SNMP collector built on the net-snmp command line tools.

Per device the collector gathers system information, interface state and
counters, BRIDGE/Q-BRIDGE forwarding tables, VLAN membership (standard
Q-BRIDGE or the TP-Link private VLAN MIB), LLDP neighbours, the standard
spanning-tree scalars and, on Dell N-series switches, the CoS DSCP map used
for the Dante QoS check. Missing OIDs and unsupported devices are handled
gracefully: every section is optional and reported as ``supported: False``.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from collections import defaultdict
from typing import Any

from .checks import utc_now


UNKNOWN = "Unknown"

# System group
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

# Interfaces
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
IF_PHYS_ADDRESS = "1.3.6.1.2.1.2.2.1.6"
IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
IF_LAST_CHANGE = "1.3.6.1.2.1.2.2.1.9"
IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
IF_IN_DISCARDS = "1.3.6.1.2.1.2.2.1.13"
IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
IF_OUT_DISCARDS = "1.3.6.1.2.1.2.2.1.19"
IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"
IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

# BRIDGE-MIB / Q-BRIDGE-MIB
DOT1D_BASE_BRIDGE_ADDRESS = "1.3.6.1.2.1.17.1.1.0"
DOT1D_BASE_PORT_IF_INDEX = "1.3.6.1.2.1.17.1.4.1.2"
DOT1D_STP_PRIORITY = "1.3.6.1.2.1.17.2.2.0"
DOT1D_STP_DESIGNATED_ROOT = "1.3.6.1.2.1.17.2.5.0"
DOT1D_STP_ROOT_COST = "1.3.6.1.2.1.17.2.6.0"
DOT1D_STP_ROOT_PORT = "1.3.6.1.2.1.17.2.7.0"
DOT1D_TP_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
DOT1Q_TP_FDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"
DOT1Q_VLAN_STATIC_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"
DOT1Q_VLAN_STATIC_EGRESS = "1.3.6.1.2.1.17.7.1.4.3.1.2"
DOT1Q_VLAN_STATIC_UNTAGGED = "1.3.6.1.2.1.17.7.1.4.3.1.4"
DOT1Q_PVID = "1.3.6.1.2.1.17.7.1.4.5.1.1"

# ARP
IP_NET_TO_MEDIA_PHYS = "1.3.6.1.2.1.4.22.1.2"
IP_NET_TO_PHYSICAL_PHYS = "1.3.6.1.2.1.4.35.1.4"

# LLDP-MIB
LLDP_LOC_PORT_ID = "1.0.8802.1.1.2.1.3.7.1.3"
LLDP_LOC_PORT_DESC = "1.0.8802.1.1.2.1.3.7.1.4"
LLDP_REM_TABLE = "1.0.8802.1.1.2.1.4.1.1"

# TP-Link private VLAN MIB (T1600G / JetStream)
TPLINK_VLAN_ID = "1.3.6.1.4.1.11863.6.14.1.2.1.1.1"
TPLINK_VLAN_NAME = "1.3.6.1.4.1.11863.6.14.1.2.1.1.2"
TPLINK_VLAN_TAGGED = "1.3.6.1.4.1.11863.6.14.1.2.1.1.3"
TPLINK_VLAN_UNTAGGED = "1.3.6.1.4.1.11863.6.14.1.2.1.1.4"
TPLINK_PORT_PVID = "1.3.6.1.4.1.11863.6.14.1.1.1.1.3"

# Dell N-series (FASTPATH) CoS: DSCP -> traffic class map and interface trust mode
DELL_COS_DSCP_MAP = "1.3.6.1.4.1.674.10895.5000.2.6132.1.1.3.3.1.2.1.3"
DELL_COS_TRUST_MODE = "1.3.6.1.4.1.674.10895.5000.2.6132.1.1.3.3.1.3.1.2"

_COUNTER_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_STATIC_CACHE: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------- settings


def _snmp_version(value: Any, fallback: str = "2c") -> str:
    text = str(value or fallback).strip().lower()
    if text.startswith("v") and text in {"v1", "v2c"}:
        text = text[1:]
    return text or fallback


def snmp_settings(config: dict[str, Any]) -> dict[str, Any]:
    configured = config.get("snmp") or {}
    community_env = str(configured.get("community_env") or "ABOUTUS_SNMP_COMMUNITY")
    community = os.getenv(community_env) or configured.get("community") or ""
    timeout = float(os.getenv("ABOUTUS_SNMP_TIMEOUT_SECONDS") or configured.get("timeout_seconds") or 2)
    retries = int(os.getenv("ABOUTUS_SNMP_RETRIES") or configured.get("retries") or 0)
    return {
        "enabled": configured.get("enabled", True) is not False,
        "version": _snmp_version(configured.get("version") or os.getenv("ABOUTUS_SNMP_VERSION") or "2c"),
        "community": str(community),
        "timeout_seconds": max(0.5, min(10.0, timeout)),
        "retries": max(0, min(3, retries)),
        "max_parallel_hosts": max(1, int(configured.get("max_parallel_hosts") or 3)),
    }


def device_snmp_settings(device: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    device_snmp = device.get("snmp") or {}
    community = ""
    env_name = device_snmp.get("community_env")
    if env_name:
        community = os.getenv(str(env_name)) or ""
    community = community or str(device_snmp.get("community") or "") or defaults.get("community", "")
    return {
        **defaults,
        "version": _snmp_version(device_snmp.get("version") or defaults["version"]),
        "community": community,
    }


def tools_available() -> bool:
    return bool(shutil.which("snmpget") and (shutil.which("snmpbulkwalk") or shutil.which("snmpwalk")))


# --------------------------------------------------------------------------- value parsing


def _split_type(value: str) -> tuple[str, str]:
    text = value.strip()
    match = re.match(r"^([A-Za-z0-9-]+):\s?(.*)$", text, re.S)
    if match and match.group(1) in {"STRING", "INTEGER", "Counter32", "Counter64", "Gauge32", "Hex-STRING", "Timeticks", "OID", "IpAddress", "Unsigned32", "BITS", "Opaque", "Network"}:
        return match.group(1), match.group(2).strip()
    if text.startswith("No Such") or text.startswith("Wrong Type"):
        return "NONE", ""
    return "RAW", text


def clean_string(value: Any) -> str:
    kind, text = _split_type(str(value or ""))
    if kind == "NONE":
        return ""
    if kind == "STRING":
        return text.strip().strip('"')
    if kind == "Hex-STRING":
        return text.strip()
    if kind == "RAW" and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text.strip().strip('"')


def as_int(value: Any) -> int | None:
    kind, text = _split_type(str(value or ""))
    if kind == "NONE":
        return None
    if kind == "Timeticks":
        match = re.search(r"\((\d+)\)", text)
        return int(match.group(1)) if match else None
    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else None


def hex_bytes(value: Any) -> list[int]:
    kind, text = _split_type(str(value or ""))
    if kind == "Hex-STRING":
        return [int(part, 16) for part in re.findall(r"[0-9A-Fa-f]{2}", text)]
    if kind == "STRING":
        stripped = text.strip().strip('"')
        if re.fullmatch(r"(?:[0-9A-Fa-f]{2}[ :-]?){6}", stripped):
            return [int(part, 16) for part in re.findall(r"[0-9A-Fa-f]{2}", stripped)]
        return [ord(char) for char in stripped]
    return []


def mac_from_value(value: Any) -> str | None:
    parts = hex_bytes(value)
    if len(parts) != 6:
        text = clean_string(value)
        match = re.fullmatch(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", text)
        if match:
            return text.lower().replace("-", ":")
        return None
    mac = ":".join(f"{part:02x}" for part in parts)
    if mac in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}:
        return None
    return mac


def mac_from_oid_suffix(parts: list[str]) -> str | None:
    if len(parts) != 6 or not all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
        return None
    mac = ":".join(f"{int(part):02x}" for part in parts)
    if mac in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}:
        return None
    return mac


def decode_port_bitmap(value: Any) -> list[int]:
    ports: list[int] = []
    for byte_index, byte in enumerate(hex_bytes(value)):
        for bit_index in range(8):
            if byte & (1 << (7 - bit_index)):
                ports.append(byte_index * 8 + bit_index + 1)
    return ports


def parse_port_list(text: str) -> list[int]:
    """Parse TP-Link style port lists: ``1/0/21,1/0/23,1/0/25-28``."""
    ports: list[int] = []
    for part in re.split(r"[,\s]+", clean_string(text)):
        if not part:
            continue
        match = re.fullmatch(r"(?:\d+/\d+/)?(\d+)(?:-(?:\d+/\d+/)?(\d+))?", part)
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        ports.extend(range(min(start, end), max(start, end) + 1))
    return ports


def status_name(value: Any) -> str:
    return {1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant", 6: "notPresent", 7: "lowerLayerDown"}.get(as_int(value) or -1, "unknown")


def uptime_text(seconds: int | None) -> str:
    if seconds is None:
        return UNKNOWN
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# --------------------------------------------------------------------------- command runner


class SnmpError(RuntimeError):
    pass


async def _run(command: list[str], timeout_seconds: float) -> str:
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.communicate()
        raise SnmpError("SNMP command timed out.")
    output = stdout.decode(errors="replace")
    if process.returncode != 0:
        message = stderr.decode(errors="replace").strip() or output.strip()
        first = message.splitlines()[0] if message else f"exit {process.returncode}"
        if "No Such" in output or "No Such" in first:
            return output
        raise SnmpError(first)
    return output


def _base(tool: str, ip_address: str, settings: dict[str, Any], extra: list[str] | None = None) -> list[str]:
    return [tool, "-v", settings["version"], "-c", settings["community"], "-t", str(settings["timeout_seconds"]), "-r", str(settings["retries"]), "-On", *(extra or []), ip_address]


def parse_output(output: str) -> dict[str, str]:
    """Parse ``.oid = TYPE: value`` lines, joining wrapped Hex-STRING lines."""
    result: dict[str, str] = {}
    current: str | None = None
    for line in output.splitlines():
        if not line.strip():
            continue
        if line.startswith(".") and " = " in line:
            oid, _, value = line.partition(" = ")
            current = oid.strip().lstrip(".")
            result[current] = value.strip()
        elif current is not None and re.fullmatch(r"(?:[0-9A-Fa-f]{2}\s*)+", line.strip()):
            result[current] = result[current] + " " + line.strip()
    return result


async def snmp_get(ip_address: str, settings: dict[str, Any], oids: list[str]) -> dict[str, str]:
    output = await _run([*_base("snmpget", ip_address, settings), *oids], settings["timeout_seconds"] * (settings["retries"] + 1) + 3)
    return parse_output(output)


def _within(oid: str, base: str) -> bool:
    return oid == base or oid.startswith(base + ".")


async def bulk_walk_columns(ip_address: str, settings: dict[str, Any], oids: list[str], max_rows: int = 5000, repetitions: int = 25) -> dict[str, dict[str, str]]:
    """Walk several table columns in parallel with repeated GETBULK requests.

    One request carries every unfinished column, so a 12-column interface
    table costs a handful of round trips instead of twelve separate walks.
    Agents that truncate large responses are handled by continuing from the
    last OID returned for each column. Falls back to ``snmpwalk`` per column
    when ``snmpbulkget`` is not installed or the agent only speaks v1.
    """
    bases = [oid.strip().lstrip(".") for oid in oids]
    result: dict[str, dict[str, str]] = {base: {} for base in bases}
    use_bulk = shutil.which("snmpbulkget") is not None and settings.get("version") != "1"
    if not use_bulk:
        for base in bases:
            output = await _run([*_base("snmpwalk", ip_address, settings), base], settings["timeout_seconds"] * (settings["retries"] + 1) * 4 + 10)
            for full_oid, value in parse_output(output).items():
                if _within(full_oid, base) and full_oid != base:
                    result[base][full_oid[len(base) + 1 :]] = value
        return result

    pending = {base: base for base in bases}  # base -> next OID to continue from
    rounds = 0
    timeout = settings["timeout_seconds"] * (settings["retries"] + 1) + 3
    while pending and rounds < 200:
        rounds += 1
        order = list(pending)
        command = [*_base("snmpbulkget", ip_address, settings, ["-Cn0", f"-Cr{max(5, min(100, repetitions))}"]), *(pending[base] for base in order)]
        parsed = await _run(command, timeout)
        rows = [(oid.strip().lstrip("."), value) for oid, value in parse_output(parsed).items()]
        if not rows:
            break
        # Rows come back interleaved column by column; attribute each to its base.
        progressed = False
        last_seen: dict[str, str] = {}
        for full_oid, value in rows:
            for base in order:
                if _within(full_oid, base):
                    if full_oid != base and full_oid[len(base) + 1 :] not in result[base]:
                        result[base][full_oid[len(base) + 1 :]] = value
                        progressed = True
                    last_seen[base] = full_oid
                    break
        for base in order:
            if base not in last_seen or len(result[base]) >= max_rows:
                pending.pop(base, None)
            else:
                pending[base] = last_seen[base]
        if not progressed:
            break
    return result


async def snmp_walk(ip_address: str, settings: dict[str, Any], oid: str, max_rows: int = 5000, repetitions: int = 25) -> dict[str, str]:
    base = oid.strip().lstrip(".")
    return (await bulk_walk_columns(ip_address, settings, [base], max_rows, repetitions)).get(base, {})


async def snmp_get_many(ip_address: str, settings: dict[str, Any], oids: list[str], batch_size: int = 20) -> dict[str, str]:
    """GET many instance OIDs in batches. Much faster than GETBULK on slow agents.

    Some agents drop GET requests with many varbinds; a timeout retries once
    with small batches before giving up.
    """
    result: dict[str, str] = {}
    try:
        for start in range(0, len(oids), batch_size):
            result.update(await snmp_get(ip_address, settings, oids[start : start + batch_size]))
    except SnmpError:
        if batch_size <= 8:
            raise
        result = {}
        for start in range(0, len(oids), 8):
            result.update(await snmp_get(ip_address, settings, oids[start : start + 8]))
    return result


async def _safe_walk(ip_address: str, settings: dict[str, Any], oid: str, errors: list[str], repetitions: int = 25) -> dict[str, str]:
    try:
        return await snmp_walk(ip_address, settings, oid, repetitions=repetitions)
    except SnmpError as exc:
        errors.append(f"{oid}: {exc}")
        return {}


# --------------------------------------------------------------------------- interfaces


_PORT_NAME = re.compile(r"^(?P<prefix>[A-Za-z][A-Za-z\- ]*?)\s*(?P<unit>\d+)/(?P<slot>\d+)/(?P<port>\d+)")


def vendor_profile(sys_descr: str, sys_object_id: str, configured_vendor: str = "") -> str:
    haystack = f"{sys_descr} {sys_object_id} {configured_vendor}".lower()
    if "dell" in haystack or "674.10895" in haystack:
        return "dell"
    if "tp-link" in haystack or "jetstream" in haystack or "11863" in haystack:
        return "tplink"
    if "allied" in haystack or "at-gs" in haystack or "207." in sys_object_id:
        return "allied"
    if "lancom" in haystack or "2356" in haystack:
        return "lancom"
    return "generic"


_VIRTUAL_NAME = re.compile(r"(vlan|cpu|loopback|\bnull|tunnel|p2p|wlc|\bpo\d+|\blag\d+|\bwlan-\d+-\d+|trunk\d+|stack|bridge)", re.I)


def _is_physical(name: str, descr: str, if_type: int | None) -> bool:
    if if_type in {24, 53, 131, 135, 136, 161}:  # loopback, propVirtual, tunnel, l2vlan, l3ipvlan, lag
        return False
    if _VIRTUAL_NAME.search(name) or (not name and _VIRTUAL_NAME.search(descr)):
        return False
    return True


def physical_port_numbers(interfaces: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Map ifIndex -> front panel port number.

    Handles ``Gi1/0/1``/``Te1/0/1`` (Dell: Te numbering restarts, so it is
    offset by the Gi count), ``gigabitEthernet 1/0/12 : copper`` (TP-Link),
    ``Port 5`` / ``port5`` (Allied and others) and finally falls back to the
    order of ifIndex values.
    """
    ordered = sorted(interfaces.values(), key=lambda item: int(item["if_index"]))
    result: dict[str, int] = {}
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    unresolved: list[dict[str, Any]] = []
    for interface in ordered:
        name = str(interface.get("name") or "")
        descr = str(interface.get("descr") or "")
        match = _PORT_NAME.match(name) or _PORT_NAME.match(descr)
        if match:
            groups.setdefault(match.group("prefix").strip().lower(), []).append((int(match.group("port")), interface))
            continue
        simple = re.search(r"(?:slot\d+/|port|eth|ethernet|ge|gi)\s*-?\s*(\d+)\b", f"{name} {descr}", re.I)
        if simple:
            result[interface["if_index"]] = int(simple.group(1))
            continue
        unresolved.append(interface)
    offset = 0
    for prefix in sorted(groups, key=lambda key: min(int(item[1]["if_index"]) for item in groups[key])):
        entries = groups[prefix]
        numbers = [number for number, _ in entries]
        base = offset if min(numbers) == 1 and offset else 0
        for number, interface in entries:
            result[interface["if_index"]] = number + base
        offset = max(result.values()) if result else 0
    next_number = (max(result.values()) if result else 0) + 1
    for interface in unresolved:
        result[interface["if_index"]] = next_number
        next_number += 1
    return result


def _attach_rates(device_id: str, interfaces: dict[str, dict[str, Any]]) -> None:
    now = time.monotonic()
    for index, interface in interfaces.items():
        key = (device_id, index)
        previous = _COUNTER_CACHE.get(key)
        interface.update({"in_bps": None, "out_bps": None, "errors_delta": 0, "discards_delta": 0, "sample_seconds": None})
        errors = (interface.get("in_errors") or 0) + (interface.get("out_errors") or 0)
        discards = (interface.get("in_discards") or 0) + (interface.get("out_discards") or 0)
        if previous:
            elapsed = max(0.5, now - previous["time"])
            interface["sample_seconds"] = round(elapsed, 1)
            for direction in ("in", "out"):
                current = interface.get(f"{direction}_octets")
                before = previous.get(f"{direction}_octets")
                if isinstance(current, int) and isinstance(before, int) and current >= before:
                    interface[f"{direction}_bps"] = int((current - before) * 8 / elapsed)
            if errors >= previous.get("errors", 0):
                interface["errors_delta"] = errors - previous.get("errors", 0)
            if discards >= previous.get("discards", 0):
                interface["discards_delta"] = discards - previous.get("discards", 0)
        _COUNTER_CACHE[key] = {
            "time": now,
            "in_octets": interface.get("in_octets"),
            "out_octets": interface.get("out_octets"),
            "errors": errors,
            "discards": discards,
        }


# --------------------------------------------------------------------------- bridge tables


def _bridge_port_map(walks: dict[str, dict[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for bridge_port, value in (walks.get(DOT1D_BASE_PORT_IF_INDEX) or {}).items():
        if_index = as_int(value)
        if if_index is not None:
            result[str(bridge_port)] = str(if_index)
    return result


def _resolve_bridge_port(bridge_port: str, bridge_map: dict[str, str], port_numbers: dict[str, int]) -> tuple[str | None, int | None]:
    """Return (ifIndex, port number) for a bridge port value."""
    if_index = bridge_map.get(bridge_port)
    if if_index and if_index in port_numbers:
        return if_index, port_numbers[if_index]
    if bridge_port in port_numbers:
        return bridge_port, port_numbers[bridge_port]
    if bridge_port.isdigit():
        number = int(bridge_port)
        for index, port_number in port_numbers.items():
            if port_number == number:
                return index, number
    return if_index, None


def _fdb(walks: dict[str, dict[str, str]], bridge_map: dict[str, str], port_numbers: dict[str, int]) -> list[dict[str, Any]]:
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for suffix, value in (walks.get(DOT1Q_TP_FDB_PORT) or {}).items():
        parts = suffix.split(".")
        if len(parts) < 7:
            continue
        mac = mac_from_oid_suffix(parts[1:7])
        bridge_port = as_int(value)
        if not mac or bridge_port is None or bridge_port == 0:
            continue
        if_index, port_number = _resolve_bridge_port(str(bridge_port), bridge_map, port_numbers)
        entries[(mac, parts[0])] = {"mac_address": mac, "vlan_id": int(parts[0]) if parts[0].isdigit() else None, "bridge_port": bridge_port, "if_index": if_index, "port": port_number, "source": "q_bridge"}
    for suffix, value in (walks.get(DOT1D_TP_FDB_PORT) or {}).items():
        parts = suffix.split(".")
        mac = mac_from_oid_suffix(parts[-6:])
        bridge_port = as_int(value)
        if not mac or bridge_port is None or bridge_port == 0:
            continue
        if any(key[0] == mac for key in entries):
            continue
        if_index, port_number = _resolve_bridge_port(str(bridge_port), bridge_map, port_numbers)
        entries[(mac, "")] = {"mac_address": mac, "vlan_id": None, "bridge_port": bridge_port, "if_index": if_index, "port": port_number, "source": "fdb"}
    return [entry for entry in entries.values() if entry.get("port") is not None]


def _bridge_ports_to_numbers(bits: list[int], bridge_map: dict[str, str], port_numbers: dict[str, int]) -> list[int]:
    numbers: list[int] = []
    for bit in bits:
        _if_index, number = _resolve_bridge_port(str(bit), bridge_map, port_numbers)
        if number is not None and number not in numbers:
            numbers.append(number)
    return sorted(numbers)


def _q_bridge_vlans(walks: dict[str, dict[str, str]], bridge_map: dict[str, str], port_numbers: dict[str, int]) -> dict[int, dict[str, Any]]:
    names = walks.get(DOT1Q_VLAN_STATIC_NAME) or {}
    egress = walks.get(DOT1Q_VLAN_STATIC_EGRESS) or {}
    untagged = walks.get(DOT1Q_VLAN_STATIC_UNTAGGED) or {}
    vlans: dict[int, dict[str, Any]] = {}
    for key in set(names) | set(egress) | set(untagged):
        if not key.isdigit():
            continue
        vlan_id = int(key)
        members = _bridge_ports_to_numbers(decode_port_bitmap(egress.get(key)), bridge_map, port_numbers)
        untagged_ports = _bridge_ports_to_numbers(decode_port_bitmap(untagged.get(key)), bridge_map, port_numbers)
        vlans[vlan_id] = {
            "id": vlan_id,
            "name": clean_string(names.get(key)) or f"VLAN {vlan_id}",
            "member_ports": members,
            "untagged_ports": [port for port in untagged_ports if port in members],
            "tagged_ports": [port for port in members if port not in untagged_ports],
            "source": "q_bridge",
        }
    return vlans


def _tplink_vlans(walks: dict[str, dict[str, str]]) -> dict[int, dict[str, Any]]:
    ids = walks.get(TPLINK_VLAN_ID) or {}
    names = walks.get(TPLINK_VLAN_NAME) or {}
    tagged = walks.get(TPLINK_VLAN_TAGGED) or {}
    untagged = walks.get(TPLINK_VLAN_UNTAGGED) or {}
    vlans: dict[int, dict[str, Any]] = {}
    for key in set(ids) | set(names) | set(tagged) | set(untagged):
        vlan_id = as_int(ids.get(key)) if key in ids else (int(key) if key.isdigit() else None)
        if vlan_id is None:
            continue
        tagged_ports = parse_port_list(tagged.get(key, ""))
        untagged_ports = parse_port_list(untagged.get(key, ""))
        vlans[vlan_id] = {
            "id": vlan_id,
            "name": clean_string(names.get(key)) or f"VLAN {vlan_id}",
            "member_ports": sorted(set(tagged_ports) | set(untagged_ports)),
            "untagged_ports": sorted(untagged_ports),
            "tagged_ports": sorted(tagged_ports),
            "source": "tplink_private",
        }
    return vlans


def _pvids(walks: dict[str, dict[str, str]], bridge_map: dict[str, str], port_numbers: dict[str, int], profile: str) -> dict[int, int]:
    pvids: dict[int, int] = {}
    if profile == "tplink" and walks.get(TPLINK_PORT_PVID):
        for if_index, value in walks[TPLINK_PORT_PVID].items():
            vlan = as_int(value)
            number = port_numbers.get(if_index)
            if vlan is not None and number is not None:
                pvids[number] = vlan
        return pvids
    for bridge_port, value in (walks.get(DOT1Q_PVID) or {}).items():
        vlan = as_int(value)
        _if_index, number = _resolve_bridge_port(bridge_port, bridge_map, port_numbers)
        if vlan is not None and number is not None:
            pvids[number] = vlan
    return pvids


# --------------------------------------------------------------------------- lldp / arp / stp / qos


_LLDP_CAPS = {0: "other", 1: "repeater", 2: "bridge", 3: "wlan-ap", 4: "router", 5: "telephone", 6: "docsis", 7: "station"}


def _lldp_capabilities(value: Any) -> list[str]:
    parts = hex_bytes(value)
    if not parts:
        return []
    caps: list[str] = []
    byte = parts[0]
    for bit in range(8):
        if byte & (1 << (7 - bit)):
            caps.append(_LLDP_CAPS.get(bit, str(bit)))
    return caps


def _lldp_neighbors(walks: dict[str, dict[str, str]], interfaces: dict[str, dict[str, Any]], port_numbers: dict[str, int]) -> dict[int, dict[str, Any]]:
    table = walks.get(LLDP_REM_TABLE) or {}
    local_ids = {key: clean_string(value) for key, value in (walks.get(LLDP_LOC_PORT_ID) or {}).items()}
    by_name = {str(item.get("name") or "").lower(): index for index, item in interfaces.items()}
    rows: dict[str, dict[str, str]] = defaultdict(dict)
    for suffix, value in table.items():
        parts = suffix.split(".")
        if len(parts) < 4:
            continue
        column, local_port = parts[0], parts[2]
        rows[f"{local_port}.{parts[3]}"][column] = value
        rows[f"{local_port}.{parts[3]}"]["_local"] = local_port
    neighbors: dict[int, dict[str, Any]] = {}
    for row in rows.values():
        local_port = row.get("_local", "")
        number = None
        local_name = local_ids.get(local_port)
        if local_name and local_name.lower() in by_name:
            number = port_numbers.get(by_name[local_name.lower()])
        if number is None and local_port in port_numbers:
            number = port_numbers[local_port]
        if number is None and local_port.isdigit():
            number = int(local_port)
        if number is None:
            continue
        chassis_subtype = as_int(row.get("4"))
        chassis_id = mac_from_value(row.get("5")) if chassis_subtype == 4 else clean_string(row.get("5"))
        if chassis_subtype == 4 and not chassis_id:
            chassis_id = clean_string(row.get("5"))
        port_subtype = as_int(row.get("6"))
        port_id = mac_from_value(row.get("7")) if port_subtype == 3 else clean_string(row.get("7"))
        if port_subtype == 3 and not port_id:
            port_id = clean_string(row.get("7"))
        neighbor = {
            "local_port": number,
            "chassis_id": chassis_id or "",
            "chassis_mac": chassis_id if chassis_subtype == 4 else None,
            "port_id": port_id or "",
            "port_description": clean_string(row.get("8")),
            "system_name": clean_string(row.get("9")),
            "system_description": clean_string(row.get("10")),
            "capabilities": _lldp_capabilities(row.get("12")) or _lldp_capabilities(row.get("11")),
        }
        current = neighbors.get(number)
        if not current or (neighbor["system_name"] and not current.get("system_name")):
            neighbors[number] = neighbor
    return neighbors


def _arp(walks: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for suffix, value in (walks.get(IP_NET_TO_MEDIA_PHYS) or {}).items():
        parts = suffix.split(".")
        if len(parts) < 5:
            continue
        ip_address = ".".join(parts[-4:])
        mac = mac_from_value(value)
        if mac and all(part.isdigit() and int(part) <= 255 for part in parts[-4:]):
            entries[ip_address] = {"ip_address": ip_address, "mac_address": mac, "if_index": parts[0], "source": "router_arp"}
    for suffix, value in (walks.get(IP_NET_TO_PHYSICAL_PHYS) or {}).items():
        parts = suffix.split(".")
        # ifIndex.addrType(1=ipv4).addrLen(4).a.b.c.d
        if len(parts) >= 7 and parts[1] == "1" and parts[2] == "4":
            ip_address = ".".join(parts[3:7])
            mac = mac_from_value(value)
            if mac:
                entries.setdefault(ip_address, {"ip_address": ip_address, "mac_address": mac, "if_index": parts[0], "source": "router_arp"})
    return sorted(entries.values(), key=lambda entry: tuple(int(part) for part in entry["ip_address"].split(".")))


def _stp(gets: dict[str, str], port_numbers: dict[str, int], bridge_map: dict[str, str]) -> dict[str, Any]:
    root = gets.get(DOT1D_STP_DESIGNATED_ROOT)
    if not root or _split_type(root)[0] == "NONE":
        return {"supported": False}
    root_bytes = hex_bytes(root)
    root_mac = ":".join(f"{part:02x}" for part in root_bytes[-6:]) if len(root_bytes) >= 6 else None
    root_priority = (root_bytes[0] << 8 | root_bytes[1]) if len(root_bytes) >= 8 else None
    bridge_mac = mac_from_value(gets.get(DOT1D_BASE_BRIDGE_ADDRESS))
    root_port = as_int(gets.get(DOT1D_STP_ROOT_PORT))
    root_port_number = None
    if root_port:
        _index, root_port_number = _resolve_bridge_port(str(root_port), bridge_map, port_numbers)
    return {
        "supported": True,
        "bridge_mac": bridge_mac,
        "priority": as_int(gets.get(DOT1D_STP_PRIORITY)),
        "root_mac": root_mac,
        "root_priority": root_priority,
        "root_cost": as_int(gets.get(DOT1D_STP_ROOT_COST)),
        "root_port": root_port_number,
        "is_root": bool(bridge_mac and root_mac and bridge_mac == root_mac) or (root_port == 0 and root_mac == bridge_mac),
    }


def _dell_qos(walks: dict[str, dict[str, str]], port_numbers: dict[str, int]) -> dict[str, Any]:
    dscp_rows = walks.get(DELL_COS_DSCP_MAP) or {}
    trust_rows = walks.get(DELL_COS_TRUST_MODE) or {}
    if not dscp_rows and not trust_rows:
        return {"supported": False}
    dscp_map: dict[int, int] = {}
    for suffix, value in dscp_rows.items():
        parts = suffix.split(".")
        if len(parts) != 2 or parts[0] != "0":
            continue
        queue = as_int(value)
        if parts[1].isdigit() and queue is not None:
            dscp_map[int(parts[1])] = queue
    trust_names = {1: "untrusted", 2: "dot1p", 3: "ip-precedence", 4: "dscp"}
    trust: dict[int, str] = {}
    for if_index, value in trust_rows.items():
        number = port_numbers.get(if_index)
        mode = as_int(value)
        if number is not None and mode is not None:
            trust[number] = trust_names.get(mode, str(mode))
    return {"supported": True, "dscp_map": dscp_map, "trust_mode": trust}


# --------------------------------------------------------------------------- device poll

_STATIC_IF_COLUMNS = [IF_NAME, IF_DESCR, IF_TYPE, IF_ALIAS, IF_PHYS_ADDRESS, IF_SPEED]
_DYNAMIC_IF_COLUMNS = [IF_ADMIN_STATUS, IF_OPER_STATUS, IF_HIGH_SPEED, IF_LAST_CHANGE, IF_HC_IN_OCTETS, IF_HC_OUT_OCTETS, IF_IN_ERRORS, IF_OUT_ERRORS, IF_IN_DISCARDS, IF_OUT_DISCARDS]
_ROUTER_PORT = re.compile(r"^(eth|lan|wan|wlan-\d+|ge|gi|port)\b", re.I)


async def _safe_columns(ip_address: str, settings: dict[str, Any], oids: list[str], errors: list[str], repetitions: int = 25) -> dict[str, dict[str, str]]:
    try:
        return await bulk_walk_columns(ip_address, settings, oids, repetitions=repetitions)
    except SnmpError as exc:
        errors.append(f"{oids[0]}: {exc}")
        return {oid: {} for oid in oids}


def _static_interfaces(walks: dict[str, dict[str, str]], kind: str, profile: str) -> dict[str, dict[str, Any]]:
    names = walks.get(IF_NAME) or {}
    descrs = walks.get(IF_DESCR) or {}
    interfaces: dict[str, dict[str, Any]] = {}
    for index in sorted(set(names) | set(descrs), key=lambda value: int(value) if value.isdigit() else 0):
        if not index.isdigit():
            continue
        name = clean_string(names.get(index)) or clean_string(descrs.get(index)) or f"if{index}"
        descr = clean_string(descrs.get(index))
        if_type = as_int((walks.get(IF_TYPE) or {}).get(index))
        if not _is_physical(name, descr, if_type):
            continue
        if kind == "router" and not _ROUTER_PORT.match(name):
            continue
        speed = as_int((walks.get(IF_SPEED) or {}).get(index))
        interfaces[index] = {
            "if_index": index,
            "name": name,
            "descr": descr,
            "alias": clean_string((walks.get(IF_ALIAS) or {}).get(index)),
            "if_type": if_type,
            "mac_address": mac_from_value((walks.get(IF_PHYS_ADDRESS) or {}).get(index)),
            "nominal_speed_mbps": int(speed / 1_000_000) if speed else None,
        }
    return interfaces


def _apply_dynamic(interfaces: dict[str, dict[str, Any]], values: dict[str, dict[str, str]]) -> None:
    for index, interface in interfaces.items():
        high_speed = as_int((values.get(IF_HIGH_SPEED) or {}).get(index))
        in_octets = as_int((values.get(IF_HC_IN_OCTETS) or {}).get(index))
        out_octets = as_int((values.get(IF_HC_OUT_OCTETS) or {}).get(index))
        if in_octets is None:
            in_octets = as_int((values.get(IF_IN_OCTETS) or {}).get(index))
        if out_octets is None:
            out_octets = as_int((values.get(IF_OUT_OCTETS) or {}).get(index))
        interface.update(
            {
                "admin_status": status_name((values.get(IF_ADMIN_STATUS) or {}).get(index)),
                "oper_status": status_name((values.get(IF_OPER_STATUS) or {}).get(index)),
                "speed_mbps": high_speed if high_speed else interface.get("nominal_speed_mbps"),
                "last_change_ticks": as_int((values.get(IF_LAST_CHANGE) or {}).get(index)),
                "in_octets": in_octets,
                "out_octets": out_octets,
                "in_errors": as_int((values.get(IF_IN_ERRORS) or {}).get(index)),
                "out_errors": as_int((values.get(IF_OUT_ERRORS) or {}).get(index)),
                "in_discards": as_int((values.get(IF_IN_DISCARDS) or {}).get(index)),
                "out_discards": as_int((values.get(IF_OUT_DISCARDS) or {}).get(index)),
            }
        )


async def _dynamic_columns(ip_address: str, settings: dict[str, Any], indexes: list[str], errors: list[str]) -> dict[str, dict[str, str]]:
    """Fetch the dynamic interface columns with batched GETs for the known ifIndexes."""
    oids = [f"{column}.{index}" for column in _DYNAMIC_IF_COLUMNS for index in indexes]
    try:
        flat = await snmp_get_many(ip_address, settings, oids)
    except SnmpError as exc:
        errors.append(f"interfaces: {exc}")
        return {}
    values: dict[str, dict[str, str]] = {column: {} for column in _DYNAMIC_IF_COLUMNS}
    for column in _DYNAMIC_IF_COLUMNS:
        for index in indexes:
            value = flat.get(f"{column}.{index}")
            if value is not None and _split_type(value)[0] != "NONE":
                values[column][index] = value
    oper = values.get(IF_OPER_STATUS) or {}
    if indexes and len(oper) < max(1, len(indexes) // 2):
        # Index cache is stale (agent restarted / renumbered): let the caller re-walk.
        return {}
    if not values.get(IF_HC_IN_OCTETS):
        legacy = await _safe_columns(ip_address, settings, [IF_IN_OCTETS, IF_OUT_OCTETS], errors)
        values.update(legacy)
    return values


async def poll_device(device: dict[str, Any], settings: dict[str, Any], *, include_static: bool) -> dict[str, Any]:
    """Poll one switch/router/AP. Missing OIDs degrade gracefully; only total failure raises."""
    device_id = str(device.get("id"))
    ip_address = str(device.get("ip_address") or "")
    kind = str(device.get("kind") or "switch")
    started = time.monotonic()
    errors: list[str] = []
    if not settings.get("community"):
        raise SnmpError("SNMP community is not configured.")

    gets = await snmp_get(ip_address, settings, [SYS_DESCR, SYS_OBJECT_ID, SYS_UPTIME, SYS_CONTACT, SYS_NAME, SYS_LOCATION])
    if all(_split_type(value)[0] == "NONE" for value in gets.values()) or not gets:
        raise SnmpError("Agent answered without system information.")
    sys_descr = clean_string(gets.get(SYS_DESCR))
    sys_object_id = clean_string(gets.get(SYS_OBJECT_ID))
    profile = vendor_profile(sys_descr, sys_object_id, str(device.get("vendor") or ""))
    uptime_ticks = as_int(gets.get(SYS_UPTIME))
    cache = _STATIC_CACHE.get(device_id) or {}
    # A reboot (uptime went backwards) invalidates the cached static tables.
    if cache and uptime_ticks is not None and cache.get("uptime_ticks") is not None and uptime_ticks < cache["uptime_ticks"]:
        cache = {}
    include_static = include_static or not cache

    walks: dict[str, dict[str, str]] = {}
    if include_static:
        walks.update(await _safe_columns(ip_address, settings, _STATIC_IF_COLUMNS, errors, repetitions=40))
        interfaces = _static_interfaces(walks, kind, profile)
        port_numbers = physical_port_numbers(interfaces)
    else:
        interfaces = {index: dict(item) for index, item in (cache.get("interfaces") or {}).items()}
        port_numbers = dict(cache.get("port_numbers") or {})

    dynamic = await _dynamic_columns(ip_address, settings, list(interfaces), errors) if interfaces else {}
    if interfaces and not dynamic:
        # GET path failed: fall back to column walks (and refresh the static view).
        dynamic = await _safe_columns(ip_address, settings, _DYNAMIC_IF_COLUMNS, errors, repetitions=40)
        if not walks.get(IF_NAME):
            walks.update(await _safe_columns(ip_address, settings, _STATIC_IF_COLUMNS, errors, repetitions=40))
            interfaces = _static_interfaces(walks, kind, profile)
            port_numbers = physical_port_numbers(interfaces)
            include_static = True
        if not dynamic.get(IF_HC_IN_OCTETS):
            dynamic.update(await _safe_columns(ip_address, settings, [IF_IN_OCTETS, IF_OUT_OCTETS], errors))
    _apply_dynamic(interfaces, dynamic)
    _attach_rates(device_id, interfaces)
    for index, interface in interfaces.items():
        interface["port"] = port_numbers.get(index)

    result: dict[str, Any] = {
        "id": device_id,
        "ip_address": ip_address,
        "kind": kind,
        "status": "ok",
        "error": None,
        "errors": errors,
        "last_poll": utc_now(),
        "vendor_profile": profile,
        "sys": {
            "descr": sys_descr,
            "object_id": sys_object_id,
            "name": clean_string(gets.get(SYS_NAME)),
            "location": clean_string(gets.get(SYS_LOCATION)),
            "contact": clean_string(gets.get(SYS_CONTACT)),
            "uptime_seconds": uptime_ticks // 100 if uptime_ticks is not None else None,
        },
        "interfaces": interfaces,
        "port_numbers": port_numbers,
        "fdb": [],
        "vlans": {},
        "pvids": {},
        "lldp": {},
        "arp": [],
        "stp": {"supported": False},
        "qos": {"supported": False},
        "static_polled": include_static,
    }
    result["sys"]["uptime"] = uptime_text(result["sys"]["uptime_seconds"])

    bridge_map: dict[str, str] = dict(cache.get("bridge_map") or {})
    if kind in {"switch", "ap"}:
        if include_static or not bridge_map:
            walks[DOT1D_BASE_PORT_IF_INDEX] = await _safe_walk(ip_address, settings, DOT1D_BASE_PORT_IF_INDEX, errors)
            bridge_map = _bridge_port_map(walks)
        walks[DOT1Q_TP_FDB_PORT] = await _safe_walk(ip_address, settings, DOT1Q_TP_FDB_PORT, errors)
        if not walks[DOT1Q_TP_FDB_PORT]:
            walks[DOT1D_TP_FDB_PORT] = await _safe_walk(ip_address, settings, DOT1D_TP_FDB_PORT, errors)
        result["fdb"] = _fdb(walks, bridge_map, port_numbers)

        if include_static:
            vlans: dict[int, dict[str, Any]] = {}
            if profile == "tplink":
                walks.update(await _safe_columns(ip_address, settings, [TPLINK_VLAN_ID, TPLINK_VLAN_NAME, TPLINK_VLAN_TAGGED, TPLINK_VLAN_UNTAGGED], errors, repetitions=10))
                walks[TPLINK_PORT_PVID] = await _safe_walk(ip_address, settings, TPLINK_PORT_PVID, errors, repetitions=30)
                vlans = _tplink_vlans(walks)
            if not vlans:
                walks.update(await _safe_columns(ip_address, settings, [DOT1Q_VLAN_STATIC_NAME, DOT1Q_VLAN_STATIC_EGRESS, DOT1Q_VLAN_STATIC_UNTAGGED], errors, repetitions=10))
                walks[DOT1Q_PVID] = await _safe_walk(ip_address, settings, DOT1Q_PVID, errors, repetitions=40)
                vlans = _q_bridge_vlans(walks, bridge_map, port_numbers)
            result["vlans"] = vlans
            result["pvids"] = _pvids(walks, bridge_map, port_numbers, profile)
            try:
                stp_gets = await snmp_get(ip_address, settings, [DOT1D_BASE_BRIDGE_ADDRESS, DOT1D_STP_PRIORITY, DOT1D_STP_DESIGNATED_ROOT, DOT1D_STP_ROOT_COST, DOT1D_STP_ROOT_PORT])
            except SnmpError as exc:
                stp_gets = {}
                errors.append(f"stp: {exc}")
            result["stp"] = _stp(stp_gets, port_numbers, bridge_map)
            if profile == "dell":
                walks.update(await _safe_columns(ip_address, settings, [DELL_COS_DSCP_MAP, DELL_COS_TRUST_MODE], errors, repetitions=40))
                result["qos"] = _dell_qos(walks, port_numbers)
            walks[LLDP_LOC_PORT_ID] = await _safe_walk(ip_address, settings, LLDP_LOC_PORT_ID, errors, repetitions=40)
        else:
            result["vlans"] = cache.get("vlans") or {}
            result["pvids"] = cache.get("pvids") or {}
            result["stp"] = cache.get("stp") or {"supported": False}
            result["qos"] = cache.get("qos") or {"supported": False}
            walks[LLDP_LOC_PORT_ID] = cache.get("lldp_local") or {}
    elif include_static:
        walks[LLDP_LOC_PORT_ID] = await _safe_walk(ip_address, settings, LLDP_LOC_PORT_ID, errors, repetitions=40)
    else:
        walks[LLDP_LOC_PORT_ID] = cache.get("lldp_local") or {}

    walks[LLDP_REM_TABLE] = await _safe_walk(ip_address, settings, LLDP_REM_TABLE, errors, repetitions=20)
    result["lldp"] = _lldp_neighbors(walks, interfaces, port_numbers)

    if kind == "router":
        walks[IP_NET_TO_MEDIA_PHYS] = await _safe_walk(ip_address, settings, IP_NET_TO_MEDIA_PHYS, errors, repetitions=40)
        if not walks[IP_NET_TO_MEDIA_PHYS]:
            walks[IP_NET_TO_PHYSICAL_PHYS] = await _safe_walk(ip_address, settings, IP_NET_TO_PHYSICAL_PHYS, errors, repetitions=40)
        result["arp"] = _arp(walks)

    if include_static:
        _STATIC_CACHE[device_id] = {
            "interfaces": {index: {key: value for key, value in item.items() if key in {"if_index", "name", "descr", "alias", "if_type", "mac_address", "nominal_speed_mbps"}} for index, item in interfaces.items()},
            "port_numbers": port_numbers,
            "bridge_map": bridge_map,
            "vlans": result["vlans"],
            "pvids": result["pvids"],
            "stp": result["stp"],
            "qos": result["qos"],
            "lldp_local": walks.get(LLDP_LOC_PORT_ID) or {},
            "uptime_ticks": uptime_ticks,
            "polled_at": utc_now(),
        }
    result["static_polled_at"] = (_STATIC_CACHE.get(device_id) or {}).get("polled_at")
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


def forget_static(device_id: str | None = None) -> None:
    """Drop cached static tables (after settings changes)."""
    if device_id is None:
        _STATIC_CACHE.clear()
    else:
        _STATIC_CACHE.pop(device_id, None)


async def poll_devices(devices: list[dict[str, Any]], config: dict[str, Any], *, include_static: bool, only_ids: set[str] | None = None) -> dict[str, Any]:
    """Poll many devices with bounded parallelism. Returns ``{id: result|error}``."""
    settings = snmp_settings(config)
    if not settings["enabled"]:
        return {"enabled": False, "status": "disabled", "devices": {}, "errors": []}
    if not tools_available():
        return {"enabled": True, "status": "missing_tools", "devices": {}, "errors": ["net-snmp tools (snmpget/snmpbulkwalk) are not installed."]}
    semaphore = asyncio.Semaphore(settings["max_parallel_hosts"])
    targets = [device for device in devices if device.get("ip_address") and device.get("snmp_enabled", True) and device.get("enabled", True) and (only_ids is None or str(device.get("id")) in only_ids)]

    async def guarded(device: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            try:
                return str(device["id"]), await poll_device(device, device_snmp_settings(device, settings), include_static=include_static)
            except SnmpError as exc:
                return str(device["id"]), {"id": device["id"], "ip_address": device.get("ip_address"), "kind": device.get("kind"), "status": "error", "error": str(exc), "last_poll": utc_now()}
            except Exception as exc:  # pragma: no cover - defensive
                return str(device["id"]), {"id": device["id"], "ip_address": device.get("ip_address"), "kind": device.get("kind"), "status": "error", "error": f"{type(exc).__name__}: {exc}", "last_poll": utc_now()}

    results = dict(await asyncio.gather(*(guarded(device) for device in targets)))
    ok = [result for result in results.values() if result.get("status") == "ok"]
    status = "ok" if len(ok) == len(results) else ("partial" if ok else ("unavailable" if results else "idle"))
    return {"enabled": True, "status": status, "devices": results, "errors": [f"{key}: {value.get('error')}" for key, value in results.items() if value.get("status") != "ok"]}


async def test_snmp_target(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    ip_address = str(payload.get("ip_address") or payload.get("ip") or "").strip()
    if not ip_address:
        raise SnmpError("IP address is required.")
    settings = device_snmp_settings({"snmp": {"version": payload.get("version"), "community": payload.get("community")}}, snmp_settings(config))
    if not settings.get("community"):
        raise SnmpError("SNMP community is required.")
    if not tools_available():
        raise SnmpError("net-snmp tools are not installed.")
    started = time.monotonic()
    gets = await snmp_get(ip_address, settings, [SYS_DESCR, SYS_OBJECT_ID, SYS_NAME, SYS_LOCATION, SYS_UPTIME])
    return {
        "status": "ok",
        "ip_address": ip_address,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "sys_descr": clean_string(gets.get(SYS_DESCR)) or UNKNOWN,
        "sys_name": clean_string(gets.get(SYS_NAME)) or UNKNOWN,
        "sys_location": clean_string(gets.get(SYS_LOCATION)) or UNKNOWN,
        "uptime": uptime_text((as_int(gets.get(SYS_UPTIME)) or 0) // 100),
        "vendor_profile": vendor_profile(clean_string(gets.get(SYS_DESCR)), clean_string(gets.get(SYS_OBJECT_ID))),
    }
