from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Iterator

from backend.config import resolve_path, source_config
from backend.db import get_or_create_source, insert_event, reset_aprs_status, touch_source, update_aprs_status, utc_now


APRS_CALLSIGN = "KF8GBU-10"
OWN_CALLSIGNS = {APRS_CALLSIGN}
DUPLICATE_WINDOW_SECONDS = 60
CALLSIGN_RE = re.compile(r"^[A-Z0-9]{1,9}(?:-[0-9]{1,2})?$", re.IGNORECASE)
POSITION_RE = re.compile(r"(\d{2})(\d{2}\.\d{2})([NS]).*?(\d{3})(\d{2}\.\d{2})([EW])")
AUDIO_LEVEL_RE = re.compile(r"audio level\s*=\s*(\d+)\(([^)]*)\)", re.IGNORECASE)
SERVER_RE = re.compile(r"\b([A-Za-z0-9.-]+\.(?:net|org|com)(?::\d+)?)\b")
STATUS_PREFIXES = (
    "#",
    "ERROR!!!",
    "Use of",
    "Digipeater ",
    "Position,",
    "Ready to accept",
    "Now connected",
    "Check server status",
)
STATUS_PHRASES = (
    "When using APRS",
    "audio level",
    "Dire Wolf",
    "Direwolf",
)


def split_prefix(line: str) -> tuple[str | None, str]:
    text = line.strip()
    if text.startswith("[") and "]" in text:
        prefix, rest = text.split("]", 1)
        return prefix[1:].strip(), rest.strip()
    return None, text


def normalize_packet_line(line: str) -> str:
    _prefix, text = split_prefix(line)
    return text


def ignored_prefix(prefix: str | None) -> bool:
    return bool(prefix and prefix.lower().startswith("ig"))


def status_or_help_line(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in STATUS_PREFIXES) or any(phrase in text for phrase in STATUS_PHRASES)


def parse_audio_status(text: str) -> tuple[int, str] | None:
    match = AUDIO_LEVEL_RE.search(text)
    if not match:
        return None
    return int(match.group(1)), match.group(2).strip()


def parse_igate_status(text: str) -> dict[str, Any]:
    lowered = text.lower()
    fields: dict[str, Any] = {
        "online": True,
        "aprs_is_connected": True,
        "last_igate_status_at": utc_now(),
    }
    if f"logresp {APRS_CALLSIGN.lower()} verified" in lowered:
        fields["aprs_is_verified"] = True
    server_match = SERVER_RE.search(text)
    if server_match:
        fields["aprs_is_server"] = server_match.group(1)
    return fields


def aprs_is_status_line(text: str) -> bool:
    lowered = text.lower()
    return "logresp" in lowered or "now connected" in lowered or "connected to" in lowered


def parse_packet_header(text: str) -> tuple[str, str] | None:
    if ">" not in text or ":" not in text:
        return None
    header, _payload = text.split(":", 1)
    if ">" not in header:
        return None
    source, path = header.split(">", 1)
    source = source.strip()
    destination = path.split(",", 1)[0].strip()
    if not source or not destination:
        return None
    if not CALLSIGN_RE.match(source):
        return None
    return source.upper(), destination.upper()


def packet_like(line: str) -> bool:
    prefix, text = split_prefix(line)
    if ignored_prefix(prefix):
        return False
    if len(text) < 6:
        return False
    if status_or_help_line(text):
        return False
    parsed_header = parse_packet_header(text)
    if not parsed_header:
        return False
    callsign, _destination = parsed_header
    return callsign not in OWN_CALLSIGNS


def parse_position(text: str) -> tuple[float | None, float | None]:
    match = POSITION_RE.search(text)
    if not match:
        return None, None
    lat_deg, lat_min, lat_hemi, lon_deg, lon_min, lon_hemi = match.groups()
    lat = int(lat_deg) + float(lat_min) / 60
    lon = int(lon_deg) + float(lon_min) / 60
    if lat_hemi.upper() == "S":
        lat *= -1
    if lon_hemi.upper() == "W":
        lon *= -1
    return lat, lon


def parse_line(line: str) -> dict[str, str | float | None]:
    text = normalize_packet_line(line)
    parsed_header = parse_packet_header(text)
    callsign, destination = parsed_header if parsed_header else (None, None)
    lat, lon = parse_position(text)
    return {
        "callsign": callsign,
        "destination": destination,
        "lat": lat,
        "lon": lon,
        "line": text,
    }


