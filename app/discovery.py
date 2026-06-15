from __future__ import annotations

import asyncio
import shutil
import time
import xml.etree.ElementTree as ET
from typing import Any

from .checks import UNKNOWN, utc_now


DEFAULT_DISCOVERY = {
    "enabled": True,
    "tool": "nmap",
    "timeout_seconds_per_subnet": 15,
    "scan_interval_seconds": 60,
    "resolve_dns": False,
    "max_retries": 1,
    "host_timeout": "5s",
    "max_parallel_scans": 2,
}


def discovery_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(DEFAULT_DISCOVERY)
    configured = config.get("discovery")
    if isinstance(configured, dict):
        settings.update({key: value for key, value in configured.items() if value is not None})
    return settings


def discovery_cache_ttl(config: dict[str, Any]) -> int:
    settings = discovery_settings(config)
    try:
        return max(5, int(settings.get("scan_interval_seconds", 60)))
    except (TypeError, ValueError):
        return 60


def _safe_nmap_command(nmap_path: str, subnet: str, settings: dict[str, Any]) -> list[str]:
    command = [nmap_path, "-sn", "-oX", "-"]

    if not bool(settings.get("resolve_dns", True)):
        command.append("-n")

    max_retries = settings.get("max_retries", 1)
    host_timeout = str(settings.get("host_timeout", "5s"))
    command.extend(["--max-retries", str(max_retries), "--host-timeout", host_timeout, subnet])
    return command


def _host_text(host: ET.Element, tag: str) -> str | None:
    value = host.find(tag)
    if value is None:
        return None
    text = value.text
    if not text:
        return None
    return text.strip() or None


def _parse_nmap_xml(xml_text: str, vlan: dict[str, Any]) -> list[dict[str, Any]]:
    if not xml_text.strip():
        return []

    root = ET.fromstring(xml_text)
    devices: list[dict[str, Any]] = []
    vlan_name = vlan.get("name")
    vlan_id = vlan.get("id")

    for host in root.findall("host"):
        status = host.find("status")
        if status is not None and status.get("state") != "up":
            continue

        ip_address = None
        mac_address = None
        vendor = None
        for address in host.findall("address"):
            address_type = address.get("addrtype")
            if address_type == "ipv4":
                ip_address = address.get("addr")
            elif address_type == "mac":
                mac_address = address.get("addr")
                vendor = address.get("vendor")

        if not ip_address:
            continue

        hostname = None
        hostnames = host.find("hostnames")
        if hostnames is not None:
            first_hostname = hostnames.find("hostname")
            if first_hostname is not None:
                hostname = first_hostname.get("name")

        devices.append(
            {
                "id": f"discovered-{ip_address}",
                "name": hostname or ip_address,
                "display_name": hostname or ip_address,
                "ip_address": ip_address,
                "mac_address": mac_address,
                "hostname": hostname,
                "vendor": vendor,
                "vlan": vlan_name,
                "vlan_id": vlan_id,
                "subnet": vlan.get("subnet"),
                "status": "online",
                "last_seen": utc_now(),
                "discovery_sources": ["nmap"],
                "connected_switch": UNKNOWN.title(),
                "connected_port": UNKNOWN.title(),
                "mac_source": "nmap" if mac_address else UNKNOWN.title(),
                "switch_port_confidence": UNKNOWN.title(),
                "role": "discovered",
                "expected": False,
            }
        )

    return devices


async def _scan_subnet(
    *,
    subnet: str,
    vlan: dict[str, Any],
    settings: dict[str, Any],
    nmap_path: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    timeout_seconds = float(settings.get("timeout_seconds_per_subnet", 8))
    command = _safe_nmap_command(nmap_path, subnet, settings)
    async with semaphore:
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            if "process" in locals():
                process.kill()
                await process.communicate()
            return {
                "subnet": subnet,
                "vlan": vlan.get("name"),
                "status": "timeout",
                "device_count": 0,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "error": f"nmap scan exceeded {timeout_seconds:g}s timeout.",
                "devices": [],
            }
        except OSError as exc:
            return {
                "subnet": subnet,
                "vlan": vlan.get("name"),
                "status": "error",
                "device_count": 0,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "error": str(exc),
                "devices": [],
            }

    error_text = stderr.decode("utf-8", errors="replace").strip()
    xml_text = stdout.decode("utf-8", errors="replace")
    devices: list[dict[str, Any]] = []
    parse_error = None
    if process.returncode == 0:
        try:
            devices = _parse_nmap_xml(xml_text, vlan)
        except ET.ParseError as exc:
            parse_error = f"Unable to parse nmap XML: {exc}"

    status = "ok" if process.returncode == 0 and not parse_error else "error"
    return {
        "subnet": subnet,
        "vlan": vlan.get("name"),
        "status": status,
        "device_count": len(devices),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "error": parse_error or error_text or None,
        "devices": devices,
    }


async def discover_devices(config: dict[str, Any]) -> dict[str, Any]:
    settings = discovery_settings(config)
    started_at = utc_now()

    if not bool(settings.get("enabled", True)):
        return {
            "enabled": False,
            "tool": settings.get("tool", "nmap"),
            "started_at": started_at,
            "finished_at": utc_now(),
            "status": "disabled",
            "errors": [],
            "subnets": [],
            "devices": [],
        }

    tool = str(settings.get("tool", "nmap")).lower()
    if tool != "nmap":
        return {
            "enabled": True,
            "tool": tool,
            "started_at": started_at,
            "finished_at": utc_now(),
            "status": "error",
            "errors": [f"Unsupported discovery tool: {tool}"],
            "subnets": [],
            "devices": [],
        }

    nmap_path = shutil.which("nmap")
    if not nmap_path:
        return {
            "enabled": True,
            "tool": "nmap",
            "started_at": started_at,
            "finished_at": utc_now(),
            "status": "error",
            "errors": ["nmap command is not installed or not in PATH."],
            "subnets": [],
            "devices": [],
        }

    vlans = [vlan for vlan in config.get("vlans", []) if vlan.get("subnet")]
    max_parallel = max(1, int(settings.get("max_parallel_scans", 2)))
    semaphore = asyncio.Semaphore(max_parallel)
    results = await asyncio.gather(
        *(
            _scan_subnet(
                subnet=str(vlan["subnet"]),
                vlan=vlan,
                settings=settings,
                nmap_path=nmap_path,
                semaphore=semaphore,
            )
            for vlan in vlans
        )
    )

    devices: list[dict[str, Any]] = []
    errors: list[str] = []
    for result in results:
        devices.extend(result.pop("devices"))
        if result.get("error"):
            errors.append(f"{result.get('vlan') or result.get('subnet')}: {result['error']}")

    return {
        "enabled": True,
        "tool": "nmap",
        "started_at": started_at,
        "finished_at": utc_now(),
        "status": "ok" if not errors else "partial",
        "errors": errors,
        "subnets": results,
        "devices": devices,
    }
