"""Home Assistant-free tests for persisted weekly preset blocks."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

path = Path(__file__).parent.parent / "custom_components" / "tadox_proxy" / "schedule.py"
spec = importlib.util.spec_from_file_location("tadox_proxy_weekly_schedule", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

normalize_weekly_schedule = module.normalize_weekly_schedule
weekly_preset_at = module.weekly_preset_at
next_weekly_event_ts = module.next_weekly_event_ts
WARSAW = ZoneInfo("Europe/Warsaw")


def test_blocks_are_sorted_and_cycle_across_empty_days_and_week_end():
    schedule = normalize_weekly_schedule(
        {
            "monday": [
                {"start": "07:00", "preset": "comfort"},
                {"start": "00:00", "preset": "eco"},
            ],
            "friday": [{"start": "21:00", "preset": "off"}],
        }
    )
    assert schedule["monday"][0]["start"] == "00:00"
    assert weekly_preset_at(schedule, datetime(2026, 10, 27, 10, tzinfo=WARSAW)) == "comfort"
    sunday = datetime(2026, 11, 1, 20, tzinfo=WARSAW)
    assert weekly_preset_at(schedule, sunday) == "off"
    assert datetime.fromtimestamp(next_weekly_event_ts(schedule, sunday), WARSAW) == datetime(
        2026, 11, 2, 0, tzinfo=WARSAW
    )


def test_duplicate_or_invalid_blocks_are_rejected_before_saving():
    with pytest.raises(ValueError):
        normalize_weekly_schedule(
            {"monday": [
                {"start": "07:00", "preset": "comfort"},
                {"start": "07:00:00", "preset": "eco"},
            ]}
        )
    with pytest.raises(ValueError):
        normalize_weekly_schedule({"monday": [{"start": "24:00", "preset": "comfort"}]})
    with pytest.raises(ValueError):
        normalize_weekly_schedule({"monday": [{"start": "07:00", "preset": "boost"}]})


def test_nonexistent_spring_time_is_skipped_without_losing_active_profile():
    schedule = normalize_weekly_schedule(
        {"sunday": [{"start": "02:30", "preset": "eco"}]}
    )
    after_jump = datetime(2026, 3, 29, 3, 15, tzinfo=WARSAW)
    assert weekly_preset_at(schedule, after_jump) == "eco"
    assert datetime.fromtimestamp(next_weekly_event_ts(schedule, after_jump), WARSAW) == datetime(
        2026, 4, 5, 2, 30, tzinfo=WARSAW
    )


def test_repeated_fall_time_does_not_revert_preset():
    schedule = normalize_weekly_schedule(
        {"sunday": [
            {"start": "02:00", "preset": "eco"},
            {"start": "02:30", "preset": "comfort"},
        ]}
    )
    second_0215 = datetime(2026, 10, 25, 2, 15, tzinfo=WARSAW, fold=1)
    assert weekly_preset_at(schedule, second_0215) == "comfort"
    assert datetime.fromtimestamp(next_weekly_event_ts(schedule, second_0215), WARSAW) == datetime(
        2026, 11, 1, 2, 0, tzinfo=WARSAW
    )
