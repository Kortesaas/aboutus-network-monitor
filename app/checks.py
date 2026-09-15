"""Reachability probes: batched fping, single ping, TCP and HTTP checks.

All probes are read-only and bounded by timeouts. ``fping`` is preferred for
batches because one process can probe every infrastructure address in well
under a second; the per-host ``ping`` fallback is used when fping is missing.
"""

from __future__ import annotations

import asyncio
import math
import platform
import re
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


ONLINE = "online"
OFFLINE = "offline"
UNKNOWN = "unknown"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _result(
    *,
    status: str,
    source: str,
    target: str | None = None,
    latency_ms: float | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "target": target,
        "latency_ms": latency_ms,
        "message": message,
        "checked_at": utc_now(),
    }


def unknown(source: str, message: str, target: str | None = None) -> dict[str, Any]:
    return _result(status=UNKNOWN, source=source, target=target, message=message)


async def _run(command: list[str], timeout_seconds: float) -> tuple[int | None, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.communicate()
        return None, "", "timeout"
    return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


# --------------------------------------------------------------------------- fping batch

_FPING_LINE = re.compile(
    r"^(?P<target>\S+)\s*:\s*xmt/rcv/%loss\s*=\s*(?P<xmt>\d+)/(?P<rcv>\d+)/(?P<loss>\d+)%"
    r"(?:,\s*min/avg/max\s*=\s*(?P<min>[\d.]+)/(?P<avg>[\d.]+)/(?P<max>[\d.]+))?"
)


async def fping_batch(targets: list[str], timeout_seconds: float = 1.0, count: int = 1) -> dict[str, dict[str, Any]] | None:
    """Probe many targets with one fping process. Returns None when fping is unavailable."""
    fping = shutil.which("fping")
    if not fping or not targets:
        return None if not fping else {}
    unique = list(dict.fromkeys(str(target) for target in targets if target))
    timeout_ms = max(100, int(timeout_seconds * 1000))
    command = [fping, "-c", str(max(1, count)), "-t", str(timeout_ms), "-q", "-i", "5", "-p", "200", *unique]
    overall_timeout = max(3.0, timeout_seconds * count + 3.0 + len(unique) * 0.05)
    returncode, stdout, stderr = await _run(command, overall_timeout)
    if returncode is None:
        return {target: _result(status=UNKNOWN, source="fping", target=target, message="fping timed out.") for target in unique}
    results: dict[str, dict[str, Any]] = {}
    for line in (stderr + "\n" + stdout).splitlines():
        match = _FPING_LINE.match(line.strip())
        if not match:
            continue
        target = match.group("target")
        received = int(match.group("rcv"))
        latency = float(match.group("avg")) if match.group("avg") else None
        if received > 0:
            results[target] = _result(status=ONLINE, source="fping", target=target, latency_ms=latency, message="Reachable.")
        else:
            results[target] = _result(status=OFFLINE, source="fping", target=target, message="No ICMP reply.")
    for target in unique:
        results.setdefault(target, _result(status=OFFLINE, source="fping", target=target, message="No ICMP reply."))
    return results


# --------------------------------------------------------------------------- single ping


def _ping_command(target: str, timeout_seconds: float, count: int) -> list[str] | None:
    ping_path = shutil.which("ping")
    if not ping_path:
        return None
    if platform.system().lower() == "windows":
        return [ping_path, "-n", str(count), "-w", str(max(1, int(timeout_seconds * 1000))), target]
    return [ping_path, "-c", str(count), "-W", str(max(1, math.ceil(timeout_seconds))), target]


async def ping_check(target: str | None, timeout_seconds: float = 1.0, count: int = 1) -> dict[str, Any]:
    if not target:
        return unknown("ping", "No ping target configured.")
    command = _ping_command(target, timeout_seconds, count)
    if not command:
        return unknown("ping", "The ping command is not available.", target)
    start = time.monotonic()
    returncode, stdout, _stderr = await _run(command, max(1.0, timeout_seconds * count + 0.5))
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    if returncode == 0:
        match = re.search(r"time[=<]([\d.]+)\s*ms", stdout)
        if match:
            latency_ms = float(match.group(1))
        return _result(status=ONLINE, source="ping", target=target, latency_ms=latency_ms, message="Reachable.")
    return _result(status=OFFLINE, source="ping", target=target, message="No ping response.")


async def ping_many(targets: list[str], timeout_seconds: float = 1.0, count: int = 1) -> dict[str, dict[str, Any]]:
    """Probe targets with fping when available, otherwise with bounded parallel ping."""
    batch = await fping_batch(targets, timeout_seconds, count)
    if batch is not None:
        return batch
    semaphore = asyncio.Semaphore(8)

    async def guarded(target: str) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            return target, await ping_check(target, timeout_seconds, count)

    unique = list(dict.fromkeys(str(target) for target in targets if target))
    return dict(await asyncio.gather(*(guarded(target) for target in unique)))


# --------------------------------------------------------------------------- tcp / http


async def tcp_check(host: str | None, port: int | None, timeout_seconds: float = 1.5) -> dict[str, Any]:
    target = f"{host}:{port}" if host and port else host
    if not host or not port:
        return unknown("tcp", "TCP host and port must be configured.", target)
    start = time.monotonic()
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_seconds)
        writer.close()
        await writer.wait_closed()
    except (OSError, asyncio.TimeoutError) as exc:
        return _result(status=OFFLINE, source="tcp", target=target, message=str(exc) or "Connection failed.")
    return _result(status=ONLINE, source="tcp", target=target, latency_ms=round((time.monotonic() - start) * 1000, 1), message="Connection accepted.")


