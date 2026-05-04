from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml


ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config.yaml"
EXAMPLE_CONFIG_PATH = ROOT_DIR / "config.example.yaml"
TILE_ROOT = ROOT_DIR / "backend" / "static" / "tiles" / "osm"
MILES_PER_DEGREE_LAT = 69.0


DEFAULT_TILES = {
    "provider_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "user_agent": "RF-Lens/0.1 (local ham radio dashboard; contact: gregapackard@gmail.com)",
    "referer": "",
    "min_zoom": 6,
    "max_zoom": 11,
    "radius_miles": 300,
    "center_lat": 39.88,
    "center_lon": -82.81,
    "request_delay_seconds": 1.1,
    "retries": 3,
}


def load_config() -> dict[str, Any]:
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def tile_config() -> dict[str, Any]:
    cfg = DEFAULT_TILES.copy()
    cfg.update((load_config().get("tiles") or {}))
    return cfg


def bbox_from_center(center_lat: float, center_lon: float, radius_miles: float) -> tuple[float, float, float, float]:
    lat_delta = radius_miles / MILES_PER_DEGREE_LAT
    lon_delta = radius_miles / (MILES_PER_DEGREE_LAT * max(0.1, math.cos(math.radians(center_lat))))
    min_lat = max(-85.0, center_lat - lat_delta)
    max_lat = min(85.0, center_lat + lat_delta)
    min_lon = max(-180.0, center_lon - lon_delta)
    max_lon = min(180.0, center_lon + lon_delta)
    return min_lat, max_lat, min_lon, max_lon


def lon_to_tile_x(lon: float, zoom: int) -> int:
    return int((lon + 180.0) / 360.0 * (2**zoom))


def lat_to_tile_y(lat: float, zoom: int) -> int:
    lat_rad = math.radians(lat)
    return int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * (2**zoom))


def tile_ranges(cfg: dict[str, Any]) -> list[tuple[int, range, range]]:
    min_lat, max_lat, min_lon, max_lon = bbox_from_center(
        float(cfg["center_lat"]),
        float(cfg["center_lon"]),
        float(cfg["radius_miles"]),
    )
    ranges = []
    for zoom in range(int(cfg["min_zoom"]), int(cfg["max_zoom"]) + 1):
        x_min = lon_to_tile_x(min_lon, zoom)
        x_max = lon_to_tile_x(max_lon, zoom)
        y_min = lat_to_tile_y(max_lat, zoom)
        y_max = lat_to_tile_y(min_lat, zoom)
        ranges.append((zoom, range(x_min, x_max + 1), range(y_min, y_max + 1)))
    return ranges


def headers(cfg: dict[str, Any]) -> dict[str, str]:
    result = {"User-Agent": str(cfg["user_agent"])}
    referer = cfg.get("referer")
    if referer:
        result["Referer"] = str(referer)
    return result


def download_tile(zoom: int, x: int, y: int, path: Path, cfg: dict[str, Any]) -> str:
    if path.exists():
        return "skipped"

    path.parent.mkdir(parents=True, exist_ok=True)
    url = str(cfg["provider_url"]).format(z=zoom, x=x, y=y)
    attempts = int(cfg.get("retries", 3)) + 1
    delay = float(cfg.get("request_delay_seconds", 1.1))

    for attempt in range(1, attempts + 1):
        request = Request(url, headers=headers(cfg))
        try:
            with urlopen(request, timeout=30) as response:
                path.write_bytes(response.read())
            time.sleep(delay)
            return "downloaded"
        except HTTPError as exc:
            if exc.code == 403:
                print(f"403 forbidden for z{zoom}/{x}/{y}: provider rejected the request. Check tile usage policy and User-Agent.")
                return "forbidden"
            if attempt == attempts:
                return f"failed HTTP {exc.code}"
            backoff = delay * (2 ** (attempt - 1))
            print(f"HTTP {exc.code} for z{zoom}/{x}/{y}; retrying in {backoff:.1f}s")
            time.sleep(backoff)
        except (URLError, TimeoutError) as exc:
            if attempt == attempts:
                return f"failed {exc}"
            backoff = delay * (2 ** (attempt - 1))
            print(f"{exc} for z{zoom}/{x}/{y}; retrying in {backoff:.1f}s")
            time.sleep(backoff)

    return "failed"


def main() -> None:
    cfg = tile_config()
    ranges = tile_ranges(cfg)
    total = sum(len(xs) * len(ys) for _, xs, ys in ranges)
    stats = {"downloaded": 0, "skipped": 0, "forbidden": 0, "failed": 0}

    print(f"Saving tiles under {TILE_ROOT}")
    print(
        f"Center {cfg['center_lat']}, {cfg['center_lon']} "
        f"radius {cfg['radius_miles']} miles"
    )
    print(f"Zoom levels {cfg['min_zoom']}-{cfg['max_zoom']}, {total} tiles")
    print("Warning: 300 miles at higher zoom levels can become a large download.")
    print("This script is intentionally polite and may take a while.")

    done = 0
    for zoom, xs, ys in ranges:
        print(f"Zoom {zoom}: {len(xs) * len(ys)} tiles")
        for x in xs:
            for y in ys:
                done += 1
                path = TILE_ROOT / str(zoom) / str(x) / f"{y}.png"
                result = download_tile(zoom, x, y, path, cfg)
                key = result if result in stats else "failed"
                stats[key] += 1
                print(f"[{done}/{total}] z{zoom}/{x}/{y} {result}")

    print(
        "Done: "
        f"{stats['downloaded']} downloaded, "
        f"{stats['skipped']} already present, "
        f"{stats['forbidden']} forbidden, "
        f"{stats['failed']} failed"
    )


if __name__ == "__main__":
    main()
