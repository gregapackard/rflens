from __future__ import annotations

import re
import time
import math
from pathlib import Path
from typing import Any, Iterator

from backend.config import resolve_path, source_config
from backend.config import load_config
from backend.db import (
    fetch_recent_aprs_callsigns,
    get_or_create_source,
    hydrate_aprs_status_from_recent_events,
    insert_event,
    reset_aprs_status,
    touch_source,
    update_recent_aprs_gate_confirmation,
    update_aprs_status,
    utc_now,
)


APRS_CALLSIGN = "KF8GBU-10"
OWN_CALLSIGNS = {APRS_CALLSIGN}
DUPLICATE_WINDOW_SECONDS = 60
EARTH_RADIUS_KM = 6371.0088
CALLSIGN_RE = re.compile(r"^[A-Z0-9]{1,9}(?:-[0-9]{1,2})?$", re.IGNORECASE)
POSITION_RE = re.compile(r"(\d{2})(\d{2}\.\d{2})([NS]).*?(\d{3})(\d{2}\.\d{2})([EW])")
AUDIO_LEVEL_RE = re.compile(r"audio level\s*=\s*(\d+)\(([^)]*)\)", re.IGNORECASE)
ESCAPED_BINARY_RE = re.compile(r"<0x[0-9a-f]{2}>", re.IGNORECASE)
IGATE_SERVER_RE = re.compile(r"Now connected to IGate server\s+(\S+)\s+\([^)]+\)", re.IGNORECASE)
RF_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)?$")
FREQUENCY_RE = re.compile(r"(?<!\d)(1[0-9]{2}\.\d{3})\s*MHz", re.IGNORECASE)
TONE_RE = re.compile(r"\bT(?:one)?\s*([0-9]{2,3}(?:\.[0-9])?)\b", re.IGNORECASE)
OFFSET_RE = re.compile(r"(?<!\d)([+-]\d{3,4})(?!\d)")
AIRCRAFT_SOURCE_RE = re.compile(r"^N[0-9]{1,5}[A-Z]{0,2}(?:-\d{1,2})?$", re.IGNORECASE)
WIDE_ALIAS_RE = re.compile(r"^WIDE\d*(?:-\d+)?\*?$", re.IGNORECASE)
Q_CONSTRUCT_RE = re.compile(r",qA[A-Z],([A-Z0-9]{1,9}(?:-[0-9]{1,2})?)", re.IGNORECASE)
NETWORK_PATH_RE = re.compile(r"(?:^|,)(?:TCPIP\*?|qA[A-Z])(?:,|$)", re.IGNORECASE)
POSITION_MOTION_RE = re.compile(r"\d{3}/\d{3}")
DEFAULT_DIRECT_RF_QUESTIONABLE_MILES = 300
DEFAULT_DIGIPEATED_RF_QUESTIONABLE_MILES = 700
SSID_HINTS = {
    1: "possible_digipeater_or_secondary",
    3: "likely_weather",
    7: "likely_handheld",
    9: "likely_mobile",
    10: "likely_igate_or_internet",
    11: "possible_balloon_or_aircraft",
    15: "possible_hf_or_misc",
}
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


def parse_audio_line(text: str) -> dict[str, Any] | None:
    match = AUDIO_LEVEL_RE.search(text)
    if not match:
        return None
    callsign_match = re.match(r"\s*([A-Z0-9]{1,9}(?:-[0-9]{1,2})?)\s+audio level", text, re.IGNORECASE)
    bar = text[match.end():].strip()
    return {
        "level": int(match.group(1)),
        "quality": match.group(2).strip(),
        "bar": bar or None,
        "source_callsign": callsign_match.group(1).upper() if callsign_match else None,
        "timestamp": utc_now(),
    }


def parse_igate_status(text: str) -> dict[str, Any]:
    lowered = text.lower()
    fields: dict[str, Any] = {
        "online": True,
        "aprs_is_connected": True,
        "last_igate_status_at": utc_now(),
    }
    if f"logresp {APRS_CALLSIGN.lower()} verified" in lowered:
        fields["aprs_is_verified"] = True
    server_match = IGATE_SERVER_RE.search(text)
    if server_match:
        fields["aprs_is_server"] = server_match.group(1)
    return fields


def parse_gate_confirmation(prefix: str | None, text: str) -> dict[str, Any] | None:
    parsed_header = parse_packet_header(text)
    if not parsed_header:
        return None
    callsign, _destination, _path = parsed_header
    q_match = Q_CONSTRUCT_RE.search(text)
    if q_match:
        gated_by = q_match.group(1).upper()
        return {
            "callsign": callsign,
            "gated_by": gated_by,
            "confirmed_gated_by_me": gated_by == APRS_CALLSIGN,
        }
    if prefix and prefix.lower() == "ig>tx":
        return {
            "callsign": callsign,
            "gated_by": APRS_CALLSIGN,
            "confirmed_gated_by_me": True,
        }
    return None


