from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterator

from backend.config import resolve_path, source_config
from backend.db import get_or_create_source, insert_event, touch_source


CALLSIGN_PATTERN = r"(?=[A-Z0-9-]*[A-Z])[A-Z0-9]{1,9}(?:-(?:[0-9]|1[0-5]))?"
PACKET_RE = re.compile(
    rf"^(?P<callsign>{CALLSIGN_PATTERN})>(?P<destination>[^,:\s]+)(?:,[^:]*)?:(?P<body>.*)$",
    re.IGNORECASE,
)
POSITION_RE = re.compile(r"(\d{2})(\d{2}\.\d{2})([NS]).*?(\d{3})(\d{2}\.\d{2})([EW])")
STATUS_PREFIXES = (
    "#",
    "Position,",
    "Ready to accept",
    "Now connected",
    "Check server status",
)
STATUS_PHRASES = (
    "When using APRS",
    "Dire Wolf",
    "Direwolf",
)


def normalize_packet_line(line: str) -> str:
    text = line.strip()
    if text.startswith("[") and "]" in text:
        text = text.split("]", 1)[1].strip()
    return text


def status_or_help_line(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in STATUS_PREFIXES) or any(phrase in text for phrase in STATUS_PHRASES)


def packet_like(line: str) -> bool:
    text = normalize_packet_line(line)
    if len(text) < 6:
        return False
    if status_or_help_line(text):
        return False
    return PACKET_RE.match(text) is not None


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
    match = PACKET_RE.match(text)
    callsign = match.group("callsign").upper() if match else None
    destination = match.group("destination").upper() if match else None
    lat, lon = parse_position(text)
    return {
        "callsign": callsign,
        "destination": destination,
        "lat": lat,
        "lon": lon,
        "line": text,
    }


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

    for line in follow(path):
        if not line:
            touch_source(source_id, "missing" if not path.exists() else "online")
            continue
        if not packet_like(line):
            continue
        parsed = parse_line(line)
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
