from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterator

from backend.config import resolve_path, source_config
from backend.db import get_or_create_source, insert_event, touch_source


OWN_CALLSIGNS = {"KF8GBU-10"}
DUPLICATE_WINDOW_SECONDS = 60
CALLSIGN_RE = re.compile(r"^[A-Z0-9]{1,9}(?:-[0-9]{1,2})?$", re.IGNORECASE)
POSITION_RE = re.compile(r"(\d{2})(\d{2}\.\d{2})([NS]).*?(\d{3})(\d{2}\.\d{2})([EW])")
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

    for line in follow(path):
        if not line:
            touch_source(source_id, "missing" if not path.exists() else "online")
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


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