def aprs_is_status_line(text: str) -> bool:
    lowered = text.lower()
    return "logresp" in lowered or "now connected" in lowered or "connected to" in lowered


def parse_packet_header(text: str) -> tuple[str, str, str] | None:
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
    return source.upper(), destination.upper(), path.strip()


def parse_callsign_ssid(callsign: str | None) -> tuple[str | None, int | None]:
    if not callsign:
        return None, None
    text = callsign.upper()
    if "-" not in text:
        return text, None
    base, suffix = text.rsplit("-", 1)
    try:
        return base, int(suffix)
    except ValueError:
        return base, None


def ssid_hint(ssid: int | None) -> str | None:
    return SSID_HINTS.get(ssid)


def packet_payload(text: str) -> str:
    if ":" not in text:
        return ""
    return text.split(":", 1)[1]


def non_aprs_ax25_packet(text: str, destination: str) -> bool:
    if destination.upper() == "NODES":
        return True
    return len(ESCAPED_BINARY_RE.findall(packet_payload(text))) >= 2


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
    callsign, destination, _path = parsed_header
    if callsign in OWN_CALLSIGNS:
        return False
    return not non_aprs_ax25_packet(text, destination)


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


def parse_path(path_raw: str | None) -> list[str]:
    if not path_raw:
        return []
    parts = [part.strip().upper() for part in path_raw.split(",") if part.strip()]
    return parts[1:] if parts else []


def last_used_digipeater(path: list[str]) -> str | None:
    used = [part for part in path if part.endswith("*")]
    return used[-1].rstrip("*") if used else None


def clean_path_token(value: Any) -> str:
    return str(value or "").strip().upper().rstrip("*")


def is_wide_alias(value: Any) -> bool:
    return bool(WIDE_ALIAS_RE.match(str(value or "").strip()))


def preferred_heard_via(path: list[str], heard_via: str | None, was_direct: bool) -> str | None:
    raw = clean_path_token(heard_via)
    if not raw:
        return "direct" if was_direct else None
    if not is_wide_alias(raw):
        return raw
    for token in reversed(path):
        clean = clean_path_token(token)
        if clean and clean != raw and not is_wide_alias(clean):
            return clean
    return raw


def network_path_seen(path_raw: str | None, prefix: str | None) -> bool:
    return bool((prefix and prefix.lower().startswith("ig")) or NETWORK_PATH_RE.search(str(path_raw or "")))


def heard_category(prefix: str | None, path_raw: str | None, was_direct: bool, was_digipeated: bool) -> str:
    if network_path_seen(path_raw, prefix):
        return "aprs_is"
    if prefix and RF_PREFIX_RE.match(prefix):
        if was_direct and not was_digipeated:
            return "direct_rf"
        if was_digipeated:
            return "digipeated_rf"
    return "unknown"


def distance_quality(distance_miles: Any, category: str, cfg: dict[str, Any]) -> str:
    try:
        miles = float(distance_miles)
    except (TypeError, ValueError):
        return "unknown"
    if miles < 0:
        return "questionable"
    direct_limit = float(cfg.get("direct_rf_questionable_miles", DEFAULT_DIRECT_RF_QUESTIONABLE_MILES))
    digi_limit = float(cfg.get("digipeated_rf_questionable_miles", DEFAULT_DIGIPEATED_RF_QUESTIONABLE_MILES))
    if category == "direct_rf" and miles > direct_limit:
        return "questionable"
    if category == "digipeated_rf" and miles > digi_limit:
        return "questionable"
    if category in {"direct_rf", "digipeated_rf"} and miles > 150:
        return "long_range"
    return "normal"


def valid_coord(lat: Any, lon: Any) -> bool:
    try:
        latitude = float(lat)
        longitude = float(lon)
    except (TypeError, ValueError):
        return False
    return abs(latitude) <= 90 and abs(longitude) <= 180 and not (latitude == 0 and longitude == 0)


