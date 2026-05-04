from __future__ import annotations

from typing import Any

from .db import insert_event


def record_event(event_type: str, **kwargs: Any) -> int:
    return insert_event(event_type=event_type, **kwargs)