def _http_probe(url: str, timeout_seconds: float) -> tuple[int | None, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "ABOUTUS-Network-Monitor/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, None
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc)
    except urllib.error.URLError as exc:
        return None, str(exc.reason)
    except (TimeoutError, OSError) as exc:
        return None, str(exc) or "Request timed out."


async def http_check(url: str | None, timeout_seconds: float = 2.0) -> dict[str, Any]:
    if not url:
        return unknown("http", "No HTTP target configured.")
    start = time.monotonic()
    status_code, error = await asyncio.to_thread(_http_probe, url, timeout_seconds)
    latency_ms = round((time.monotonic() - start) * 1000, 1)
    if status_code is not None and 200 <= status_code < 400:
        return _result(status=ONLINE, source="http", target=url, latency_ms=latency_ms, message=f"HTTP {status_code}.")
    if status_code is not None:
        return _result(status=OFFLINE, source="http", target=url, latency_ms=latency_ms, message=f"HTTP {status_code}.")
    return _result(status=OFFLINE, source="http", target=url, latency_ms=latency_ms, message=error or "HTTP probe failed.")


async def run_check(
    check: dict[str, Any] | None,
    *,
    default_target: str | None = None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    defaults = defaults or {}
    prepared = dict(check or {})
    check_type = str(prepared.get("type") or "ping").lower()
    timeout_seconds = float(prepared.get("timeout_seconds") or defaults.get("timeout_seconds") or 1.0)
    if check_type == "ping":
        target = prepared.get("target") or default_target
        count = int(prepared.get("count") or defaults.get("ping_count") or 1)
        return await ping_check(target, timeout_seconds, count)
    if check_type == "tcp":
        host = prepared.get("host") or prepared.get("target") or default_target
        port = prepared.get("port")
        return await tcp_check(host, int(port) if port else None, timeout_seconds)
    if check_type in {"http", "https"}:
        return await http_check(prepared.get("target") or default_target, max(timeout_seconds, 2.0))
    if check_type in {"manual", "none", "disabled"}:
        return unknown(check_type, "No live status check configured.", default_target)
    return unknown(check_type, f"Unsupported check type: {check_type}", default_target)
