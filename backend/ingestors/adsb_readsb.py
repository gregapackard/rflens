from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import load_config, resolve_path, source_config
from backend.db import get_or_create_source, insert_event, touch_source

EARTH_RADIUS_NMI = 3440.065
DEFAULT_MIN_INSERT_SECONDS = 120
DEFAULT_MIN_DISTANCE_NMI = 2
DEFAULT_MIN_ALTITUDE_CHANGE_FT = 1000
DEFAULT_MIN_RANGE_CHANGE_NMI = 10
STRONG_POSITIONLESS_RSSI_DB = -5
MAX_REASONABLE_RANGE_NMI = 500
MAX_REASONABLE_ALTITUDE_FT = 60000


@dataclass
class AircraftState:
    stored_at: float
    lat: float | None
    lon: float | None
    altitude: float | None
    range_nmi: float | None


@dataclass
class InsertThresholds:
    min_insert_seconds: float
    min_distance_nmi: float
    min_altitude_change_ft: float
    min_range_change_nmi: float


@dataclass
class BestSeen:
    range_nmi: float | None = None
    altitude: float | None = None
    rssi: float | None = None


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def config_number(cfg: dict[str, Any], key: str, default: float) -> float:
    value = number(cfg.get(key))
    return value if value is not None and value >= 0 else default


def altitude_value(aircraft: dict[str, Any]) -> float | None:
    altitude = aircraft.get("alt_baro", aircraft.get("alt_geom"))
    if altitude == "ground":
        return 0
    return number(altitude)


def aircraft_range(aircraft: dict[str, Any]) -> float | None:
    return number(aircraft.get("r_dst"))


def valid_coord(lat: float | None, lon: float | None) -> bool:
    return (
        lat is not None
        and lon is not None
        and abs(lat) <= 90
        and abs(lon) <= 180
        and not (lat == 0 and lon == 0)
    )


