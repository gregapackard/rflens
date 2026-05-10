from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None  # type: ignore[assignment]

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT_DIR / "config.example.yaml"


class Reporter:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0
        self.suggestions: list[str] = []

    def ok(self, message: str) -> None:
        print(f"OK   {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(f"WARN {message}")

    def fail(self, message: str) -> None:
        self.failures += 1
        print(f"FAIL {message}")

    def suggest(self, message: str) -> None:
        if message not in self.suggestions:
            self.suggestions.append(message)

    def print_next_actions(self) -> None:
        if not self.failures and not self.warnings:
            print()
            print("Setup check passed. Next: bash ./scripts/run_api.sh")
            return
        print()
        print("Next actions:")
        if self.failures:
            print("  - Fix FAIL items before starting RFLens.")
        for suggestion in self.suggestions:
            print(f"  - {suggestion}")
        if self.warnings and not self.failures:
            print("  - WARN items do not block API-only startup; disabled sources can be configured later.")
            print("  - Start the API with: bash ./scripts/run_api.sh")


def resolve_path(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return ROOT_DIR / candidate


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        text = handle.read()
    if yaml is None:
        return parse_simple_yaml(text)
    payload = yaml.safe_load(text) or {}
    return payload if isinstance(payload, dict) else {}


def parse_simple_value(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value[0:1] in {"'", '"'} and value[-1:] == value[0]:
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def strip_inline_comment(line: str) -> str:
    quote: str | None = None
    for index, char in enumerate(line):
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        if char == "#" and quote is None:
            return line[:index]
    return line


def parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        line = strip_inline_comment(raw_line).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, separator, value = line.strip().partition(":")
        if not separator:
            continue
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value.strip():
            parent[key] = parse_simple_value(value)
            continue
        child: dict[str, Any] = {}
        parent[key] = child
        stack.append((indent, child))
    return root


def value_present(value: Any) -> bool:
    return value not in (None, "")


def check_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".rflens-check-", dir=path, delete=False) as handle:
            temp_path = Path(handle.name)
        temp_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def check_station(reporter: Reporter, cfg: dict[str, Any]) -> None:
    station = cfg.get("station", {}) or {}
    for key in ("name", "callsign", "grid", "lat", "lon"):
        if value_present(station.get(key)):
            reporter.ok(f"station.{key} configured")
        else:
            reporter.warn(f"station.{key} is missing")
            reporter.suggest(f"Edit config.yaml and set station.{key}.")
    if str(station.get("callsign") or "").upper() == "N0CALL":
        reporter.warn("station.callsign still uses the N0CALL placeholder")
        reporter.suggest("Replace N0CALL placeholders in config.yaml with your station callsign.")


def check_path_exists(reporter: Reporter, label: str, path_value: Any) -> None:
    if not value_present(path_value):
        reporter.warn(f"{label} is not configured")
        return
    path = resolve_path(str(path_value))
    if path.exists():
        reporter.ok(f"{label} exists: {path}")
    else:
        reporter.warn(f"{label} does not exist yet: {path}")


def main() -> int:
    reporter = Reporter()

    if CONFIG_PATH.exists():
        reporter.ok(f"config.yaml found: {CONFIG_PATH}")
        cfg = load_yaml(CONFIG_PATH)
    elif EXAMPLE_CONFIG_PATH.exists():
        reporter.warn("config.yaml not found; using config.example.yaml for this check")
        reporter.suggest("Create a private config first: cp config.example.yaml config.yaml")
        cfg = load_yaml(EXAMPLE_CONFIG_PATH)
    else:
        reporter.fail("No config.yaml or config.example.yaml found")
        return reporter.failures

    database_path = resolve_path(str(cfg.get("database_path") or "./data/rflens.db"))
    if check_writable_directory(database_path.parent):
        reporter.ok(f"database parent is writable: {database_path.parent}")
    else:
        reporter.fail(f"database parent is not writable: {database_path.parent}")

    check_station(reporter, cfg)

    server = cfg.get("server", {}) or {}
    host = server.get("host", "0.0.0.0")
    port = server.get("port", 8080)
    if value_present(host) and value_present(port):
        reporter.ok(f"server configured: {host}:{port}")
    else:
        reporter.fail("server.host and server.port must be configured")

    sources = cfg.get("sources", {}) or {}

    aprs = sources.get("aprs", {}) or {}
    if aprs.get("enabled"):
        reporter.ok("APRS source enabled")
        if value_present(aprs.get("callsign") or aprs.get("igate_callsign") or (cfg.get("station", {}) or {}).get("aprs_callsign")):
            reporter.ok("APRS local/iGate callsign configured")
        else:
            reporter.warn("APRS enabled but no local/iGate callsign is configured")
            reporter.suggest("Set sources.aprs.callsign or station.aprs_callsign before running rflens-aprs.")
        check_path_exists(reporter, "Direwolf log path", aprs.get("log_path"))
        if value_present(aprs.get("log_path")) and not resolve_path(str(aprs.get("log_path"))).exists():
            reporter.suggest("Start Direwolf or correct sources.aprs.log_path before running rflens-aprs.")
    else:
        reporter.ok("APRS source disabled")

    adsb = sources.get("adsb", {}) or {}
    adsb_ui = cfg.get("adsb_ui", {}) or {}
    if adsb.get("enabled"):
        reporter.ok("ADS-B source enabled")
        check_path_exists(reporter, "readsb aircraft.json path", adsb.get("aircraft_json_path"))
        if value_present(adsb.get("aircraft_json_path")) and not resolve_path(str(adsb.get("aircraft_json_path"))).exists():
            reporter.suggest("Start readsb or correct sources.adsb.aircraft_json_path before running rflens-adsb.")
    else:
        reporter.ok("ADS-B source disabled")

    if adsb_ui.get("enabled"):
        if value_present(adsb_ui.get("url")):
            reporter.ok(f"ADS-B UI URL configured: {adsb_ui.get('url')}")
        else:
            reporter.warn("adsb_ui is enabled but url is empty")
            reporter.suggest("Set adsb_ui.url or disable adsb_ui until tar1090 is reachable.")
    else:
        reporter.ok("ADS-B UI disabled")

    satellite = sources.get("satellite", {}) or {}
    if satellite.get("enabled"):
        reporter.ok("SatDump capture watcher enabled")
        check_path_exists(reporter, "SatDump captures path", satellite.get("captures_path"))
        if value_present(satellite.get("captures_path")) and not resolve_path(str(satellite.get("captures_path"))).exists():
            reporter.suggest("Create sources.satellite.captures_path or disable satellite until SatDump is configured.")
    else:
        reporter.ok("SatDump capture watcher disabled")

    system_cfg = cfg.get("system", {}) or {}
    if value_present(system_cfg.get("disk_path")):
        path = resolve_path(str(system_cfg["disk_path"]))
        if path.exists():
            reporter.ok(f"system.disk_path exists: {path}")
        else:
            reporter.warn(f"system.disk_path does not exist yet: {path}")
            reporter.suggest("Create system.disk_path or remove it to use the database directory.")
    else:
        reporter.ok("system.disk_path not set; database parent will be used")

    reporter.print_next_actions()
    return 1 if reporter.failures else 0


if __name__ == "__main__":
    sys.exit(main())
