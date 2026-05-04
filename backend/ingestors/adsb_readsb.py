from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from backend.config import load_config, resolve_path, source_config
from backend.db import get_or_create_source, insert_event, touch_source


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_aircraft(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    aircraft = payload.get("aircraft", [])
    return aircraft if isinstance(aircraft, list) else []


def aircraft_callsign(aircraft: dict[str, Any]) -> str | None:
    flight = str(aircraft.get("flight") or "").strip()
    hex_id = str(aircraft.get("hex") or "").strip()
    return flight or hex_id or None


def store_aircraft(source_id: int, aircraft: dict[str, Any]) -> int:
    altitude = aircraft.get("alt_baro", aircraft.get("alt_geom"))
    if altitude == "ground":
        altitude = 0
    return insert_event(
        source_id=source_id,
        event_type="adsb_aircraft",
        callsign=aircraft_callsign(aircraft),
        lat=number(aircraft.get("lat")),
        lon=number(aircraft.get("lon")),
        altitude=number(altitude),
        speed=number(aircraft.get("gs")),
        raw_text=json.dumps(aircraft, separators=(",", ":"), default=str),
        metadata=aircraft,
    )


def run_forever() -> None:
    cfg = source_config("adsb")
    source_id = get_or_create_source(
        cfg.get("name", "ADS-B readsb"),
        "adsb",
        cfg.get("device_index"),
        cfg.get("frequency"),
    )
    path = resolve_path(cfg.get("aircraft_json_path", "/run/readsb/aircraft.json"))
    interval = float(cfg.get("poll_seconds", 2))
    seen: dict[str, float] = {}

    while True:
        try:
            aircraft = read_aircraft(path)
            now = time.time()
            for item in aircraft:
                if not isinstance(item, dict):
                    continue
                key = "|".join(
                    str(item.get(field, ""))
                    for field in ("hex", "flight", "lat", "lon", "alt_baro", "gs", "seen")
                )
                last = seen.get(key)
                if last and now - last < interval * 2:
                    continue
                seen[key] = now
                store_aircraft(source_id, item)
            touch_source(source_id, "online")
        except FileNotFoundError:
            touch_source(source_id, "missing")
        except json.JSONDecodeError:
            touch_source(source_id, "malformed")
        except Exception as exc:
            touch_source(source_id, f"error: {exc}")
        time.sleep(interval)


def main() -> None:
    load_config()
    run_forever()


if __name__ == "__main__":
    main()
