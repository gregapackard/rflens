from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

try:
    from check_setup import CONFIG_PATH, EXAMPLE_CONFIG_PATH, load_yaml, resolve_path, value_present
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_setup import CONFIG_PATH, EXAMPLE_CONFIG_PATH, load_yaml, resolve_path, value_present


def line(label: str, value: str) -> None:
    print(f"{label}: {value}")


def bool_text(value: bool) -> str:
    return "yes" if value else "no"


def path_status(path_value: Any) -> tuple[Path | None, bool]:
    if not value_present(path_value):
        return None, False
    path = resolve_path(str(path_value))
    return path, path.exists()


def directory_writable(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        with tempfile.NamedTemporaryFile(prefix=".rflens-source-doctor-", dir=path, delete=False) as handle:
            temp_path = Path(handle.name)
        temp_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def event_count(database_path: Path, event_type: str) -> int | None:
    if not database_path.exists():
        return None
    try:
        with sqlite3.connect(database_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM events WHERE event_type = ?", (event_type,)).fetchone()
    except sqlite3.Error:
        return None
    return int(row[0] if row else 0)


def api_urls(host: Any, port: Any) -> list[str]:
    host_text = str(host or "0.0.0.0").strip()
    port_text = str(port or 8080).strip()
    candidates = []
    if host_text and host_text not in {"0.0.0.0", "::"}:
        candidates.append(f"http://{host_text}:{port_text}/api/health")
    candidates.append(f"http://localhost:{port_text}/api/health")
    return list(dict.fromkeys(candidates))


def api_reachable(urls: list[str]) -> tuple[bool, str]:
    for url in urls:
        try:
            with urlopen(url, timeout=2) as response:
                if 200 <= response.status < 300:
                    return True, url
        except (OSError, URLError):
            continue
    return False, urls[-1] if urls else "not configured"


def enabled_text(source: dict[str, Any]) -> str:
    return "enabled" if source.get("enabled") else "disabled"


def first_configured(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def placeholder_callsign(value: str) -> bool:
    normalized = value.strip().upper()
    return normalized in {"N0CALL", "NOCALL"} or normalized.startswith("N0CALL-") or normalized.startswith("NOCALL-")


def main() -> int:
    suggestions: list[str] = []

    if CONFIG_PATH.exists():
        cfg = load_yaml(CONFIG_PATH)
        line("config.yaml", f"found ({CONFIG_PATH})")
    elif EXAMPLE_CONFIG_PATH.exists():
        cfg = load_yaml(EXAMPLE_CONFIG_PATH)
        line("config.yaml", "missing; using config.example.yaml for this report")
        suggestions.append("Run bash ./scripts/setup.sh, then edit config.yaml for your station.")
    else:
        line("config.yaml", "missing")
        line("next suggested action", "Run bash ./scripts/setup.sh from the repo root.")
        return 1

    database_path = resolve_path(str(cfg.get("database_path") or "./data/rflens.db"))
    db_parent = database_path.parent
    db_parent_writable = directory_writable(db_parent)
    line("database path", str(database_path))
    line("database parent writable", bool_text(db_parent_writable))
    if not db_parent_writable:
        suggestions.append(f"Create or fix permissions for the database directory: {db_parent}")

    server = cfg.get("server", {}) or {}
    urls = api_urls(server.get("host"), server.get("port"))
    reachable, reached_url = api_reachable(urls)
    line("API reachable", f"{bool_text(reachable)} ({reached_url})")
    if not reachable:
        suggestions.append("Start the API with bash ./scripts/run_api.sh, then rerun this script.")

    sources = cfg.get("sources", {}) or {}
    aprs = sources.get("aprs", {}) or {}
    station = cfg.get("station", {}) or {}
    station_callsign = str(station.get("callsign") or "").strip()
    aprs_callsign = first_configured(
        aprs.get("callsign"),
        aprs.get("igate_callsign"),
        station.get("aprs_callsign"),
        station.get("callsign"),
        "NOCALL",
    )
    line("station callsign", station_callsign or "missing")
    line("effective APRS callsign", aprs_callsign)
    placeholder = placeholder_callsign(station_callsign) or placeholder_callsign(aprs_callsign)
    line("N0CALL/NOCALL placeholder present", bool_text(placeholder))
    if placeholder:
        suggestions.append("Replace N0CALL/NOCALL placeholders in config.yaml with your station callsign.")

    line("APRS source", enabled_text(aprs))
    aprs_log, aprs_log_exists = path_status(aprs.get("log_path"))
    line("APRS log path", f"{aprs_log or 'missing'} ({'exists' if aprs_log_exists else 'missing'})")
    aprs_count = event_count(database_path, "aprs_packet")
    line("APRS recent event count in SQLite", "unknown" if aprs_count is None else str(aprs_count))
    if not aprs.get("enabled"):
        suggestions.append("Enable sources.aprs in config.yaml if you want APRS data.")
    elif not aprs_log_exists:
        suggestions.append("Point sources.aprs.log_path at your Direwolf text log.")
    elif aprs_count == 0:
        suggestions.append("Run the APRS ingestor with bash ./scripts/run_all.sh.")

    adsb = sources.get("adsb", {}) or {}
    line("ADS-B source", enabled_text(adsb))
    adsb_json, adsb_json_exists = path_status(adsb.get("aircraft_json_path"))
    line("ADS-B aircraft_json_path", f"{adsb_json or 'missing'} ({'exists' if adsb_json_exists else 'missing'})")
    adsb_count = event_count(database_path, "adsb_aircraft")
    line("ADS-B recent event count in SQLite", "unknown" if adsb_count is None else str(adsb_count))
    if not adsb.get("enabled"):
        suggestions.append("Enable sources.adsb in config.yaml if you want ADS-B data.")
    elif not adsb_json_exists:
        suggestions.append("Point sources.adsb.aircraft_json_path at readsb aircraft.json.")
    elif adsb_count == 0:
        suggestions.append("Run the ADS-B ingestor with bash ./scripts/run_all.sh.")

    satellite = sources.get("satellite", {}) or {}
    line("satellite source", enabled_text(satellite))
    captures_path, captures_exists = path_status(satellite.get("captures_path"))
    line("SatDump captures path", f"{captures_path or 'missing'} ({'exists' if captures_exists else 'missing'})")
    if satellite.get("enabled") and not captures_exists:
        suggestions.append("Point sources.satellite.captures_path at your SatDump captures folder.")

    print()
    print("Next suggested action:")
    if suggestions:
        for suggestion in suggestions:
            print(f"- {suggestion}")
    else:
        print("- Sources look configured. If the UI is still empty, wait for new RF activity or inspect data/logs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
