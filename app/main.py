from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import ConfigError
from .status_service import build_status


STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="ABOUTUS Network Monitor",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
async def api_status() -> dict:
    try:
        return await build_status()
    except ConfigError as exc:
        return {
            "generated_at": None,
            "station": {},
            "infrastructure": [],
            "internet": {"status": "unknown", "probes": []},
            "vlans": [],
            "devices": [],
            "error": str(exc),
        }
