from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import get_database_path, load_config


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT,
    type TEXT,
    device_index INTEGER,
    frequency TEXT,
    status TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    source_id INTEGER,
    event_type TEXT,
    timestamp TEXT,
    callsign TEXT,
    lat REAL,
    lon REAL,
    altitude REAL,
    speed REAL,
    raw_text TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS records (
    record_type TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    value REAL,
    value_text TEXT,
    callsign TEXT,
    timestamp TEXT,
    source_event_id INTEGER,
    metadata_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY,
    source_id INTEGER,
    satellite TEXT,
    start_time TEXT,
    end_time TEXT,
    max_elevation REAL,
    image_path TEXT,
    status TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type_timestamp ON events(event_type, timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_captures_image_path ON captures(image_path);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path) if db_path else get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        seed_records_from_events(conn)
    ensure_configured_sources()


def ensure_configured_sources() -> None:
    cfg = load_config()
    sources = cfg.get("sources", {}) or {}
    with connect() as conn:
        for source_type, info in sources.items():
            name = info.get("name", source_type.upper())
            existing = conn.execute(
                "SELECT id FROM sources WHERE type = ? OR name = ?",
                (source_type, name),
            ).fetchone()
            values = (
                name,
                source_type,
                info.get("device_index"),
                info.get("frequency"),
                "enabled" if info.get("enabled", False) else "disabled",
                None,
            )
            if existing:
                conn.execute(
                    """
                    UPDATE sources
                    SET name = ?, type = ?, device_index = ?, frequency = ?, status = COALESCE(status, ?)
                    WHERE id = ?
                    """,
                    values[:5] + (existing["id"],),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO sources (name, type, device_index, frequency, status, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def parse_metadata_payload(payload: str | None) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def format_count(value: float | int) -> str:
    count = int(value)
    return f"{count} capture{'s' if count != 1 else ''}"


def upsert_record(
    conn: sqlite3.Connection,
    *,
    record_type: str,
    label: str,
    value: float | int | None,
    value_text: str,
    callsign: str | None,
    timestamp: str | None,
    source_event_id: int | None,
    metadata_json: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO records (
            record_type, label, value, value_text, callsign, timestamp,
            source_event_id, metadata_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(record_type) DO UPDATE SET
            label = excluded.label,
            value = excluded.value,
            value_text = excluded.value_text,
            callsign = excluded.callsign,
            timestamp = excluded.timestamp,
            source_event_id = excluded.source_event_id,
            metadata_json = excluded.metadata_json,
            updated_at = excluded.updated_at
        """,
        (
            record_type,
            label,
            value,
            value_text,
            callsign,
            timestamp,
            source_event_id,
            metadata_json,
            utc_now(),
        ),
    )


def upsert_max_record(
    conn: sqlite3.Connection,
    *,
    record_type: str,
    label: str,
    value: float,
    value_text: str,
    callsign: str | None,
    timestamp: str | None,
    source_event_id: int,
    metadata_json: str | None,
) -> None:
    existing = conn.execute(
        "SELECT value FROM records WHERE record_type = ?",
        (record_type,),
    ).fetchone()
    if existing and as_float(existing["value"]) is not None and value <= float(existing["value"]):
        return
    upsert_record(
        conn,
        record_type=record_type,
        label=label,
        value=value,
        value_text=value_text,
        callsign=callsign,
        timestamp=timestamp,
        source_event_id=source_event_id,
        metadata_json=metadata_json,
    )


def update_records_for_event(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    event_type: str,
    timestamp: str | None,
    callsign: str | None,
    altitude: float | None,
    metadata_json: str | None,
) -> None:
    metadata = parse_metadata_payload(metadata_json)
    if event_type == "adsb_aircraft":
        range_nmi = as_float(metadata.get("r_dst"))
        if range_nmi is not None:
            upsert_max_record(
                conn,
                record_type="adsb_max_range",
                label="ADS-B max range",
                value=range_nmi,
                value_text=f"{range_nmi:.1f} nmi",
                callsign=callsign or metadata.get("flight") or metadata.get("hex"),
                timestamp=timestamp,
                source_event_id=event_id,
                metadata_json=metadata_json,
            )
        altitude_ft = as_float(altitude)
        if altitude_ft is None:
            altitude_ft = as_float(metadata.get("alt_baro"))
        if altitude_ft is not None:
            upsert_max_record(
                conn,
                record_type="adsb_highest_altitude",
                label="ADS-B highest altitude",
                value=altitude_ft,
                value_text=f"{altitude_ft:,.0f} ft",
                callsign=callsign or metadata.get("flight") or metadata.get("hex"),
                timestamp=timestamp,
                source_event_id=event_id,
                metadata_json=metadata_json,
            )
        rssi = as_float(metadata.get("rssi"))
        if rssi is not None:
            upsert_max_record(
                conn,
                record_type="adsb_strongest_signal",
                label="ADS-B strongest signal",
                value=rssi,
                value_text=f"{rssi:.1f} dB",
                callsign=callsign or metadata.get("flight") or metadata.get("hex"),
                timestamp=timestamp,
                source_event_id=event_id,
                metadata_json=metadata_json,
            )
    if event_type == "satellite_capture":
        existing = conn.execute(
            "SELECT value FROM records WHERE record_type = 'satellite_total_captures'"
        ).fetchone()
        total = int(as_float(existing["value"]) or 0) + 1 if existing else 1
        upsert_record(
            conn,
            record_type="satellite_total_captures",
            label="Satellite total captures",
            value=total,
            value_text=format_count(total),
            callsign=callsign or metadata.get("satellite") or metadata.get("name"),
            timestamp=timestamp,
            source_event_id=event_id,
            metadata_json=metadata_json,
        )
        latest = conn.execute(
            "SELECT timestamp FROM records WHERE record_type = 'satellite_latest_capture'"
        ).fetchone()
        if not latest or (timestamp or "") >= (latest["timestamp"] or ""):
            upsert_record(
                conn,
                record_type="satellite_latest_capture",
                label="Satellite latest capture",
                value=as_float(event_id),
                value_text=callsign or metadata.get("satellite") or metadata.get("name") or "Satellite capture",
                callsign=callsign or metadata.get("satellite") or metadata.get("name"),
                timestamp=timestamp,
                source_event_id=event_id,
                metadata_json=metadata_json,
            )


def seed_records_from_events(conn: sqlite3.Connection) -> None:
    record_count = conn.execute("SELECT COUNT(*) AS count FROM records").fetchone()["count"]
    if record_count:
        return
    rows = conn.execute(
        """
        SELECT id, event_type, timestamp, callsign, altitude, metadata_json
        FROM events
        WHERE event_type IN ('adsb_aircraft', 'satellite_capture')
        ORDER BY timestamp ASC, id ASC
        """
    ).fetchall()
    for row in rows:
        update_records_for_event(
            conn,
            event_id=int(row["id"]),
            event_type=row["event_type"],
            timestamp=row["timestamp"],
            callsign=row["callsign"],
            altitude=row["altitude"],
            metadata_json=row["metadata_json"],
        )


def get_or_create_source(
    name: str,
    source_type: str,
    device_index: int | None = None,
    frequency: str | None = None,
) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM sources WHERE type = ? OR name = ?",
            (source_type, name),
        ).fetchone()
        if row:
            source_id = int(row["id"])
            conn.execute(
                """
                UPDATE sources
                SET name = ?, type = ?, device_index = COALESCE(?, device_index),
                    frequency = COALESCE(?, frequency), status = 'online', last_seen = ?
                WHERE id = ?
                """,
                (name, source_type, device_index, frequency, utc_now(), source_id),
            )
            return source_id
        cur = conn.execute(
            """
            INSERT INTO sources (name, type, device_index, frequency, status, last_seen)
            VALUES (?, ?, ?, ?, 'online', ?)
            """,
            (name, source_type, device_index, frequency, utc_now()),
        )
        return int(cur.lastrowid)


def touch_source(source_id: int, status: str = "online") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE sources SET status = ?, last_seen = ? WHERE id = ?",
            (status, utc_now(), source_id),
        )


def insert_event(
    *,
    event_type: str,
    source_id: int | None = None,
    timestamp: str | None = None,
    callsign: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    altitude: float | None = None,
    speed: float | None = None,
    raw_text: str | None = None,
    metadata: dict[str, Any] | list[Any] | None = None,
    metadata_json: str | None = None,
) -> int:
    payload = metadata_json
    if payload is None and metadata is not None:
        payload = json.dumps(metadata, separators=(",", ":"), default=str)
    event_timestamp = timestamp or utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO events (
                source_id, event_type, timestamp, callsign, lat, lon,
                altitude, speed, raw_text, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                event_type,
                event_timestamp,
                callsign,
                lat,
                lon,
                altitude,
                speed,
                raw_text,
                payload,
            ),
        )
        if source_id:
            conn.execute(
                "UPDATE sources SET status = 'online', last_seen = ? WHERE id = ?",
                (utc_now(), source_id),
            )
        event_id = int(cur.lastrowid)
        update_records_for_event(
            conn,
            event_id=event_id,
            event_type=event_type,
            timestamp=event_timestamp,
            callsign=callsign,
            altitude=altitude,
            metadata_json=payload,
        )
        return event_id


def insert_capture(
    *,
    source_id: int | None,
    satellite: str | None,
    start_time: str | None,
    end_time: str | None,
    max_elevation: float | None,
    image_path: str,
    status: str = "complete",
    metadata: dict[str, Any] | None = None,
) -> int | None:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO captures (
                source_id, satellite, start_time, end_time, max_elevation,
                image_path, status, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                satellite,
                start_time,
                end_time,
                max_elevation,
                image_path,
                status,
                json.dumps(metadata or {}, separators=(",", ":"), default=str),
            ),
        )
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [row_to_dict(row) for row in conn.execute(query, params).fetchall()]


def fetch_records() -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT
            record_type, label, value, value_text, callsign, timestamp,
            source_event_id, metadata_json, updated_at
        FROM records
        ORDER BY CASE record_type
            WHEN 'adsb_max_range' THEN 1
            WHEN 'adsb_highest_altitude' THEN 2
            WHEN 'adsb_strongest_signal' THEN 3
            WHEN 'satellite_total_captures' THEN 4
            WHEN 'satellite_latest_capture' THEN 5
            ELSE 99
        END, label
        """
    )