def range_and_bearing(
    station_lat: float | None,
    station_lon: float | None,
    packet_lat: float | None,
    packet_lon: float | None,
) -> dict[str, float]:
    if not valid_coord(station_lat, station_lon) or not valid_coord(packet_lat, packet_lon):
        return {}
    phi_a = math.radians(float(station_lat))
    phi_b = math.radians(float(packet_lat))
    delta_phi = math.radians(float(packet_lat) - float(station_lat))
    delta_lambda = math.radians(float(packet_lon) - float(station_lon))
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    distance_km = EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    y = math.sin(delta_lambda) * math.cos(phi_b)
    x = math.cos(phi_a) * math.sin(phi_b) - math.sin(phi_a) * math.cos(phi_b) * math.cos(delta_lambda)
    return {
        "distance_km": round(distance_km, 2),
        "distance_miles": round(distance_km * 0.621371, 2),
        "distance_nmi": round(distance_km * 0.539957, 2),
        "bearing_degrees": round((math.degrees(math.atan2(y, x)) + 360) % 360, 1),
    }


def packet_comment(payload: str) -> str:
    if payload.startswith(";") and len(payload) > 37:
        return payload[37:].strip()
    if len(payload) > 19 and payload[0] in "!=/@":
        return payload[19:].strip()
    return payload.strip()


def parse_repeater_object(payload: str) -> dict[str, Any]:
    if not payload.startswith(";"):
        return {}
    name = payload[1:10].strip()
    comment = packet_comment(payload)
    frequency = FREQUENCY_RE.search(payload)
    tone = TONE_RE.search(payload)
    offset = OFFSET_RE.search(payload)
    data: dict[str, Any] = {
        "object_name": name or None,
        "comment": comment,
    }
    if frequency:
        data["frequency_mhz"] = float(frequency.group(1))
    if tone:
        data["tone_hz"] = float(tone.group(1))
    if offset:
        data["offset_khz"] = int(offset.group(1)) * 10
    return {key: value for key, value in data.items() if value not in (None, "")}


def infer_station_type(source: str | None, destination: str | None, payload: str, comment: str, raw_text: str, ssid: int | None) -> tuple[str, str]:
    text = f"{payload} {comment} {raw_text}".lower()
    if (destination or "").upper() == "NODES":
        return "ax25_node", "high"
    if (destination or "").upper() == "ID" or "network node" in text:
        return "packet_node", "high"
    if payload.startswith(";") and FREQUENCY_RE.search(payload):
        return "repeater_object", "high"
    if "digi igate" in text or "i-gate" in text or "igate" in text:
        return "igate", "high"
    if "digi" in text or "digipeater" in text:
        return "digipeater", "high"
    if "small aircraft" in text or (source and AIRCRAFT_SOURCE_RE.match(source)):
        return "aircraft", "high"
    if "weather" in text or payload.startswith("_"):
        return "weather", "high"
    if ssid == 9 and POSITION_MOTION_RE.search(payload):
        return "mobile", "medium"
    if ssid == 11:
        return "aircraft", "low"
    if ssid == 9:
        return "mobile", "low"
    if ssid == 7:
        return "handheld", "low"
    if ssid == 3:
        return "weather", "low"
    if ssid == 10:
        return "likely_igate", "low"
    if ssid == 1:
        return "possible_digipeater", "low"
    return "station", "low"


def enrich_packet(
    line: str,
    last_audio: dict[str, Any] | None,
    station_cfg: dict[str, Any],
    gate_eligible: bool,
) -> dict[str, Any]:
    prefix, text = split_prefix(line)
    parsed_header = parse_packet_header(text)
    callsign, destination, path_raw = parsed_header if parsed_header else (None, None, None)
    base_callsign, ssid = parse_callsign_ssid(callsign)
    path = parse_path(path_raw)
    used_digi = last_used_digipeater(path)
    was_digipeated = bool(used_digi)
    was_direct = not was_digipeated
    heard_via_raw = used_digi or "direct"
    payload = packet_payload(text)
    comment = packet_comment(payload)
    lat, lon = parse_position(text)
    station_type, station_type_confidence = infer_station_type(callsign, destination, payload, comment, text, ssid)
    heard_over_rf = bool(prefix and RF_PREFIX_RE.match(prefix))
    category = heard_category(prefix, path_raw, was_direct, was_digipeated)
    preferred_via = None if category == "aprs_is" else preferred_heard_via(path, used_digi, was_direct)
    distances = range_and_bearing(station_cfg.get("lat"), station_cfg.get("lon"), lat, lon)
    metadata: dict[str, Any] = {
        "callsign": callsign,
        "source_callsign": callsign,
        "base_callsign": base_callsign,
        "ssid": ssid,
        "ssid_hint": ssid_hint(ssid),
        "destination": destination,
        "path_raw": path_raw,
        "path": path,
        "was_digipeated": was_digipeated,
        "was_direct": was_direct,
        "last_used_digipeater": used_digi,
        "heard_via": heard_via_raw,
        "heard_via_raw": heard_via_raw,
        "preferred_heard_via": preferred_via,
        "heard_transport": "rf" if heard_over_rf else "unknown",
        "heard_over_rf": heard_over_rf,
        "heard_category": category,
        "direct_rf_heard": category == "direct_rf",
        "digipeated_rf_heard": category == "digipeated_rf",
        "network_seen": category == "aprs_is",
        "gate_eligible": bool(heard_over_rf and gate_eligible),
        "likely_gated_by_me": bool(heard_over_rf and gate_eligible),
        "confirmed_gated_by_me": False,
        "rf_channel_prefix": prefix if prefix and RF_PREFIX_RE.match(prefix) else None,
        "station_type": station_type,
        "station_type_confidence": station_type_confidence,
        "payload": payload,
        "comment": comment,
        "lat": lat,
        "lon": lon,
        "line": text,
    }
    if station_type in {"packet_node", "ax25_node"}:
        metadata["is_packet_node"] = True
        metadata["importance"] = "low"
    metadata.update(parse_repeater_object(payload))
    metadata.update(distances)
    metadata["distance_quality"] = distance_quality(metadata.get("distance_miles"), category, station_cfg)
    if last_audio:
        metadata.update(
            {
                "audio_level": last_audio.get("level"),
                "audio_quality": last_audio.get("quality"),
                "audio_bar": last_audio.get("bar"),
                "audio_source_callsign": last_audio.get("source_callsign"),
                "audio_timestamp": last_audio.get("timestamp"),
            }
        )
    return {key: value for key, value in metadata.items() if value is not None}


