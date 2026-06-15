from __future__ import annotations

import asyncio
import math
import platform
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
    latency_ms: int | None = None,
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


def _ping_command(target: str, timeout_seconds: float, count: int) -> list[str] | None:
    ping_path = shutil.which("ping")
    if not ping_path:
        return None

    system = platform.system().lower()
    if system == "windows":
        return [
            ping_path,
            "-n",
            str(count),
            "-w",
            str(max(1, int(timeout_seconds * 1000))),
            target,
        ]

    return [
        ping_path,
        "-c",
        str(count),
        "-W",
        str(max(1, math.ceil(timeout_seconds))),
        target,
    ]


async def ping_check(target: str | None, timeout_seconds: float = 1.5, count: int = 1) -> dict[str, Any]:
    if not target:
        return unknown("ping", "No ping target configured.")

    command = _ping_command(target, timeout_seconds, count)
    if not command:
        return unknown("ping", "The ping command is not available.", target)

    start = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(
            process.communicate(),
            timeout=max(1.0, timeout_seconds * count + 0.5),
        )
    except asyncio.TimeoutError:
        if "process" in locals():
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.communicate()
        latency_ms = round((time.monotonic() - start) * 1000)
        return _result(
            status=OFFLINE,
            source="ping",
            target=target,
            latency_ms=latency_ms,
            message="No response before timeout.",
        )

    latency_ms = round((time.monotonic() - start) * 1000)
    if process.returncode == 0:
        return _result(
            status=ONLINE,
            source="ping",
            target=target,
            latency_ms=latency_ms,
            message="Reachable.",
        )

    return _result(
        status=OFFLINE,
        source="ping",
        target=target,
        latency_ms=latency_ms,
        message="No ping response.",
    )


async def tcp_check(host: str | None, port: int | None, timeout_seconds: float = 1.5) -> dict[str, Any]:
    target = f"{host}:{port}" if host and port else host
    if not host or not port:
        return unknown("tcp", "TCP host and port must be configured.", target)

    start = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout_seconds,
        )
        writer.close()
        await writer.wait_closed()
    except OSError as exc:
        latency_ms = round((time.monotonic() - start) * 1000)
        return _result(
            status=OFFLINE,
            source="tcp",
            target=target,
            latency_ms=latency_ms,
            message=str(exc),
        )
    except asyncio.TimeoutError:
        latency_ms = round((time.monotonic() - start) * 1000)
        return _result(
            status=OFFLINE,
            source="tcp",
            target=target,
            latency_ms=latency_ms,
            message="Connection timed out.",
        )

    latency_ms = round((time.monotonic() - start) * 1000)
    return _result(
        status=ONLINE,
        source="tcp",
        target=target,
        latency_ms=latency_ms,
        message="Connection accepted.",
    )


def _http_probe(url: str, timeout_seconds: float) -> tuple[int | None, str | None]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ABOUTUS-Network-Monitor/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, None
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc)
    except urllib.error.URLError as exc:
        return None, str(exc.reason)
    except TimeoutError:
        return None, "Request timed out."


async def http_check(url: str | None, timeout_seconds: float = 2.0) -> dict[str, Any]:
    if not url:
        return unknown("http", "No HTTP target configured.")

    start = time.monotonic()
    status_code, error = await asyncio.to_thread(_http_probe, url, timeout_seconds)
    latency_ms = round((time.monotonic() - start) * 1000)

    if status_code is not None and 200 <= status_code < 400:
        return _result(
            status=ONLINE,
            source="http",
            target=url,
            latency_ms=latency_ms,
            message=f"HTTP {status_code}.",
        )

    if status_code is not None:
        return _result(
            status=OFFLINE,
            source="http",
            target=url,
            latency_ms=latency_ms,
            message=f"HTTP {status_code}.",
        )

    return _result(
        status=OFFLINE,
        source="http",
        target=url,
        latency_ms=latency_ms,
        message=error or "HTTP probe failed.",
    )


async def run_check(
    check: dict[str, Any] | None,
    *,
    default_target: str | None = None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    defaults = defaults or {}
    prepared = dict(check or {})
    check_type = str(prepared.get("type") or "ping").lower()
    timeout_seconds = float(prepared.get("timeout_seconds") or defaults.get("timeout_seconds") or 1.5)

    if check_type == "ping":
        target = prepared.get("target") or default_target
        count = int(prepared.get("count") or defaults.get("ping_count") or 1)
        return await ping_check(target, timeout_seconds, count)

    if check_type == "tcp":
        host = prepared.get("host") or prepared.get("target") or default_target
        port = prepared.get("port")
        return await tcp_check(host, int(port) if port else None, timeout_seconds)

    if check_type in {"http", "https"}:
        target = prepared.get("target") or default_target
        return await http_check(target, timeout_seconds)

    if check_type in {"manual", "none", "disabled"}:
        return unknown(check_type, "No live status check configured.", default_target)

    return unknown(check_type, f"Unsupported check type: {check_type}", default_target)