def distance_nmi(
    lat_a: float | None,
    lon_a: float | None,
    lat_b: float | None,
    lon_b: float | None,
) -> float | None:
    if not valid_coord(lat_a, lon_a) or not valid_coord(lat_b, lon_b):
        return None
    phi_a = math.radians(lat_a)
    phi_b = math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lambda = math.radians(lon_b - lon_a)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return EARTH_RADIUS_NMI * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def flag_is_set(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if isinstance(value, str):
        text = value.strip().lower()
        return bool(text and text not in {"0", "false", "none", "null", "undefined", "no"})
    return value not in (None, False, 0)


def squawk(aircraft: dict[str, Any]) -> str:
    return str(aircraft.get("squawk") or "").strip()


def has_emergency_signal(aircraft: dict[str, Any]) -> bool:
    return (
        squawk(aircraft) in {"7500", "7600", "7700"}
        or flag_is_set(aircraft.get("emergency"))
        or flag_is_set(aircraft.get("alert"))
        or flag_is_set(aircraft.get("spi"))
    )


def has_valid_position(aircraft: dict[str, Any]) -> bool:
    return valid_coord(number(aircraft.get("lat")), number(aircraft.get("lon")))


def beats_best_seen(value: float | None, best_value: float | None) -> bool:
    return value is not None and (best_value is None or value > best_value)


def is_record_candidate(aircraft: dict[str, Any], best_seen: BestSeen) -> bool:
    altitude = altitude_value(aircraft)
    positioned = has_valid_position(aircraft)
    range_nmi = aircraft_range(aircraft)
    rssi = number(aircraft.get("rssi"))
    return (
        (
            altitude is not None
            and positioned
            and 0 <= altitude <= MAX_REASONABLE_ALTITUDE_FT
            and beats_best_seen(altitude, best_seen.altitude)
        )
        or (
            range_nmi is not None
            and positioned
            and 0 <= range_nmi <= MAX_REASONABLE_RANGE_NMI
            and beats_best_seen(range_nmi, best_seen.range_nmi)
        )
        or (
            rssi is not None
            and STRONG_POSITIONLESS_RSSI_DB <= rssi <= 5
            and beats_best_seen(rssi, best_seen.rssi)
        )
    )


def update_best_seen(aircraft: dict[str, Any], best_seen: BestSeen) -> None:
    positioned = has_valid_position(aircraft)
    altitude = altitude_value(aircraft)
    range_nmi = aircraft_range(aircraft)
    rssi = number(aircraft.get("rssi"))
    if (
        altitude is not None
        and positioned
        and 0 <= altitude <= MAX_REASONABLE_ALTITUDE_FT
        and beats_best_seen(altitude, best_seen.altitude)
    ):
        best_seen.altitude = altitude
    if (
        range_nmi is not None
        and positioned
        and 0 <= range_nmi <= MAX_REASONABLE_RANGE_NMI
        and beats_best_seen(range_nmi, best_seen.range_nmi)
    ):
        best_seen.range_nmi = range_nmi
    if rssi is not None and STRONG_POSITIONLESS_RSSI_DB <= rssi <= 5 and beats_best_seen(rssi, best_seen.rssi):
        best_seen.rssi = rssi


def read_aircraft(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    aircraft = payload.get("aircraft", [])
    return aircraft if isinstance(aircraft, list) else []


def aircraft_callsign(aircraft: dict[str, Any]) -> str | None:
    flight = str(aircraft.get("flight") or "").strip()
    hex_id = str(aircraft.get("hex") or "").strip()
    return flight or hex_id or None


def aircraft_key(aircraft: dict[str, Any]) -> str:
    hex_id = str(aircraft.get("hex") or "").strip()
    flight = str(aircraft.get("flight") or "").strip()
    callsign = aircraft_callsign(aircraft)
    return hex_id or flight or callsign or "unknown"


def aircraft_state(aircraft: dict[str, Any], stored_at: float) -> AircraftState:
    return AircraftState(
        stored_at=stored_at,
        lat=number(aircraft.get("lat")),
        lon=number(aircraft.get("lon")),
        altitude=altitude_value(aircraft),
        range_nmi=aircraft_range(aircraft),
    )


def should_store_aircraft(
    aircraft: dict[str, Any],
    previous: AircraftState | None,
    now: float,
    thresholds: InsertThresholds,
    best_seen: BestSeen,
) -> bool:
    if has_emergency_signal(aircraft) or is_record_candidate(aircraft, best_seen):
        return True

    if not has_valid_position(aircraft):
        return False

    if previous is None:
        return True
    if now - previous.stored_at < thresholds.min_insert_seconds:
        return False

    current = aircraft_state(aircraft, now)
    moved = distance_nmi(previous.lat, previous.lon, current.lat, current.lon)
    if moved is not None and moved >= thresholds.min_distance_nmi:
        return True
    if (
        previous.altitude is not None
        and current.altitude is not None
        and abs(current.altitude - previous.altitude) >= thresholds.min_altitude_change_ft
    ):
        return True
    if (
        previous.range_nmi is not None
        and current.range_nmi is not None
        and abs(current.range_nmi - previous.range_nmi) >= thresholds.min_range_change_nmi
    ):
        return True
    return False


def store_aircraft(source_id: int, aircraft: dict[str, Any]) -> int:
    return insert_event(
        source_id=source_id,
        event_type="adsb_aircraft",
        callsign=aircraft_callsign(aircraft),
        lat=number(aircraft.get("lat")),
        lon=number(aircraft.get("lon")),
        altitude=altitude_value(aircraft),
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
    thresholds = InsertThresholds(
        min_insert_seconds=config_number(cfg, "min_insert_seconds", DEFAULT_MIN_INSERT_SECONDS),
        min_distance_nmi=config_number(cfg, "min_distance_nmi", DEFAULT_MIN_DISTANCE_NMI),
        min_altitude_change_ft=config_number(cfg, "min_altitude_change_ft", DEFAULT_MIN_ALTITUDE_CHANGE_FT),
        min_range_change_nmi=config_number(cfg, "min_range_change_nmi", DEFAULT_MIN_RANGE_CHANGE_NMI),
    )
    last_stored: dict[str, AircraftState] = {}
    best_seen = BestSeen()

    while True:
        try:
            aircraft = read_aircraft(path)
            now = time.time()
            for item in aircraft:
                if not isinstance(item, dict):
                    continue
                key = aircraft_key(item)
                if not should_store_aircraft(item, last_stored.get(key), now, thresholds, best_seen):
                    continue
                store_aircraft(source_id, item)
                last_stored[key] = aircraft_state(item, now)
                update_best_seen(item, best_seen)
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
