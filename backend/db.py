from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import get_database_path, load_config


LOGGER = logging.getLogger(__name__)
SQLITE_TIMEOUT_SECONDS = 10.0
SQLITE_BUSY_TIMEOUT_MS = 10000
WAL_CONFIGURED_PATHS: set[str] = set()


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

CREATE TABLE IF NOT EXISTS aprs_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    callsign TEXT,
    online INTEGER,
    aprs_is_connected INTEGER,
    aprs_is_verified INTEGER,
    aprs_is_server TEXT,
    last_igate_status_at TEXT,
    last_rf_packet_at TEXT,
    last_rf_callsign TEXT,
    last_audio_level INTEGER,
    last_audio_quality TEXT,
    last_audio_timestamp TEXT,
    best_audio_level INTEGER,
    rf_packets_heard_total INTEGER,
    unique_callsigns_seen INTEGER,
    ignored_igate_lines INTEGER,
    ignored_status_lines INTEGER,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type_timestamp ON events(event_type, timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_captures_image_path ON captures(image_path);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure_wal(conn: sqlite3.Connection, path: Path) -> None:
    if path.name == ":memory:":
        return
    key = str(path.resolve())
    if key in WAL_CONFIGURED_PATHS:
        return
    try:
        current = conn.execute("PRAGMA journal_mode").fetchone()
        mode = str(current[0]).lower() if current else ""
        if mode != "wal":
            current = conn.execute("PRAGMA journal_mode = WAL").fetchone()
            mode = str(current[0]).lower() if current else ""
        if mode == "wal":
            WAL_CONFIGURED_PATHS.add(key)
    except sqlite3.OperationalError as exc:
        LOGGER.warning("Could not enable SQLite WAL mode: %s", exc)


@contextmanager
def connect(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path) if db_path else get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=SQLITE_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    configure_wal(conn, path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        cleanup_invalid_records(conn)
        cleanup_old_events(conn)
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


def valid_coord(lat: Any, lon: Any) -> bool:
    latitude = as_float(lat)
    longitude = as_float(lon)
    return (
        latitude is not None
        and longitude is not None
        and abs(latitude) <= 90
        and abs(longitude) <= 180
        and not (latitude == 0 and longitude == 0)
    )


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
    lat: float | None,
    lon: float | None,
    altitude: float | None,
    metadata_json: str | None,
) -> None:
    metadata = parse_metadata_payload(metadata_json)
    if event_type == "adsb_aircraft":
        has_position = valid_coord(lat if lat is not None else metadata.get("lat"), lon if lon is not None else metadata.get("lon"))
        range_nmi = as_float(metadata.get("r_dst"))
        if range_nmi is not None and has_position and 0 <= range_nmi <= 500:
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
        if altitude_ft is not None and has_position and 0 <= altitude_ft <= 60000:
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
        if rssi is not None and -60 <= rssi <= 5:
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


def cleanup_invalid_records(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM records
        WHERE record_type = 'adsb_highest_altitude'
          AND (value IS NULL OR value < 0 OR value > 60000)
        """
    )


def retention_days(retention: dict[str, Any], key: str, default: int | None = None) -> int | None:
    value = retention.get(key, default)
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None
    return days if days > 0 else None


def cutoff_for_days(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def delete_old_events(conn: sqlite3.Connection, event_type: str, days: int) -> int:
    cur = conn.execute(
        """
        DELETE FROM events
        WHERE event_type = ?
          AND timestamp < ?
        """,
        (event_type, cutoff_for_days(days)),
    )
    return cur.rowcount


def delete_old_routine_adsb_events(conn: sqlite3.Connection, days: int) -> int:
    cur = conn.execute(
        """
        DELETE FROM events
        WHERE event_type = 'adsb_aircraft'
          AND timestamp < ?
          AND COALESCE(metadata_json, '') NOT LIKE '%"squawk":"7500"%'
          AND COALESCE(metadata_json, '') NOT LIKE '%"squawk":"7600"%'
          AND COALESCE(metadata_json, '') NOT LIKE '%"squawk":"7700"%'
          AND NOT (
              COALESCE(metadata_json, '') LIKE '%"emergency"%'
              AND COALESCE(metadata_json, '') NOT LIKE '%"emergency":"none"%'
              AND COALESCE(metadata_json, '') NOT LIKE '%"emergency":null%'
              AND COALESCE(metadata_json, '') NOT LIKE '%"emergency":false%'
              AND COALESCE(metadata_json, '') NOT LIKE '%"emergency":0%'
          )
          AND COALESCE(metadata_json, '') NOT LIKE '%"alert":true%'
          AND COALESCE(metadata_json, '') NOT LIKE '%"alert":1%'
          AND COALESCE(metadata_json, '') NOT LIKE '%"spi":true%'
          AND COALESCE(metadata_json, '') NOT LIKE '%"spi":1%'
        """,
        (cutoff_for_days(days),),
    )
    return cur.rowcount


def cleanup_old_events(conn: sqlite3.Connection) -> None:
    cfg = load_config()
    retention = cfg.get("retention", {}) or {}
    if not isinstance(retention, dict):
        return

    try:
        adsb_days = retention_days(retention, "adsb_events_days", 7)
        if adsb_days is not None:
            deleted = delete_old_routine_adsb_events(conn, adsb_days)
            if deleted:
                LOGGER.info("Deleted %s old routine ADS-B events", deleted)

        aprs_days = retention_days(retention, "aprs_events_days", 30)
        if aprs_days is not None:
            deleted = delete_old_events(conn, "aprs_packet", aprs_days)
            if deleted:
                LOGGER.info("Deleted %s old APRS events", deleted)

        if "satellite_events_days" in retention:
            satellite_days = retention_days(retention, "satellite_events_days")
            if satellite_days is not None:
                deleted = delete_old_events(conn, "satellite_capture", satellite_days)
                if deleted:
                    LOGGER.info("Deleted %s old satellite capture events", deleted)
    except sqlite3.OperationalError as exc:
        LOGGER.warning("Skipping event retention cleanup: %s", exc)


def default_aprs_status() -> dict[str, Any]:
    return {
        "online": False,
        "callsign": "KF8GBU-10",
        "aprs_is_connected": False,
        "aprs_is_verified": False,
        "aprs_is_server": None,
        "last_igate_status_at": None,
        "last_rf_packet_at": None,
        "last_rf_callsign": None,
        "last_audio_level": None,
        "last_audio_quality": None,
        "last_audio_timestamp": None,
        "best_audio_level": None,
        "rf_packets_heard_total": 0,
        "unique_callsigns_seen": 0,
        "ignored_igate_lines": 0,
        "ignored_status_lines": 0,
    }


def ensure_aprs_status_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aprs_status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            callsign TEXT,
            online INTEGER,
            aprs_is_connected INTEGER,
            aprs_is_verified INTEGER,
            aprs_is_server TEXT,
            last_igate_status_at TEXT,
            last_rf_packet_at TEXT,
            last_rf_callsign TEXT,
            last_audio_level INTEGER,
            last_audio_quality TEXT,
            last_audio_timestamp TEXT,
            best_audio_level INTEGER,
            rf_packets_heard_total INTEGER,
            unique_callsigns_seen INTEGER,
            ignored_igate_lines INTEGER,
            ignored_status_lines INTEGER,
            updated_at TEXT
        )
        """
    )


def reset_aprs_status(callsign: str = "KF8GBU-10") -> None:
    now = utc_now()
    with connect() as conn:
        ensure_aprs_status_table(conn)
        conn.execute(
            """
            INSERT INTO aprs_status (
                id, callsign, online, aprs_is_connected, aprs_is_verified,
                rf_packets_heard_total, unique_callsigns_seen,
                ignored_igate_lines, ignored_status_lines, updated_at
            )
            VALUES (1, ?, 1, 0, 0, 0, 0, 0, 0, ?)
            ON CONFLICT(id) DO UPDATE SET
                callsign = excluded.callsign,
                online = 1,
                aprs_is_connected = 0,
                aprs_is_verified = 0,
                aprs_is_server = NULL,
                last_igate_status_at = NULL,
                last_rf_packet_at = NULL,
                last_rf_callsign = NULL,
                last_audio_level = NULL,
                last_audio_quality = NULL,
                last_audio_timestamp = NULL,
                best_audio_level = NULL,
                rf_packets_heard_total = 0,
                unique_callsigns_seen = 0,
                ignored_igate_lines = 0,
                ignored_status_lines = 0,
                updated_at = excluded.updated_at
            """,
            (callsign, now),
        )


def hydrate_aprs_status_from_recent_events(callsign: str = "KF8GBU-10") -> None:
    now = utc_now()
    with connect() as conn:
        ensure_aprs_status_table(conn)
        latest = conn.execute(
            """
            SELECT timestamp, callsign
            FROM events
            WHERE event_type = 'aprs_packet'
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        unique = conn.execute(
            """
            SELECT COUNT(DISTINCT callsign) AS count
            FROM events
            WHERE event_type = 'aprs_packet'
              AND callsign IS NOT NULL
              AND callsign != ''
            """
        ).fetchone()
        recent_audio_rows = conn.execute(
            """
            SELECT timestamp, metadata_json
            FROM events
            WHERE event_type = 'aprs_packet'
              AND metadata_json LIKE '%audio_level%'
            ORDER BY timestamp DESC, id DESC
            LIMIT 250
            """
        ).fetchall()
        conn.execute(
            """
            INSERT OR IGNORE INTO aprs_status (
                id, callsign, online, aprs_is_connected, aprs_is_verified,
                rf_packets_heard_total, unique_callsigns_seen,
                ignored_igate_lines, ignored_status_lines, updated_at
            )
            VALUES (1, ?, 1, 0, 0, 0, 0, 0, 0, ?)
            """,
            (callsign, now),
        )
        fields: dict[str, Any] = {
            "callsign": callsign,
            "online": 1,
            "unique_callsigns_seen": int(unique["count"] or 0) if unique else 0,
            "updated_at": now,
        }
        if latest:
            fields["last_rf_packet_at"] = latest["timestamp"]
            fields["last_rf_callsign"] = latest["callsign"]
        best_audio_level: int | None = None
        for audio_row in recent_audio_rows:
            metadata = parse_metadata_payload(audio_row["metadata_json"])
            level = as_float(metadata.get("audio_level"))
            if level is None:
                continue
            if "last_audio_level" not in fields:
                fields["last_audio_level"] = int(level)
                fields["last_audio_quality"] = metadata.get("audio_quality")
                fields["last_audio_timestamp"] = metadata.get("audio_timestamp") or audio_row["timestamp"]
            if best_audio_level is None or level > best_audio_level:
                best_audio_level = int(level)
        if best_audio_level is not None:
            fields["best_audio_level"] = best_audio_level
        columns = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE aprs_status SET {columns} WHERE id = 1", tuple(fields.values()))


def fetch_recent_aprs_callsigns() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT callsign
            FROM events
            WHERE event_type = 'aprs_packet'
              AND callsign IS NOT NULL
              AND callsign != ''
            """
        ).fetchall()
    return [str(row["callsign"]) for row in rows]


def update_aprs_status(**fields: Any) -> None:
    if not fields:
        return
    allowed = {
        "online",
        "callsign",
        "aprs_is_connected",
        "aprs_is_verified",
        "aprs_is_server",
        "last_igate_status_at",
        "last_rf_packet_at",
        "last_rf_callsign",
        "last_audio_level",
        "last_audio_quality",
        "last_audio_timestamp",
        "best_audio_level",
        "rf_packets_heard_total",
        "unique_callsigns_seen",
        "ignored_igate_lines",
        "ignored_status_lines",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = utc_now()
    columns = ", ".join(f"{key} = ?" for key in updates)
    values = tuple(updates.values())
    with connect() as conn:
        ensure_aprs_status_table(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO aprs_status (
                id, callsign, online, aprs_is_connected, aprs_is_verified,
                rf_packets_heard_total, unique_callsigns_seen,
                ignored_igate_lines, ignored_status_lines, updated_at
            )
            VALUES (1, 'KF8GBU-10', 1, 0, 0, 0, 0, 0, 0, ?)
            """,
            (utc_now(),),
        )
        conn.execute(f"UPDATE aprs_status SET {columns} WHERE id = 1", values)


def fetch_aprs_status() -> dict[str, Any]:
    with connect() as conn:
        ensure_aprs_status_table(conn)
        row = conn.execute("SELECT * FROM aprs_status WHERE id = 1").fetchone()
    if not row:
        return default_aprs_status()
    status = row_to_dict(row)
    for key in ("online", "aprs_is_connected", "aprs_is_verified"):
        status[key] = bool(status.get(key))
    for key in ("rf_packets_heard_total", "unique_callsigns_seen", "ignored_igate_lines", "ignored_status_lines"):
        status[key] = int(status.get(key) or 0)
    return status


def safe_update_records_for_event(conn: sqlite3.Connection, **kwargs: Any) -> None:
    try:
        update_records_for_event(conn, **kwargs)
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc).lower():
            raise
        LOGGER.warning("Skipping record update because SQLite is locked: %s", exc)


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
        safe_update_records_for_event(
            conn,
            event_id=event_id,
            event_type=event_type,
            timestamp=event_timestamp,
            callsign=callsign,
            lat=lat,
            lon=lon,
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


def record_by_type(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record.get("record_type")): record for record in records}


def station_type_label(value: Any) -> str:
    text = str(value or "station").replace("_", " ").strip().lower()
    labels = {
        "igate": "digi/iGate",
        "repeater object": "repeater object",
        "mobile digipeater": "mobile digipeater",
        "digipeater": "digipeater",
        "aircraft": "small aircraft tracker",
    }
    return labels.get(text, text or "station")


def format_audio(level: Any, quality: Any = None) -> str | None:
    number = as_float(level)
    if number is None:
        return None
    return f"{int(number)} ({quality})" if quality else str(int(number))


def format_distance_miles(value: Any) -> str | None:
    miles = as_float(value)
    if miles is None:
        return None
    return f"{miles:.0f} miles"


def format_distance_nmi(value: Any) -> str | None:
    nmi = as_float(value)
    if nmi is None:
        return None
    return f"{nmi:.1f} nmi"


def format_feet(value: Any) -> str | None:
    feet = as_float(value)
    if feet is None:
        return None
    return f"{feet:,.0f} ft"


def format_db(value: Any) -> str | None:
    db = as_float(value)
    if db is None:
        return None
    return f"{db:.1f} dB"


def first_number(*values: Any) -> float | None:
    for value in values:
        number = as_float(value)
        if number is not None:
            return number
    return None


def top_count(items: list[str]) -> str | None:
    counts: dict[str, int] = {}
    for item in items:
        if not item:
            continue
        counts[item] = counts.get(item, 0) + 1
    if not counts:
        return None
    key, count = sorted(counts.items(), key=lambda item: item[1], reverse=True)[0]
    return f"{key} ({count} packets)"


def aprs_event_sentence(event: dict[str, Any]) -> str:
    metadata = parse_metadata_payload(event.get("metadata_json"))
    callsign = event.get("callsign") or metadata.get("source_callsign") or "an APRS station"
    station_type = station_type_label(metadata.get("station_type"))
    if metadata.get("station_type") == "repeater_object":
        parts = [f"I heard {metadata.get('object_name') or callsign} as a repeater object"]
        if metadata.get("frequency_mhz"):
            parts.append(f"advertising {float(metadata['frequency_mhz']):.3f} MHz")
        if metadata.get("tone_hz"):
            parts.append(f"with PL {metadata['tone_hz']}")
        return " ".join(parts) + "."

    transport = str(metadata.get("heard_transport") or "RF").upper()
    parts = [f"I heard {callsign} over {transport}"]
    distance = format_distance_miles(metadata.get("distance_miles"))
    if distance:
        parts.append(f"{distance} away")
    heard_via = metadata.get("heard_via")
    if heard_via and heard_via != "direct":
        parts.append(f"via {heard_via}")
    elif metadata.get("was_direct") is True or heard_via == "direct":
        parts.append("directly")
    audio = format_audio(metadata.get("audio_level"), metadata.get("audio_quality"))
    if audio:
        parts.append(f"audio {audio}")
    sentence = ", ".join(parts)
    if station_type and station_type != "station":
        sentence += f", and it appears to be a {station_type}"
    return sentence + "."


def fetch_insights() -> dict[str, Any]:
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    aprs_status = fetch_aprs_status()
    records = record_by_type(fetch_records())
    aprs_recent = fetch_all(
        """
        SELECT *
        FROM events
        WHERE event_type = 'aprs_packet'
        ORDER BY timestamp DESC, id DESC
        LIMIT 250
        """
    )
    aprs_today = fetch_all(
        """
        SELECT *
        FROM events
        WHERE event_type = 'aprs_packet'
          AND timestamp >= ?
        ORDER BY timestamp DESC, id DESC
        """,
        (today_start,),
    )
    adsb_today = fetch_all(
        """
        SELECT *
        FROM events
        WHERE event_type = 'adsb_aircraft'
          AND timestamp >= ?
        ORDER BY timestamp DESC, id DESC
        """,
        (today_start,),
    )

    aprs_with_metadata = [(event, parse_metadata_payload(event.get("metadata_json"))) for event in aprs_recent]
    aprs_today_with_metadata = [(event, parse_metadata_payload(event.get("metadata_json"))) for event in aprs_today]
    adsb_today_with_metadata = [(event, parse_metadata_payload(event.get("metadata_json"))) for event in adsb_today]

    farthest_aprs = max(
        ((event, metadata, as_float(metadata.get("distance_miles"))) for event, metadata in aprs_with_metadata),
        key=lambda item: item[2] if item[2] is not None else -1,
        default=(None, {}, None),
    )
    farthest_today = max(
        ((event, metadata, as_float(metadata.get("distance_miles"))) for event, metadata in aprs_today_with_metadata),
        key=lambda item: item[2] if item[2] is not None else -1,
        default=(None, {}, None),
    )
    best_audio = max(
        ((event, metadata, as_float(metadata.get("audio_level"))) for event, metadata in aprs_with_metadata),
        key=lambda item: item[2] if item[2] is not None else -1,
        default=(None, {}, None),
    )
    best_audio_today = max(
        ((event, metadata, as_float(metadata.get("audio_level"))) for event, metadata in aprs_today_with_metadata),
        key=lambda item: item[2] if item[2] is not None else -1,
        default=(None, {}, None),
    )
    latest_aprs = aprs_recent[0] if aprs_recent else None
    latest_metadata = parse_metadata_payload(latest_aprs.get("metadata_json")) if latest_aprs else {}
    top_digi = top_count([
        str(metadata.get("heard_via") or "")
        for _event, metadata in aprs_with_metadata
        if metadata.get("heard_via") and metadata.get("heard_via") != "direct"
    ])
    top_digi_today = top_count([
        str(metadata.get("heard_via") or "")
        for _event, metadata in aprs_today_with_metadata
        if metadata.get("heard_via") and metadata.get("heard_via") != "direct"
    ])

    adsb_ranges = [value for value in (as_float(metadata.get("r_dst")) for _event, metadata in adsb_today_with_metadata) if value is not None]
    adsb_altitudes = [
        value
        for value in (first_number(event.get("altitude"), metadata.get("alt_baro")) for event, metadata in adsb_today_with_metadata)
        if value is not None
    ]
    adsb_rssis = [value for value in (as_float(metadata.get("rssi")) for _event, metadata in adsb_today_with_metadata) if value is not None]
    adsb_range_today = max(adsb_ranges, default=None)
    adsb_altitude_today = max(adsb_altitudes, default=None)
    adsb_rssi_today = max(adsb_rssis, default=None)

    summary: list[str] = []
    if aprs_status.get("aprs_is_connected") and aprs_status.get("aprs_is_verified"):
        summary.append("Your APRS iGate is online and verified with APRS-IS.")
    elif aprs_status.get("online"):
        summary.append("Your APRS receiver is online, but APRS-IS verification is not confirmed yet.")
    if aprs_recent:
        unique_recent = len({event.get("callsign") for event in aprs_recent if event.get("callsign")})
        summary.append(f"You heard {unique_recent} APRS stations recently over RF.")
    if farthest_aprs[0] and farthest_aprs[2] is not None:
        summary.append(f"The farthest APRS station heard was {farthest_aprs[0].get('callsign')} at approximately {farthest_aprs[2]:.0f} miles.")
    if latest_aprs:
        via = latest_metadata.get("heard_via")
        via_text = f" via {via}" if via and via != "direct" else " directly" if via == "direct" else ""
        summary.append(f"Most recent RF APRS packet was {latest_aprs.get('callsign')}{via_text}.")
    if best_audio[0] and best_audio[2] is not None:
        summary.append(f"Best APRS audio recently was {format_audio(best_audio[2], best_audio[1].get('audio_quality'))}.")
    if not summary:
        summary.append("RF Lens is waiting for fresh APRS or ADS-B observations to summarize.")

    aprs_plain = [aprs_event_sentence(event) for event in aprs_recent[:8]]
    adsb_plain: list[str] = []
    if records.get("adsb_max_range"):
        adsb_plain.append(f"Your farthest ADS-B aircraft record is {records['adsb_max_range'].get('value_text')}.")
    if records.get("adsb_highest_altitude"):
        adsb_plain.append(f"Highest valid aircraft altitude recorded is {records['adsb_highest_altitude'].get('value_text')}.")
    if records.get("adsb_strongest_signal"):
        adsb_plain.append(f"Strongest ADS-B signal recorded is {records['adsb_strongest_signal'].get('value_text')}.")
    if not adsb_plain:
        adsb_plain.append("ADS-B records will appear here once aircraft with valid range, altitude, or signal data are stored.")
    for line in adsb_plain:
        if len(summary) >= 6:
            break
        if line not in summary:
            summary.append(line)
    if len(summary) < 3 and not aprs_recent:
        summary.append("No stored APRS RF packets are available for the insight layer yet.")
    if len(summary) < 3 and not adsb_today:
        summary.append("No ADS-B aircraft events have been stored today yet.")

    return {
        "summary": summary[:6],
        "daily": {
            "aprs_packets_heard_today": len(aprs_today),
            "unique_aprs_stations_heard_today": len({event.get("callsign") for event in aprs_today if event.get("callsign")}),
            "farthest_aprs_station_today": (
                f"{farthest_today[0].get('callsign')} at approximately {farthest_today[2]:.0f} miles"
                if farthest_today[0] and farthest_today[2] is not None
                else None
            ),
            "best_aprs_audio_today": (
                format_audio(best_audio_today[2], best_audio_today[1].get("audio_quality"))
                if best_audio_today[0] and best_audio_today[2] is not None
                else None
            ),
            "most_common_digipeater_path_today": top_digi_today,
            "adsb_max_range_today": format_distance_nmi(adsb_range_today),
            "adsb_highest_altitude_today": format_feet(adsb_altitude_today),
            "adsb_strongest_signal_today": format_db(adsb_rssi_today),
        },
        "aprs": {
            "plain_english": aprs_plain,
            "notable": {
                "farthest_heard": (
                    f"{farthest_aprs[0].get('callsign')} at approximately {farthest_aprs[2]:.0f} miles"
                    if farthest_aprs[0] and farthest_aprs[2] is not None
                    else None
                ),
                "best_audio": (
                    f"{best_audio[0].get('callsign')} with audio {format_audio(best_audio[2], best_audio[1].get('audio_quality'))}"
                    if best_audio[0] and best_audio[2] is not None
                    else None
                ),
                "latest_rf": (
                    f"{latest_aprs.get('callsign')} via {latest_metadata.get('heard_via')}"
                    if latest_aprs and latest_metadata.get("heard_via")
                    else latest_aprs.get("callsign") if latest_aprs else None
                ),
                "top_digipeater": top_digi,
            },
        },
        "adsb": {
            "plain_english": adsb_plain,
            "today": {
                "max_range": format_distance_nmi(adsb_range_today),
                "highest_altitude": format_feet(adsb_altitude_today),
                "strongest_signal": format_db(adsb_rssi_today),
            },
        },
    }
