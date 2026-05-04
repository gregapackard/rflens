from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from backend.config import resolve_path, source_config
from backend.db import get_or_create_source, insert_capture, insert_event, touch_source, utc_now


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
METADATA_NAMES = {"metadata.json", "pass.json", "info.json", "tle.json"}


def image_files(path: Path) -> list[Path]:
    if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
        return [path]
    if not path.exists() or not path.is_dir():
        return []
    return [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]


def read_metadata(folder: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {"folder": str(folder)}
    candidates = folder.iterdir() if folder.exists() and folder.is_dir() else []
    for candidate in candidates:
        if candidate.name.lower() not in METADATA_NAMES:
            continue
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                metadata[candidate.name] = json.load(handle)
        except Exception as exc:
            metadata[candidate.name] = {"error": str(exc)}
    return metadata


def infer_satellite(folder: Path, metadata: dict[str, Any]) -> str | None:
    for value in metadata.values():
        if isinstance(value, dict):
            for key in ("satellite", "satellite_name", "name", "norad_name"):
                candidate = value.get(key)
                if candidate:
                    return str(candidate)
    name = folder.name.replace("_", " ").replace("-", " ").strip()
    return name or None


def process_path(source_id: int, path: Path) -> None:
    folder = path if path.is_dir() else path.parent
    metadata = read_metadata(folder)
    metadata["detected_path"] = str(path)
    satellite = infer_satellite(folder, metadata)

    for image in image_files(path):
        capture_id = insert_capture(
            source_id=source_id,
            satellite=satellite,
            start_time=utc_now(),
            end_time=None,
            max_elevation=None,
            image_path=str(image),
            status="complete",
            metadata={**metadata, "image": str(image)},
        )
        if capture_id is None:
            continue
        insert_event(
            source_id=source_id,
            event_type="satellite_capture",
            callsign=satellite,
            raw_text=str(image),
            metadata={"capture_id": capture_id, **metadata, "image": str(image)},
        )


class SatDumpHandler(FileSystemEventHandler):
    def __init__(self, source_id: int) -> None:
        self.source_id = source_id

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory or Path(event.src_path).suffix.lower() in IMAGE_EXTENSIONS:
            process_path(self.source_id, Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        process_path(self.source_id, Path(event.dest_path))


def scan_existing(source_id: int, root: Path) -> None:
    if root.exists():
        process_path(source_id, root)


def run_forever() -> None:
    cfg = source_config("satellite")
    source_id = get_or_create_source(
        cfg.get("name", "SatDump Captures"),
        "satellite",
        cfg.get("device_index"),
        cfg.get("frequency"),
    )
    root = resolve_path(cfg.get("captures_path", "./data/captures"))
    root.mkdir(parents=True, exist_ok=True)
    scan_existing(source_id, root)
    touch_source(source_id, "online")

    observer = Observer()
    observer.schedule(SatDumpHandler(source_id), str(root), recursive=True)
    observer.start()
    try:
        while True:
            touch_source(source_id, "online")
            time.sleep(5)
    finally:
        observer.stop()
        observer.join()


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