def parse_line(line: str) -> dict[str, str | float | None]:
    text = normalize_packet_line(line)
    parsed_header = parse_packet_header(text)
    callsign, destination, _path = parsed_header if parsed_header else (None, None, None)
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
    full_cfg = load_config()
    cfg = source_config("aprs", full_cfg)
    station_cfg = full_cfg.get("station", {}) or {}
    source_id = get_or_create_source(
        cfg.get("name", "APRS Direwolf"),
        "aprs",
        cfg.get("device_index"),
        cfg.get("frequency"),
    )
    path = resolve_path(cfg.get("log_path", "./data/direwolf.log"))
    seen_packets: dict[str, float] = {}
    seen_callsigns: set[str] = set(fetch_recent_aprs_callsigns())
    rf_packets_heard_total = 0
    ignored_igate_lines = 0
    ignored_status_lines = 0
    best_audio_level: int | None = None
    last_audio: dict[str, Any] | None = None
    aprs_is_connected = False
    aprs_is_verified = False
    reset_aprs_status(APRS_CALLSIGN)
    hydrate_aprs_status_from_recent_events(APRS_CALLSIGN)

    for line in follow(path):
        if not line:
            touch_source(source_id, "missing" if not path.exists() else "online")
            continue
        prefix, text = split_prefix(line)
        if ignored_prefix(prefix):
            ignored_igate_lines += 1
            confirmation = parse_gate_confirmation(prefix, text)
            if confirmation:
                update_recent_aprs_gate_confirmation(raw_text=text, **confirmation)
            fields = parse_igate_status(text)
            aprs_is_connected = bool(fields.get("aprs_is_connected", aprs_is_connected))
            aprs_is_verified = bool(fields.get("aprs_is_verified", aprs_is_verified))
            fields["ignored_igate_lines"] = ignored_igate_lines
            update_aprs_status(**fields)
            touch_source(source_id, "online")
            continue
        audio_status = parse_audio_line(text)
        if audio_status:
            ignored_status_lines += 1
            level = int(audio_status["level"])
            quality = str(audio_status["quality"])
            last_audio = audio_status
            update_audio_metrics(level, quality, best_audio_level, ignored_status_lines)
            if best_audio_level is None or level > best_audio_level:
                best_audio_level = level
            touch_source(source_id, "online")
            continue
        if status_or_help_line(text):
            ignored_status_lines += 1
            if aprs_is_status_line(text):
                fields = parse_igate_status(text)
                aprs_is_connected = bool(fields.get("aprs_is_connected", aprs_is_connected))
                aprs_is_verified = bool(fields.get("aprs_is_verified", aprs_is_verified))
                fields["ignored_status_lines"] = ignored_status_lines
                update_aprs_status(**fields)
            else:
                update_ignored_status(ignored_status_lines)
            touch_source(source_id, "online")
            continue
        if not packet_like(line):
            continue
        parsed = enrich_packet(line, last_audio, station_cfg, aprs_is_connected and aprs_is_verified)
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
