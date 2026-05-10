from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import configured_aprs_callsign, load_config, resolve_path
from .db import ensure_configured_sources, fetch_all, fetch_aprs_status, fetch_insights, fetch_records, init_db, insert_event
from .models import EventIn
from .system_stats import memory_stats_from_values, read_meminfo_memory


app = FastAPI(title="RFLens")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def startup() -> None:
    init_db()
    ensure_configured_sources()


app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui")


@app.get("/api/health")
def health() -> dict[str, object]:
    cfg = load_config()
    return {
        "ok": True,
        "station": cfg.get("station", {}),
        "server": cfg.get("server", {}),
    }


@app.get("/api/station")
def station() -> dict[str, object]:
    cfg = load_config()
    station_cfg = cfg.get("station", {}) or {}
    return {
        "name": station_cfg.get("name"),
        "callsign": station_cfg.get("callsign"),
        "aprs_callsign": station_cfg.get("aprs_callsign") or configured_aprs_callsign(cfg),
        "lat": station_cfg.get("lat"),
        "lon": station_cfg.get("lon"),
        "grid": station_cfg.get("grid"),
    }


@app.get("/api/adsb/ui")
def adsb_ui() -> dict[str, object]:
    cfg = load_config()
    ui_cfg = cfg.get("adsb_ui", {}) or {}
    return {
        "enabled": bool(ui_cfg.get("enabled")),
        "url": ui_cfg.get("url") or "",
    }


@app.get("/api/system")
def system() -> dict[str, object]:
    cfg = load_config()
    system_cfg = cfg.get("system", {}) or {}
    disk_path = resolve_path(system_cfg.get("disk_path") or cfg.get("database_path", "."))
    if disk_path.suffix:
        disk_path = disk_path.parent
    usage = shutil.disk_usage(disk_path if disk_path.exists() else Path.cwd())
    cpu_usage = None
    memory = None
    try:
        import psutil  # type: ignore[import-not-found]

        cpu_usage = psutil.cpu_percent(interval=None)
        memory_info = psutil.virtual_memory()
        memory = memory_stats_from_values(memory_info.total, memory_info.available)
    except Exception:
        try:
            load_1m = os.getloadavg()[0]
            cpu_count = os.cpu_count() or 1
            cpu_usage = min(100.0, max(0.0, (load_1m / cpu_count) * 100))
        except (AttributeError, OSError):
            cpu_usage = None
        memory = read_meminfo_memory()
    return {
        "cpu_percent": cpu_usage,
        "disk": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": (usage.used / usage.total * 100) if usage.total else None,
        },
        "memory": memory,
    }


@app.get("/api/insights")
def insights() -> dict[str, object]:
    return fetch_insights()


@app.get("/api/map/tiles/local")
def local_tiles() -> dict[str, object]:
    tile_root = STATIC_DIR / "tiles" / "osm"
    exists = tile_root.exists() and any(tile_root.glob("*/*/*.png"))
    return {
        "available": exists,
        "path": "/ui/tiles/osm/{z}/{x}/{y}.png",
    }


@app.get("/api/sources")
def sources() -> list[dict[str, object]]:
    return fetch_all("SELECT * FROM sources ORDER BY device_index, name")


@app.get("/api/events/recent")
def recent_events(limit: int = Query(100, ge=1, le=5000)) -> list[dict[str, object]]:
    return fetch_all("SELECT * FROM events ORDER BY timestamp DESC, id DESC LIMIT ?", (limit,))


@app.get("/api/aprs/recent")
def recent_aprs(limit: int = Query(100, ge=1, le=5000)) -> list[dict[str, object]]:
    return fetch_all(
        "SELECT * FROM events WHERE event_type = 'aprs_packet' ORDER BY timestamp DESC, id DESC LIMIT ?",
        (limit,),
    )


@app.get("/api/aprs/status")
def aprs_status() -> dict[str, object]:
    return fetch_aprs_status()


@app.get("/api/adsb/recent")
def recent_adsb(limit: int = Query(100, ge=1, le=5000)) -> list[dict[str, object]]:
    return fetch_all(
        "SELECT * FROM events WHERE event_type = 'adsb_aircraft' ORDER BY timestamp DESC, id DESC LIMIT ?",
        (limit,),
    )


@app.get("/api/captures")
def captures() -> list[dict[str, object]]:
    return fetch_all("SELECT * FROM captures ORDER BY start_time DESC, id DESC")


@app.get("/api/records")
def records() -> list[dict[str, object]]:
    return fetch_records()


@app.post("/api/events")
def create_event(event: EventIn) -> dict[str, int]:
    if event.source_id is None and event.source_name and event.source_type:
        from .db import get_or_create_source

        event.source_id = get_or_create_source(event.source_name, event.source_type)
    if event.source_id is None:
        raise HTTPException(status_code=400, detail="source_id or source_name/source_type is required")
    if hasattr(event, "model_dump"):
        payload = event.model_dump(exclude={"source_name", "source_type"})
    else:
        payload = event.dict(exclude={"source_name", "source_type"})
    event_id = insert_event(**payload)
    return {"id": event_id}
