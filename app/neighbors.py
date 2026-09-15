"""Local Linux neighbour (ARP) table and interface addresses of the Pi."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .checks import utc_now


_MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")


def normalize_mac(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("-", ":").replace(".", "")
    if re.fullmatch(r"[0-9a-f]{12}", text):
        text = ":".join(text[index : index + 2] for index in range(0, 12, 2))
    if not _MAC.match(text) or text in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}:
        return None
    return text


async def _run(command: list[str], timeout: float = 3.0) -> str:
    try:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, OSError):
        return ""
    if process.returncode != 0:
        return ""
    return stdout.decode(errors="replace")


async def local_neighbors() -> dict[str, dict[str, Any]]:
    """Return ``{ip: {mac, state, source}}`` from ``ip neigh`` plus the Pi's own addresses."""
    neighbors: dict[str, dict[str, Any]] = {}
    output = await _run(["ip", "-4", "neigh", "show"])
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or "lladdr" not in parts:
            continue
        mac = normalize_mac(parts[parts.index("lladdr") + 1]) if parts.index("lladdr") + 1 < len(parts) else None
        state = parts[-1].upper()
        if not mac or state in {"FAILED", "INCOMPLETE"}:
            continue
        neighbors[parts[0]] = {"ip_address": parts[0], "mac_address": mac, "state": state, "source": "linux_neigh", "seen_at": utc_now(), "current": state in {"REACHABLE", "DELAY", "PROBE"}}
    output = await _run(["ip", "-j", "-4", "addr", "show"])
    if output:
        try:
            for interface in json.loads(output):
                mac = normalize_mac(interface.get("address"))
                if not mac:
                    continue
                for address in interface.get("addr_info") or []:
                    if address.get("family") == "inet" and address.get("local"):
                        neighbors[str(address["local"])] = {"ip_address": str(address["local"]), "mac_address": mac, "state": "LOCAL", "source": "local_interface", "seen_at": utc_now(), "current": True}
        except json.JSONDecodeError:
            pass
    return neighbors
