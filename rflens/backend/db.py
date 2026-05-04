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
                timestamp or utc_now(),
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
        return int(cur.lastrowid)


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
