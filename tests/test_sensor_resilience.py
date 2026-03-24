"""Tests for sensor resilience: last-valid bridging and timer revalidation.

These tests validate the sensor grace-period logic that was added to the
regulation cycle.  Since the full climate entity requires HA, we test the
core logic in isolation using a lightweight helper that mirrors the
_async_regulation_cycle sensor resolution.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Load parameters.py HA-free
# ---------------------------------------------------------------------------

_COMP_DIR = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "tadox_proxy"
)


def _load_module(name: str, path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_params = _load_module(
    "tadox_proxy.parameters",
    os.path.join(_COMP_DIR, "parameters.py"),
)

DEFAULT_SENSOR_GRACE_S = _params.DEFAULT_SENSOR_GRACE_S


# ---------------------------------------------------------------------------
# Lightweight sensor-resolution logic (mirrors climate.py regulation cycle)
# ---------------------------------------------------------------------------


class SensorResolver:
    """Mimics the sensor-resolution logic from _async_regulation_cycle."""

    def __init__(self, grace_s: int = DEFAULT_SENSOR_GRACE_S):
        self.last_valid_room_temp: float | None = None
        self.last_valid_room_temp_ts: float = 0.0
        self.sensor_grace_s = grace_s
        self.sensor_degraded: bool = False

    def resolve(self, room_temp: float | None, now: float) -> float | None:
        """Resolve the effective room_temp, using last-valid if within grace."""
        if room_temp is not None:
            self.last_valid_room_temp = room_temp
            self.last_valid_room_temp_ts = now
            self.sensor_degraded = False
            return room_temp

        if (
            self.last_valid_room_temp is not None
            and (now - self.last_valid_room_temp_ts) <= self.sensor_grace_s
        ):
            self.sensor_degraded = True
            return self.last_valid_room_temp

        self.sensor_degraded = (
            room_temp is None and self.last_valid_room_temp is not None
        )
        return None


# ---------------------------------------------------------------------------
# Tests: Last-valid grace period
# ---------------------------------------------------------------------------


class TestSensorGracePeriod:
    """Verify that short sensor gaps are bridged with the last valid value."""

    def test_valid_reading_updates_last_valid(self):
        r = SensorResolver()
        result = r.resolve(20.5, now=1000.0)
        assert result == 20.5
        assert r.last_valid_room_temp == 20.5
        assert r.last_valid_room_temp_ts == 1000.0
        assert r.sensor_degraded is False

    def test_none_within_grace_returns_last_valid(self):
        r = SensorResolver(grace_s=300)
        r.resolve(20.5, now=1000.0)
        result = r.resolve(None, now=1100.0)  # 100s later
        assert result == 20.5
        assert r.sensor_degraded is True

    def test_none_at_grace_boundary_returns_last_valid(self):
        r = SensorResolver(grace_s=300)
        r.resolve(20.5, now=1000.0)
        result = r.resolve(None, now=1300.0)  # exactly 300s
        assert result == 20.5
        assert r.sensor_degraded is True

    def test_none_past_grace_returns_none(self):
        r = SensorResolver(grace_s=300)
        r.resolve(20.5, now=1000.0)
        result = r.resolve(None, now=1301.0)  # 301s > 300s
        assert result is None
        assert r.sensor_degraded is True  # was valid, now expired

    def test_no_previous_value_returns_none(self):
        r = SensorResolver()
        result = r.resolve(None, now=1000.0)
        assert result is None
        assert r.sensor_degraded is False  # never had a valid value

    def test_valid_reading_after_gap_clears_degraded(self):
        r = SensorResolver(grace_s=300)
        r.resolve(20.5, now=1000.0)
        r.resolve(None, now=1100.0)  # gap
        assert r.sensor_degraded is True
        r.resolve(21.0, now=1200.0)  # recovered
        assert r.sensor_degraded is False
        assert r.last_valid_room_temp == 21.0

    def test_multiple_gaps_within_grace(self):
        """Multiple None readings within grace all return last valid."""
        r = SensorResolver(grace_s=300)
        r.resolve(20.5, now=1000.0)
        assert r.resolve(None, now=1050.0) == 20.5
        assert r.resolve(None, now=1100.0) == 20.5
        assert r.resolve(None, now=1200.0) == 20.5
        # Grace timer is from the LAST valid reading, not from the first None
        assert r.resolve(None, now=1300.0) == 20.5  # exactly 300s
        assert r.resolve(None, now=1301.0) is None  # past grace

    def test_zero_grace_disables_bridging(self):
        """With grace_s=0, any gap immediately returns None."""
        r = SensorResolver(grace_s=0)
        r.resolve(20.5, now=1000.0)
        result = r.resolve(None, now=1000.1)
        assert result is None

    def test_grace_with_new_valid_resets_timer(self):
        """A new valid reading resets the grace timer."""
        r = SensorResolver(grace_s=300)
        r.resolve(20.5, now=1000.0)
        r.resolve(None, now=1200.0)  # 200s into grace
        r.resolve(21.0, now=1250.0)  # new valid reading
        # Now grace starts from 1250, so 300s later = 1550
        assert r.resolve(None, now=1500.0) == 21.0  # within new grace
        assert r.resolve(None, now=1551.0) is None   # past new grace


class TestDefaultGraceValue:
    """Verify the default sensor grace constant."""

    def test_default_is_300_seconds(self):
        assert DEFAULT_SENSOR_GRACE_S == 300
