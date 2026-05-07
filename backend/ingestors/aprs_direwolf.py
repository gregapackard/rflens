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
    update_aprs_event_metadata,
    update_recent_aprs_gate_confirmation,
    update_aprs_status,
    utc_now,
)


APRS_CALLSIGN = "KF8GBU-10"
OWN_CALLSIGNS = {APRS_CALLSIGN}
DUPLICATE_WINDOW_SECONDS = 60
SOURCE_TOUCH_INTERVAL_SECONDS = 15
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
DECODED_TRAILING_HEMI_POSITION_RE = re.compile(
    r"(?P<lat_deg>\d{1,2})[^\dA-Z]+(?P<lat_min>\d{1,2}\.\d+)\s*(?P<lat_hemi>[NS])"
    r".*?"
    r"(?P<lon_deg>\d{1,3})[^\dA-Z]+(?P<lon_min>\d{1,2}\.\d+)\s*(?P<lon_hemi>[EW])",
    re.IGNORECASE,
)
DECODED_PREFIX_HEMI_POSITION_RE = re.compile(
    r"^\s*(?P<lat_hemi>[NS])\s+"
    r"(?P<lat_deg>\d{1,2})\s+"
    r"(?P<lat_min>\d{1,2}\.\d+)\s*,\s*"
    r"(?P<lon_hemi>[EW])\s+"
    r"(?P<lon_deg>\d{1,3})\s+"
    r"(?P<lon_min>\d{1,2}\.\d+)\b",
    re.IGNORECASE,
)
SPEED_RE = re.compile(r"\b(?:speed\s*)?(?P<speed>\d{1,3}(?:\.\d+)?)\s*(?P<unit>mph|knots?|kts?|kt)\b", re.IGNORECASE)
COURSE_RE = re.compile(r"\b(?:course|bearing|cog)\s*[=: ]\s*(?P<course>\d{1,3})\b", re.IGNORECASE)
ALTITUDE_RE = re.compile(r"\b(?:alt(?:itude)?\s*[=: ]\s*)?(?P<altitude>-?\d{1,6})\s*(?:ft|feet)\b", re.IGNORECASE)
MIC_E_STATUS_RE = re.compile(r"\b(?:mic-?e|status)\b[^:]*[:=,]\s*(?P<status>[^,;]+)", re.IGNORECASE)
MANUFACTURER_RE = re.compile(r"\b(?:manufacturer|device|radio|modem)\s*[=:]\s*(?P<manufacturer>[^,;]+)", re.IGNORECASE)
DECODED_KEYWORDS = (
    "mic-e",
    "mice",
    "latitude",
    "longitude",
    "course",
    "speed",
    "altitude",
    "manufacturer",
    "device",
    "symbol",
)
IGNORED_FOLLOWUP_PHRASES = (
    "tell the sender",
    "use of id",
    "error!!!",
)
RECENT_PACKET_FOLLOWUP_SECONDS = 4
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


def followup_boundary(line: str) -> bool:
    prefix, text = split_prefix(line)
    if not text:
        return True
    return bool(
        ignored_prefix(prefix)
        or parse_audio_line(text)
        or status_or_help_line(text)
        or packet_like(line)
    )


