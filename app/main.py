from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import ConfigError
from .status_service import build_devices, build_history, build_status, build_topology


STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="ABOUTUS Network Monitor",
    version="0.6.0",
    docs_url="/api/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.head("/", include_in_schema=False)
async def dashboard_head() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(
        content=(STATIC_DIR / "favicon.svg").read_text(encoding="utf-8"),
        media_type="image/svg+xml",
    )


@app.head("/favicon.ico", include_in_schema=False)
async def favicon_head() -> Response:
    return Response(media_type="image/svg+xml")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
async def api_status(refresh: bool = False) -> dict:
    try:
        return await build_status(force_refresh=refresh)
    except ConfigError as exc:
        return {
            "generated_at": None,
            "station": {},
            "infrastructure": [],
            "internet": {"status": "unknown", "probes": []},
            "vlans": [],
            "devices": [],
            "devices_by_vlan": [],
            "discovery": {"enabled": False, "status": "error", "errors": [str(exc)]},
            "topology": {"nodes": [], "links": [], "warnings": []},
            "error": str(exc),
        }


@app.get("/api/devices")
async def api_devices(refresh: bool = False) -> dict:
    try:
        return await build_devices(force_refresh=refresh)
    except ConfigError as exc:
        return {
            "generated_at": None,
            "devices": [],
            "devices_by_vlan": [],
            "discovery": {"enabled": False, "status": "error", "errors": [str(exc)]},
            "error": str(exc),
        }


@app.get("/api/topology")
async def api_topology(refresh: bool = False) -> dict:
    try:
        return await build_topology(force_refresh=refresh)
    except ConfigError as exc:
        return {
            "generated_at": None,
            "topology": {"nodes": [], "links": [], "warnings": []},
            "error": str(exc),
        }


@app.get("/api/history")
async def api_history(refresh: bool = False) -> dict:
    try:
        return await build_history(force_refresh=refresh)
    except ConfigError as exc:
        return {
            "generated_at": None,
            "history": {"events": [], "latest_snapshot": {}, "device_totals": {}},
            "error": str(exc),
        }
