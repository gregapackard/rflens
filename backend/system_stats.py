from __future__ import annotations

from pathlib import Path


def memory_stats_from_values(total_bytes: int, available_bytes: int) -> dict[str, float | int] | None:
    if total_bytes <= 0 or available_bytes < 0:
        return None
    available_bytes = min(available_bytes, total_bytes)
    used_bytes = total_bytes - available_bytes
    return {
        "total": total_bytes,
        "available": available_bytes,
        "used": used_bytes,
        "total_mb": round(total_bytes / 1024 / 1024, 1),
        "available_mb": round(available_bytes / 1024 / 1024, 1),
        "used_mb": round(used_bytes / 1024 / 1024, 1),
        "percent": round((used_bytes / total_bytes) * 100, 1),
    }


def read_meminfo_memory(path: Path = Path("/proc/meminfo")) -> dict[str, float | int] | None:
    try:
        fields: dict[str, int] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            if key not in {"MemTotal", "MemAvailable"}:
                continue
            value_text = rest.strip().split()[0]
            fields[key] = int(value_text) * 1024
        total = fields.get("MemTotal")
        available = fields.get("MemAvailable")
        if total is None or available is None:
            return None
        return memory_stats_from_values(total, available)
    except (OSError, ValueError, IndexError):
        return None