def configured_aprs_callsign(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_config()
    station_cfg = cfg.get("station", {}) or {}
    aprs_cfg = source_config("aprs", cfg)
    return str(
        aprs_cfg.get("callsign")
        or station_cfg.get("aprs_callsign")
        or station_cfg.get("callsign")
        or APRS_CALLSIGN
    ).upper()


def parse_igate_status(text: str, local_callsign: str = APRS_CALLSIGN) -> dict[str, Any]:
    lowered = text.lower()
    fields: dict[str, Any] = {
        "online": True,
        "aprs_is_connected": True,
        "last_igate_status_at": utc_now(),
    }
    if f"logresp {local_callsign.lower()} verified" in lowered:
        fields["aprs_is_verified"] = True
    server_match = IGATE_SERVER_RE.search(text)
    if server_match:
        fields["aprs_is_server"] = server_match.group(1)
    return fields


def parse_gate_confirmation(prefix: str | None, text: str, local_callsign: str = APRS_CALLSIGN) -> dict[str, Any] | None:
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
            "confirmed_gated_by_me": str(gated_by).strip().upper() == str(local_callsign).strip().upper(),
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


def parse_decoded_position(text: str) -> tuple[float | None, float | None]:
    match = DECODED_PREFIX_HEMI_POSITION_RE.search(text) or DECODED_TRAILING_HEMI_POSITION_RE.search(text)
    if match:
        lat = int(match.group("lat_deg")) + float(match.group("lat_min")) / 60
        lon = int(match.group("lon_deg")) + float(match.group("lon_min")) / 60
        if match.group("lat_hemi").upper() == "S":
            lat *= -1
        if match.group("lon_hemi").upper() == "W":
            lon *= -1
        return lat, lon
    return None, None


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


def parse_decoded_followup_line(text: str) -> dict[str, Any] | None:
    prefix, line = split_prefix(text)
    if not line:
        return None
    if ignored_prefix(prefix):
        return None
    lowered = line.lower()
    if parse_packet_header(line) or parse_audio_line(line) or aprs_is_status_line(line) or status_or_help_line(line):
        return None
    if any(phrase in lowered for phrase in IGNORED_FOLLOWUP_PHRASES):
        return None
    lat, lon = parse_decoded_position(line)
    decoded: dict[str, Any] = {}
    if lat is not None and lon is not None:
        decoded["decoded_lat"] = round(lat, 6)
        decoded["decoded_lon"] = round(lon, 6)
    speed_match = SPEED_RE.search(line)
    if speed_match:
        speed = float(speed_match.group("speed"))
        unit = speed_match.group("unit").lower()
        if unit in {"kt", "kts", "knot", "knots"}:
            decoded["speed_knots"] = speed
            decoded["speed_mph"] = round(speed * 1.15078, 2)
        else:
            decoded["speed_mph"] = speed
            decoded["speed_knots"] = round(speed / 1.15078, 2)
    course_match = COURSE_RE.search(line)
    if course_match:
        decoded["course_degrees"] = int(course_match.group("course")) % 360
    altitude_match = ALTITUDE_RE.search(line)
    if altitude_match:
        decoded["altitude_ft"] = int(altitude_match.group("altitude"))
    status_match = MIC_E_STATUS_RE.search(line)
    if status_match:
        decoded["mic_e_status"] = status_match.group("status").strip()
    manufacturer_match = MANUFACTURER_RE.search(line)
    if manufacturer_match:
        decoded["manufacturer"] = manufacturer_match.group("manufacturer").strip()
    if "symbol" in lowered:
        symbol_match = re.search(r"\bsymbol\s*[=:]\s*([^,;]+)", line, re.IGNORECASE)
        if symbol_match:
            decoded["symbol"] = symbol_match.group(1).strip()
    if not decoded and not any(keyword in lowered for keyword in DECODED_KEYWORDS):
        return None
    decoded["decoded_followup_line"] = line
    return decoded


def metadata_is_mic_e(metadata: dict[str, Any]) -> bool:
    payload = str(metadata.get("payload") or "")
    decoded_text = str(metadata.get("direwolf_decoded_text") or "")
    return bool(payload[:1] in {"`", "'"} or "mic-e" in decoded_text.lower())


def decoded_followup_metadata(
    line: str,
    existing_metadata: dict[str, Any],
    station_cfg: dict[str, Any],
) -> tuple[dict[str, Any], float | None, float | None] | None:
    decoded = parse_decoded_followup_line(line)
    if not decoded:
        return None
    followup_lines = list(existing_metadata.get("decoded_followup_lines") or [])
    raw_line = decoded.pop("decoded_followup_line")
    if raw_line not in followup_lines:
        followup_lines.append(raw_line)
    is_mic_e = metadata_is_mic_e(existing_metadata) or "mic-e" in raw_line.lower()
    original_has_position = existing_metadata.get("lat") is not None and existing_metadata.get("lon") is not None
    if original_has_position and not is_mic_e:
        decoded.pop("decoded_lat", None)
        decoded.pop("decoded_lon", None)
    updates: dict[str, Any] = {
        "decoded_followup_lines": followup_lines[-8:],
        "direwolf_decoded_text": " ".join(followup_lines[-8:]),
        **decoded,
    }
    lat = decoded.get("decoded_lat")
    lon = decoded.get("decoded_lon")
    update_lat = float(lat) if lat is not None and existing_metadata.get("lat") is None and valid_coord(lat, lon) else None
    update_lon = float(lon) if lon is not None and existing_metadata.get("lon") is None and valid_coord(lat, lon) else None
    if update_lat is not None and update_lon is not None:
        category = str(existing_metadata.get("heard_category") or "unknown")
        distances = range_and_bearing(station_cfg.get("lat"), station_cfg.get("lon"), update_lat, update_lon)
        quality = distance_quality(distances.get("distance_miles"), category, station_cfg)
        if quality == "questionable":
            updates.pop("decoded_lat", None)
            updates.pop("decoded_lon", None)
            update_lat = None
            update_lon = None
        else:
            updates.update(distances)
            updates["distance_quality"] = quality
    return updates, update_lat, update_lon


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


def update_ignored_igate(count: int, text: str, local_callsign: str = APRS_CALLSIGN) -> None:
    fields = parse_igate_status(text, local_callsign)
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


def touch_source_if_due(source_id: int, status: str, last_touch_at: float, *, force: bool = False) -> float:
    now = time.time()
    if force or now - last_touch_at >= SOURCE_TOUCH_INTERVAL_SECONDS:
        touch_source(source_id, status)
        return now
    return last_touch_at


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
    local_callsign = configured_aprs_callsign(full_cfg)
    OWN_CALLSIGNS.add(local_callsign)
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
    recent_packet: dict[str, Any] | None = None
    last_source_touch_at = 0.0
    reset_aprs_status(local_callsign)
    hydrated_status = hydrate_aprs_status_from_recent_events(local_callsign)
    rf_packets_heard_total = int(hydrated_status.get("rf_packets_heard_total") or 0)

    for line in follow(path):
        if not line:
            recent_packet = None
            last_source_touch_at = touch_source_if_due(
                source_id,
                "missing" if not path.exists() else "online",
                last_source_touch_at,
            )
            continue
        prefix, text = split_prefix(line)
        if recent_packet and followup_boundary(line):
            recent_packet = None
        if ignored_prefix(prefix):
            ignored_igate_lines += 1
            confirmation = parse_gate_confirmation(prefix, text, local_callsign)
            if confirmation:
                update_recent_aprs_gate_confirmation(raw_text=text, **confirmation)
            fields = parse_igate_status(text, local_callsign)
            aprs_is_connected = bool(fields.get("aprs_is_connected", aprs_is_connected))
            aprs_is_verified = bool(fields.get("aprs_is_verified", aprs_is_verified))
            fields["ignored_igate_lines"] = ignored_igate_lines
            update_aprs_status(**fields)
            last_source_touch_at = touch_source_if_due(source_id, "online", last_source_touch_at)
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
            last_source_touch_at = touch_source_if_due(source_id, "online", last_source_touch_at)
            continue
        if recent_packet and time.time() - float(recent_packet.get("seen_at") or 0) <= RECENT_PACKET_FOLLOWUP_SECONDS:
            decoded = decoded_followup_metadata(text, recent_packet.get("metadata") or {}, station_cfg)
            if decoded:
                metadata_updates, update_lat, update_lon = decoded
                if update_aprs_event_metadata(
                    event_id=int(recent_packet["id"]),
                    metadata_updates=metadata_updates,
                    lat=update_lat,
                    lon=update_lon,
                ):
                    recent_packet["metadata"].update(metadata_updates)
                    if update_lat is not None:
                        recent_packet["metadata"]["lat"] = update_lat
                    if update_lon is not None:
                        recent_packet["metadata"]["lon"] = update_lon
                    last_source_touch_at = touch_source_if_due(source_id, "online", last_source_touch_at)
                    line_lat, line_lon = parse_decoded_position(text)
                    if (
                        (update_lat is not None and update_lon is not None)
                        or (line_lat is not None and line_lon is not None)
                        or any(key in metadata_updates for key in ("speed_mph", "speed_knots", "course_degrees", "altitude_ft"))
                    ):
                        recent_packet = None
                    continue
        if status_or_help_line(text):
            ignored_status_lines += 1
            if aprs_is_status_line(text):
                fields = parse_igate_status(text, local_callsign)
                aprs_is_connected = bool(fields.get("aprs_is_connected", aprs_is_connected))
                aprs_is_verified = bool(fields.get("aprs_is_verified", aprs_is_verified))
                fields["ignored_status_lines"] = ignored_status_lines
                update_aprs_status(**fields)
            else:
                update_ignored_status(ignored_status_lines)
            last_source_touch_at = touch_source_if_due(source_id, "online", last_source_touch_at)
            continue
        if not packet_like(line):
            recent_packet = None
            continue
        parsed = enrich_packet(line, last_audio, station_cfg, aprs_is_connected and aprs_is_verified)
        packet_line = str(parsed.get("line") or "")
        if duplicate_packet(packet_line, seen_packets, time.time()):
            continue
        event_id = insert_event(
            source_id=source_id,
            event_type="aprs_packet",
            callsign=parsed.get("callsign"),
            lat=parsed.get("lat"),
            lon=parsed.get("lon"),
            raw_text=line,
            metadata=parsed,
        )
        recent_packet = {
            "id": event_id,
            "metadata": dict(parsed),
            "seen_at": time.time(),
        }
        packet_timestamp = utc_now()
        rf_packets_heard_total += 1
        last_source_touch_at = touch_source_if_due(source_id, "online", last_source_touch_at)
        callsign = str(parsed.get("callsign") or "")
        if callsign:
            seen_callsigns.add(callsign)
        update_rf_metrics(rf_packets_heard_total, seen_callsigns, callsign or None, packet_timestamp)


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
