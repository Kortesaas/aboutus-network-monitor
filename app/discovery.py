"""Read-only subnet host discovery (fping sweep or nmap ping scan).

Discovery is the only "noisy" activity of the monitor. It runs on the full
cycle schedule only, is serialised by the monitor scan lock, and is limited to
the VLAN subnets that are marked ``monitor: true`` in the configuration.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import shutil
import time
import xml.etree.ElementTree as ET
from typing import Any

from .checks import utc_now


DEFAULT_DISCOVERY = {
    "enabled": True,
    "tool": "fping",
    "timeout_seconds_per_subnet": 20,
    "host_timeout": "5s",
    "max_parallel_scans": 2,
    "resolve_dns": False,
}


def discovery_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(DEFAULT_DISCOVERY)
    configured = config.get("discovery")
    if isinstance(configured, dict):
        settings.update({key: value for key, value in configured.items() if value is not None})
    return settings


def _tool(settings: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (tool name, executable path) honouring the configured preference."""
    preferred = str(settings.get("tool") or "fping").lower()
    order = ["fping", "nmap"] if preferred == "fping" else ["nmap", "fping"]
    for name in order:
        path = shutil.which(name)
        if path:
            return name, path
    return None, None


def _host(ip_address: str, vlan: dict[str, Any], mac: str | None = None, vendor: str | None = None, hostname: str | None = None, latency: float | None = None) -> dict[str, Any]:
    return {
        "ip_address": ip_address,
        "mac_address": mac.lower() if mac else None,
        "vendor": vendor,
        "hostname": hostname,
        "vlan_id": vlan.get("id"),
        "latency_ms": latency,
        "seen_at": utc_now(),
        "source": "discovery",
    }


_FPING_ALIVE = re.compile(r"^(?P<ip>\d+\.\d+\.\d+\.\d+)\s*(?::.*?\[\d+\],\s*(?P<ms>[\d.]+)\s*ms.*)?$")


async def _fping_subnet(path: str, subnet: str, vlan: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    command = [path, "-a", "-q", "-i", "5", "-r", "1", "-t", "400", "-g", subnet]
    try:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        return {"subnet": subnet, "vlan_id": vlan.get("id"), "status": "timeout", "hosts": [], "error": f"fping exceeded {timeout_seconds:g}s", "duration_ms": round((time.monotonic() - started) * 1000)}
    except OSError as exc:
        return {"subnet": subnet, "vlan_id": vlan.get("id"), "status": "error", "hosts": [], "error": str(exc), "duration_ms": round((time.monotonic() - started) * 1000)}
    hosts: list[dict[str, Any]] = []
    for line in stdout.decode(errors="replace").splitlines():
        match = _FPING_ALIVE.match(line.strip())
        if match:
            hosts.append(_host(match.group("ip"), vlan))
    error = None
    text = stderr.decode(errors="replace").strip()
    # fping prints per-host ICMP errors on stderr (e.g. "ICMP Host Unreachable"); ignore those.
    if process.returncode not in (0, 1) and text and "ICMP" not in text:
        error = text.splitlines()[-1]
    return {"subnet": subnet, "vlan_id": vlan.get("id"), "status": "ok" if not error else "error", "hosts": hosts, "error": error, "duration_ms": round((time.monotonic() - started) * 1000)}


async def _nmap_subnet(path: str, subnet: str, vlan: dict[str, Any], settings: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    command = [path, "-sn", "-oX", "-"]
    if not bool(settings.get("resolve_dns", False)):
        command.append("-n")
    command.extend(["--max-retries", "1", "--host-timeout", str(settings.get("host_timeout", "5s")), subnet])
    try:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        return {"subnet": subnet, "vlan_id": vlan.get("id"), "status": "timeout", "hosts": [], "error": f"nmap exceeded {timeout_seconds:g}s", "duration_ms": round((time.monotonic() - started) * 1000)}
    except OSError as exc:
        return {"subnet": subnet, "vlan_id": vlan.get("id"), "status": "error", "hosts": [], "error": str(exc), "duration_ms": round((time.monotonic() - started) * 1000)}
    hosts: list[dict[str, Any]] = []
    error = None
    if process.returncode == 0:
        try:
            root = ET.fromstring(stdout.decode(errors="replace"))
            for host in root.findall("host"):
                status = host.find("status")
                if status is not None and status.get("state") != "up":
                    continue
                ip_address = mac = vendor = hostname = None
                for address in host.findall("address"):
                    if address.get("addrtype") == "ipv4":
                        ip_address = address.get("addr")
                    elif address.get("addrtype") == "mac":
                        mac = address.get("addr")
                        vendor = address.get("vendor")
                names = host.find("hostnames")
                if names is not None and names.find("hostname") is not None:
                    hostname = names.find("hostname").get("name")
                if ip_address:
                    hosts.append(_host(ip_address, vlan, mac, vendor, hostname))
        except ET.ParseError as exc:
            error = f"Unable to parse nmap XML: {exc}"
    else:
        error = stderr.decode(errors="replace").strip().splitlines()[-1:] or ["nmap failed"]
        error = error[0]
    return {"subnet": subnet, "vlan_id": vlan.get("id"), "status": "ok" if not error else "error", "hosts": hosts, "error": error, "duration_ms": round((time.monotonic() - started) * 1000)}


async def discover_subnets(config: dict[str, Any], vlans: list[dict[str, Any]], only_vlan_ids: set[int] | None = None) -> dict[str, Any]:
    """Sweep the monitored VLAN subnets. ``only_vlan_ids`` limits a targeted sweep."""
    settings = discovery_settings(config)
    started_at = utc_now()
    base = {"enabled": bool(settings.get("enabled", True)), "started_at": started_at, "subnets": [], "hosts": [], "errors": []}
    if not base["enabled"]:
        return {**base, "tool": None, "finished_at": utc_now(), "status": "disabled"}
    tool, path = _tool(settings)
    if not tool:
        return {**base, "tool": None, "finished_at": utc_now(), "status": "error", "errors": ["Neither fping nor nmap is installed."]}

    targets = []
    for vlan in vlans:
        if not vlan.get("subnet") or not vlan.get("monitor", True):
            continue
        if only_vlan_ids is not None and vlan.get("id") not in only_vlan_ids:
            continue
        try:
            ipaddress.ip_network(str(vlan["subnet"]), strict=False)
        except ValueError:
            base["errors"].append(f"Invalid subnet for VLAN {vlan.get('id')}: {vlan.get('subnet')}")
            continue
        targets.append(vlan)

    timeout_seconds = float(settings.get("timeout_seconds_per_subnet") or 20)
    semaphore = asyncio.Semaphore(max(1, int(settings.get("max_parallel_scans") or 2)))

    async def guarded(vlan: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            if tool == "fping":
                return await _fping_subnet(path, str(vlan["subnet"]), vlan, timeout_seconds)
            return await _nmap_subnet(path, str(vlan["subnet"]), vlan, settings, timeout_seconds)

    results = await asyncio.gather(*(guarded(vlan) for vlan in targets))
    hosts: list[dict[str, Any]] = []
    for result in results:
        hosts.extend(result.pop("hosts"))
        if result.get("error"):
            base["errors"].append(f"VLAN {result.get('vlan_id')} {result.get('subnet')}: {result['error']}")
    status = "ok" if not base["errors"] else ("partial" if hosts or any(r.get("status") == "ok" for r in results) else "error")
    return {**base, "tool": tool, "finished_at": utc_now(), "status": status, "subnets": results, "hosts": hosts, "host_count": len(hosts)}