def duplicate_packet(packet_line: str, seen_packets: dict[str, float], now: float) -> bool:
    last_seen = seen_packets.get(packet_line)
    expired = [line for line, seen_at in seen_packets.items() if now - seen_at > DUPLICATE_WINDOW_SECONDS]
    for line in expired:
        del seen_packets[line]
    if last_seen is not None and now - last_seen < DUPLICATE_WINDOW_SECONDS:
        return True
    seen_packets[packet_line] = now
    return False


def update_ignored_igate(count: int, text: str) -> None:
    fields = parse_igate_status(text)
    fields["ignored_igate_lines"] = count
    update_aprs_status(**fields)


def update_ignored_status(count: int) -> None:
    update_aprs_status(online=True, ignored_status_lines=count)


def update_audio_metrics(level: int, quality: str, best_level: int | None, ignored_status_lines: int) -> None:
    fields: dict[str, Any] = {
        "online": True,
        "last_audio_level": level,
        "last_audio_quality": quality,
        "last_audio_timestamp": utc_now(),
        "ignored_status_lines": ignored_status_lines,
    }
    if best_level is None or level > best_level:
        fields["best_audio_level"] = level
    update_aprs_status(**fields)


def update_rf_metrics(total: int, callsigns: set[str], last_callsign: str | None, timestamp: str) -> None:
    update_aprs_status(
        online=True,
        last_rf_packet_at=timestamp,
        last_rf_callsign=last_callsign,
        rf_packets_heard_total=total,
        unique_callsigns_seen=len(callsigns),
    )


def follow(path: Path) -> Iterator[str]:
    position = 0
    inode = None
    while True:
        try:
            stat = path.stat()
            if inode != stat.st_ino or position > stat.st_size:
                inode = stat.st_ino
                position = 0
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(position)
                while True:
                    line = handle.readline()
                    if not line:
                        break
                    position = handle.tell()
                    yield line.rstrip("\n")
        except FileNotFoundError:
            yield ""
        time.sleep(1)


def run_forever() -> None:
    cfg = source_config("aprs")
    source_id = get_or_create_source(
        cfg.get("name", "APRS Direwolf"),
        "aprs",
        cfg.get("device_index"),
        cfg.get("frequency"),
    )
    path = resolve_path(cfg.get("log_path", "./data/direwolf.log"))
    seen_packets: dict[str, float] = {}
    seen_callsigns: set[str] = set()
    rf_packets_heard_total = 0
    ignored_igate_lines = 0
    ignored_status_lines = 0
    best_audio_level: int | None = None
    reset_aprs_status(APRS_CALLSIGN)

    for line in follow(path):
        if not line:
            touch_source(source_id, "missing" if not path.exists() else "online")
            continue
        prefix, text = split_prefix(line)
        if ignored_prefix(prefix):
            ignored_igate_lines += 1
            update_ignored_igate(ignored_igate_lines, text)
            touch_source(source_id, "online")
            continue
        audio_status = parse_audio_status(text)
        if audio_status:
            ignored_status_lines += 1
            level, quality = audio_status
            update_audio_metrics(level, quality, best_audio_level, ignored_status_lines)
            if best_audio_level is None or level > best_audio_level:
                best_audio_level = level
            touch_source(source_id, "online")
            continue
        if status_or_help_line(text):
            ignored_status_lines += 1
            if aprs_is_status_line(text):
                fields = parse_igate_status(text)
                fields["ignored_status_lines"] = ignored_status_lines
                update_aprs_status(**fields)
            else:
                update_ignored_status(ignored_status_lines)
            touch_source(source_id, "online")
            continue
        if not packet_like(line):
            continue
        parsed = parse_line(line)
        packet_line = str(parsed.get("line") or "")
        if duplicate_packet(packet_line, seen_packets, time.time()):
            continue
        insert_event(
            source_id=source_id,
            event_type="aprs_packet",
            callsign=parsed.get("callsign"),
            lat=parsed.get("lat"),
            lon=parsed.get("lon"),
            raw_text=line,
            metadata=parsed,
        )
        packet_timestamp = utc_now()
        rf_packets_heard_total += 1
        callsign = str(parsed.get("callsign") or "")
        if callsign:
            seen_callsigns.add(callsign)
        update_rf_metrics(rf_packets_heard_total, seen_callsigns, callsign or None, packet_timestamp)


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
