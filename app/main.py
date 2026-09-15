"""FastAPI application: static dashboard + JSON API + Server-Sent Events."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import ConfigError, load_config
from .monitor import VERSION, monitor
from .settings import settings_view, update_settings
from .snmp import SnmpError, test_snmp_target
from .storage import delete_device_metadata, delete_device_record, load_device_metadata, recent_events, save_device_metadata


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await monitor.start()
    try:
        yield
    finally:
        await monitor.stop()


app = FastAPI(title="ABOUTUS Network Monitor", version=VERSION, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _no_cache(payload: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- pages


@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(content=(STATIC_DIR / "favicon.svg").read_text(encoding="utf-8"), media_type="image/svg+xml")


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    scan = monitor.scan_status()
    return {"status": "ok", "version": VERSION, "ready": bool(monitor.state), "scan_state": scan.get("state"), "last_success_at": scan.get("last_success_at"), "last_error": scan.get("last_error")}


# --------------------------------------------------------------------------- state / events


@app.get("/api/state")
async def api_state() -> JSONResponse:
    return _no_cache(monitor.snapshot())


@app.get("/api/status")
async def api_status() -> JSONResponse:
    return _no_cache(monitor.snapshot())


def _sse(event_type: str, data: Any, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append("data: " + json.dumps(data, separators=(",", ":"), default=str))
    return "\n".join(lines) + "\n\n"


@app.get("/api/events")
async def api_events(request: Request) -> StreamingResponse:
    async def stream():
        queue = monitor.subscribe()
        try:
            yield _sse("state_snapshot", monitor.snapshot(), 0)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield _sse("heartbeat", {"scan": monitor.scan_status()})
                    continue
                yield _sse(str(event["type"]), event.get("data") or {}, int(event["id"]))
        finally:
            monitor.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.get("/api/scan/status")
async def api_scan_status() -> JSONResponse:
    return _no_cache(monitor.scan_status())


@app.post("/api/scan/request")
async def api_scan_request(mode: str = "fast") -> JSONResponse:
    return _no_cache(await monitor.request_scan(mode, source="manual"))


@app.post("/api/scan")
async def api_scan_legacy(mode: str = "fast") -> JSONResponse:
    return _no_cache(await monitor.request_scan(mode, source="manual"))


# --------------------------------------------------------------------------- sections


@app.get("/api/devices")
async def api_devices() -> JSONResponse:
    state = monitor.snapshot()
    return _no_cache({"generated_at": state.get("generated_at"), "devices": state.get("devices") or [], "device_observations": state.get("device_observations") or {"mac_only": []}, "device_counts": state.get("device_counts") or {}, "scan": state.get("scan")})


@app.get("/api/switches")
async def api_switches() -> JSONResponse:
    state = monitor.snapshot()
    return _no_cache({"generated_at": state.get("generated_at"), "switches": state.get("switches") or [], "snmp": state.get("snmp") or {}, "scan": state.get("scan")})


@app.get("/api/switches/{switch_id}")
async def api_switch(switch_id: str) -> JSONResponse:
    state = monitor.snapshot()
    for switch in state.get("switches") or []:
        if switch.get("id") == switch_id:
            return _no_cache({"generated_at": state.get("generated_at"), "switch": switch, "scan": state.get("scan")})
    raise HTTPException(status_code=404, detail="Unknown switch")


@app.get("/api/topology")
async def api_topology() -> JSONResponse:
    state = monitor.snapshot()
    return _no_cache({"generated_at": state.get("generated_at"), "topology": state.get("topology") or {}, "scan": state.get("scan")})


@app.get("/api/problems")
async def api_problems() -> JSONResponse:
    state = monitor.snapshot()
    return _no_cache({"generated_at": state.get("generated_at"), "problems": state.get("problems") or [], "problem_counts": state.get("problem_counts") or {}, "scan": state.get("scan")})


@app.post("/api/problems/{problem_id:path}/ack")
async def api_problem_ack(problem_id: str, payload: dict[str, Any] | None = None) -> JSONResponse:
    acknowledged = bool((payload or {}).get("acknowledged", True))
    state = monitor.acknowledge(problem_id, acknowledged)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown problem")
    return _no_cache({"problem": state})


@app.get("/api/history")
async def api_history(limit: int = 100) -> JSONResponse:
    return _no_cache({"events": recent_events(max(1, min(500, limit)))})


# --------------------------------------------------------------------------- known devices


@app.get("/api/devices/metadata")
async def api_metadata() -> JSONResponse:
    indexed = load_device_metadata()
    unique = {meta["identity_key"]: meta for meta in indexed.values()}
    return _no_cache({"metadata": sorted(unique.values(), key=lambda item: item.get("display_name") or item["mac_address"])})


@app.put("/api/devices/metadata")
async def api_update_metadata(payload: dict[str, Any]) -> JSONResponse:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
    try:
        saved = save_device_metadata(metadata.get("mac_address") or payload.get("mac_address"), metadata)
    except ValueError as exc:
        return _no_cache({"error": str(exc)}, status_code=400)
    # Re-run a fast cycle so names/aliases apply immediately (queued if busy).
    await monitor.request_scan("fast", source="metadata")
    return _no_cache({"metadata": saved, "scan": monitor.scan_status()})


@app.delete("/api/devices/metadata/{identity_key:path}")
async def api_delete_metadata(identity_key: str) -> JSONResponse:
    removed = delete_device_metadata(identity_key)
    await monitor.request_scan("fast", source="metadata")
    return _no_cache({"removed": removed})


@app.delete("/api/devices/{device_key:path}")
async def api_forget_device(device_key: str) -> JSONResponse:
    """Forget an offline device's history (it reappears if it is seen again)."""
    delete_device_record(device_key)
    monitor.previous_devices.pop(device_key, None)
    await monitor.request_scan("fast", source="forget")
    return _no_cache({"removed": True})


# --------------------------------------------------------------------------- settings


@app.get("/api/settings")
async def api_settings() -> JSONResponse:
    try:
        return _no_cache({"settings": settings_view()})
    except ConfigError as exc:
        return _no_cache({"error": str(exc)}, status_code=500)


@app.put("/api/settings")
async def api_update_settings(payload: dict[str, Any]) -> JSONResponse:
    try:
        settings = update_settings(payload.get("settings") if isinstance(payload.get("settings"), dict) else payload)
    except (ConfigError, ValueError) as exc:
        return _no_cache({"error": str(exc)}, status_code=400)
    monitor.reload_config()
    await monitor.request_scan("fast", source="settings")
    return _no_cache({"settings": settings, "scan": monitor.scan_status()})


@app.post("/api/snmp/test")
async def api_snmp_test(payload: dict[str, Any]) -> JSONResponse:
    try:
        return _no_cache({"result": await test_snmp_target(payload, load_config())})
    except (ConfigError, SnmpError, ValueError) as exc:
        return _no_cache({"result": {"status": "error", "error": str(exc)}})
