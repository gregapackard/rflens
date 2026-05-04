from __future__ import annotations

from pydantic import BaseModel


class EventIn(BaseModel):
    source_id: int | None = None
    source_name: str | None = None
    source_type: str | None = None
    event_type: str
    timestamp: str | None = None
    callsign: str | None = None
    lat: float | None = None
    lon: float | None = None
    altitude: float | None = None
    speed: float | None = None
    raw_text: str | None = None
    metadata_json: str | None = None
