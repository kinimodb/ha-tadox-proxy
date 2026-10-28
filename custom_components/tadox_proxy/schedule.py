"""Validate and evaluate weekly preset schedules stored in options."""

from __future__ import annotations

import datetime as dt
from typing import Any

SCHEDULE_PRESETS = frozenset({"comfort", "eco", "frost_protection", "off"})
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def normalize_weekly_schedule(value: Any) -> dict[str, list[dict[str, str]]]:
    """Validate and sort editable weekday blocks before persisting options."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(WEEKDAYS):
        raise ValueError("Invalid weekday schedule")

    result: dict[str, list[dict[str, str]]] = {}
    for day in WEEKDAYS:
        blocks = value.get(day, [])
        if not isinstance(blocks, list):
            raise ValueError(f"Invalid blocks for {day}")
        seen: set[str] = set()
        normalized: list[dict[str, str]] = []
        for block in blocks:
            if not isinstance(block, dict) or set(block) != {"start", "preset"}:
                raise ValueError(f"Invalid block for {day}")
            start = block["start"]
            preset = block["preset"]
            if not isinstance(start, str) or not isinstance(preset, str):
                raise ValueError(f"Invalid block values for {day}")
            try:
                parsed = dt.time.fromisoformat(start)
            except ValueError as err:
                raise ValueError(f"Invalid time for {day}") from err
            if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
                raise ValueError(f"Invalid time for {day}")
            start = parsed.strftime("%H:%M")
            if preset not in SCHEDULE_PRESETS or start in seen:
                raise ValueError(f"Invalid or duplicate block for {day}")
            seen.add(start)
            normalized.append({"start": start, "preset": preset})
        if normalized:
            result[day] = sorted(normalized, key=lambda block: block["start"])
    return result


def _wall_time_ts(date: dt.date, start: str, tz: dt.tzinfo) -> float | None:
    """Use the first fall-back occurrence; skip nonexistent spring-forward time."""
    hour, minute = map(int, start.split(":"))
    wall = dt.datetime.combine(date, dt.time(hour, minute), tzinfo=tz)
    timestamp = wall.timestamp()
    actual = dt.datetime.fromtimestamp(timestamp, tz)
    if (actual.date(), actual.hour, actual.minute) != (date, hour, minute):
        return None
    return timestamp


def weekly_preset_at(schedule: dict, now: dt.datetime) -> str | None:
    """Return the last block active at *now*, including one from last week."""
    if now.tzinfo is None:
        raise ValueError("An aware datetime is required")
    if not schedule:
        return None
    best: tuple[float, str] | None = None
    for days_back in range(15):
        date = now.date() - dt.timedelta(days=days_back)
        for block in schedule.get(WEEKDAYS[date.weekday()], []):
            timestamp = _wall_time_ts(date, block["start"], now.tzinfo)
            if timestamp is not None and timestamp <= now.timestamp():
                if best is None or timestamp > best[0]:
                    best = (timestamp, block["preset"])
    return best[1] if best else None


def next_weekly_event_ts(schedule: dict, now: dt.datetime) -> float | None:
    """Return the next wall-clock block boundary, including next week."""
    if now.tzinfo is None:
        raise ValueError("An aware datetime is required")
    if not schedule:
        return None
    next_ts: float | None = None
    for days_ahead in range(15):
        date = now.date() + dt.timedelta(days=days_ahead)
        for block in schedule.get(WEEKDAYS[date.weekday()], []):
            timestamp = _wall_time_ts(date, block["start"], now.tzinfo)
            if timestamp is not None and timestamp > now.timestamp():
                if next_ts is None or timestamp < next_ts:
                    next_ts = timestamp
    return next_ts
