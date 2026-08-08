from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def parse_datetime(value: Any) -> datetime | None:
    """Parse the date variants used by scholarly metadata APIs."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%d %B %Y", "%d %b %Y", "%B %Y", "%Y"):
            try:
                parsed = datetime.strptime(text, pattern).replace(tzinfo=UTC)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def content_value(content: dict[str, Any], key: str, default: Any = None) -> Any:
    """Unwrap OpenReview API v2 content fields while accepting legacy values."""
    value = content.get(key, default)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value
