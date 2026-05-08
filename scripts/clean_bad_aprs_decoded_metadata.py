from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.config import get_database_path, load_config
from backend.ingestors.aprs_direwolf import distance_quality, range_and_bearing, valid_coord


POSITION_DERIVED_FIELDS = (
    "decoded_lat",
    "decoded_lon",
    "distance_km",
    "distance_miles",
    "distance_nmi",
    "bearing_degrees",
    "distance_quality",
)


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def decoded_position_bad(metadata: dict[str, Any], station_cfg: dict[str, Any]) -> tuple[bool, list[str]]:
    lat = as_float(metadata.get("decoded_lat"))
    lon = as_float(metadata.get("decoded_lon"))
    reasons: list[str] = []
    if lat is None or lon is None:
        return False, reasons
    if not valid_coord(lat, lon):
        reasons.append("decoded coordinates are invalid")
    station_lon = as_float(station_cfg.get("lon"))
    if station_lon is not None and station_lon < 0 and lon > 0:
        reasons.append("decoded longitude is positive for a western-hemisphere station")
    category = str(metadata.get("heard_category") or "unknown")
    distances = range_and_bearing(station_cfg.get("lat"), station_cfg.get("lon"), lat, lon)
    quality = distance_quality(distances.get("distance_miles"), category, station_cfg)
    if quality == "questionable":
        reasons.append("decoded coordinates are implausible for this RF category")
    if metadata.get("distance_quality") == "questionable" and metadata.get("distance_miles") is not None:
        reasons.append("stored distance quality is questionable")
    return bool(reasons), reasons


def event_position_looks_decoded(row: sqlite3.Row, metadata: dict[str, Any]) -> bool:
    event_lat = as_float(row["lat"])
    event_lon = as_float(row["lon"])
    decoded_lat = as_float(metadata.get("decoded_lat"))
    decoded_lon = as_float(metadata.get("decoded_lon"))
    metadata_lat = metadata.get("lat")
    metadata_lon = metadata.get("lon")
    if event_lat is None or event_lon is None or decoded_lat is None or decoded_lon is None:
        return False
    if metadata_lat is not None or metadata_lon is not None:
        return False
    return abs(event_lat - decoded_lat) < 0.000001 and abs(event_lon - decoded_lon) < 0.000001


def cleaned_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(metadata)
    for key in POSITION_DERIVED_FIELDS:
        cleaned.pop(key, None)
    return cleaned


def cleanup_candidates(rows: list[sqlite3.Row], station_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(metadata, dict):
            continue
        bad, reasons = decoded_position_bad(metadata, station_cfg)
        if not bad:
            continue
        clear_event_position = event_position_looks_decoded(row, metadata)
        candidates.append({
            "id": row["id"],
            "callsign": row["callsign"],
            "reasons": reasons,
            "metadata": metadata,
            "cleaned_metadata": cleaned_metadata(metadata),
            "clear_event_position": clear_event_position,
        })
    return candidates


def fetch_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT id, callsign, lat, lon, metadata_json
        FROM events
        WHERE event_type = 'aprs_packet'
          AND metadata_json LIKE '%decoded_lat%'
          AND metadata_json LIKE '%decoded_lon%'
        ORDER BY id
        """
    ).fetchall()


def apply_cleanup(conn: sqlite3.Connection, candidates: list[dict[str, Any]]) -> None:
    for candidate in candidates:
        metadata_json = json.dumps(candidate["cleaned_metadata"], separators=(",", ":"), default=str)
        if candidate["clear_event_position"]:
            conn.execute(
                "UPDATE events SET lat = NULL, lon = NULL, metadata_json = ? WHERE id = ?",
                (metadata_json, candidate["id"]),
            )
        else:
            conn.execute(
                "UPDATE events SET metadata_json = ? WHERE id = ?",
                (metadata_json, candidate["id"]),
            )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply cleanup for APRS decoded coordinates from the old broad Direwolf follow-up parser."
    )
    parser.add_argument("--db", type=Path, default=None, help="SQLite database path. Defaults to config.yaml database_path.")
    parser.add_argument("--config", type=Path, default=None, help="Config path. Defaults to config.yaml.")
    parser.add_argument("--apply", action="store_true", help="Apply updates. Without this flag the script only reports changes.")
    args = parser.parse_args()

    cfg = load_config(args.config)
    db_path = args.db or get_database_path(cfg)
    station_cfg = cfg.get("station", {}) or {}
    with sqlite3.connect(db_path) as conn:
        rows = fetch_rows(conn)
        candidates = cleanup_candidates(rows, station_cfg)
        mode = "APPLY" if args.apply else "DRY RUN"
        print(f"{mode}: scanned {len(rows)} APRS decoded rows; {len(candidates)} row(s) {'will be' if args.apply else 'would be'} changed.")
        for candidate in candidates[:25]:
            callsign = candidate.get("callsign") or "-"
            reasons = "; ".join(candidate["reasons"])
            clear_note = " and clear event lat/lon" if candidate["clear_event_position"] else ""
            print(f"  id={candidate['id']} callsign={callsign}: remove decoded position metadata{clear_note} ({reasons})")
        if len(candidates) > 25:
            print(f"  ... {len(candidates) - 25} more candidate row(s)")
        if args.apply and candidates:
            apply_cleanup(conn, candidates)
            print(f"Applied cleanup to {len(candidates)} row(s).")


if __name__ == "__main__":
    main()
