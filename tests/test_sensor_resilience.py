"""Tests for sensor resilience: last-valid bridging of room-sensor gaps.

Exercises the production ``RoomTempBridge`` from climate_controllers.py, which
the regulation cycle (climate_regulation.py) calls once per cycle.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import types

# ---------------------------------------------------------------------------
# Load HA-free modules
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
_ctrl = _load_module(
    "tadox_proxy.climate_controllers",
    os.path.join(_COMP_DIR, "climate_controllers.py"),
)

DEFAULT_SENSOR_GRACE_S = _params.DEFAULT_SENSOR_GRACE_S
RoomTempBridge = _ctrl.RoomTempBridge


# ---------------------------------------------------------------------------
# Tests: Last-valid grace period
# ---------------------------------------------------------------------------


class TestSensorGracePeriod:
    """Verify that short sensor gaps are bridged with the last valid value.

    These readings carry no sensor timestamp, so the poll time counts.
    """

    def test_valid_reading_updates_last_valid(self):
        r = RoomTempBridge(DEFAULT_SENSOR_GRACE_S)
        result = r.resolve(20.5, None, now=1000.0)
        assert result == 20.5
        assert r.last_valid == 20.5
        assert r.last_valid_ts == 1000.0
        assert r.degraded is False

    def test_none_within_grace_returns_last_valid(self):
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, None, now=1000.0)
        result = r.resolve(None, None, now=1100.0)  # 100s later
        assert result == 20.5
        assert r.degraded is True

    def test_none_at_grace_boundary_returns_last_valid(self):
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, None, now=1000.0)
        result = r.resolve(None, None, now=1300.0)  # exactly 300s
        assert result == 20.5
        assert r.degraded is True

    def test_none_past_grace_returns_none(self):
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, None, now=1000.0)
        result = r.resolve(None, None, now=1301.0)  # 301s > 300s
        assert result is None
        assert r.degraded is True  # was valid, now expired

    def test_no_previous_value_returns_none(self):
        r = RoomTempBridge(DEFAULT_SENSOR_GRACE_S)
        result = r.resolve(None, None, now=1000.0)
        assert result is None
        assert r.degraded is False  # never had a valid value

    def test_valid_reading_after_gap_clears_degraded(self):
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, None, now=1000.0)
        r.resolve(None, None, now=1100.0)  # gap
        assert r.degraded is True
        r.resolve(21.0, None, now=1200.0)  # recovered
        assert r.degraded is False
        assert r.last_valid == 21.0

    def test_multiple_gaps_within_grace(self):
        """Multiple None readings within grace all return last valid."""
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, None, now=1000.0)
        assert r.resolve(None, None, now=1050.0) == 20.5
        assert r.resolve(None, None, now=1100.0) == 20.5
        assert r.resolve(None, None, now=1200.0) == 20.5
        # Grace timer is from the LAST valid reading, not from the first None
        assert r.resolve(None, None, now=1300.0) == 20.5  # exactly 300s
        assert r.resolve(None, None, now=1301.0) is None  # past grace

    def test_zero_grace_disables_bridging(self):
        """With grace_s=0, any gap immediately returns None."""
        r = RoomTempBridge(grace_s=0)
        r.resolve(20.5, None, now=1000.0)
        result = r.resolve(None, None, now=1000.1)
        assert result is None

    def test_grace_with_new_valid_resets_timer(self):
        """A new valid reading resets the grace timer."""
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, None, now=1000.0)
        r.resolve(None, None, now=1200.0)  # 200s into grace
        r.resolve(21.0, None, now=1250.0)  # new valid reading
        # Now grace starts from 1250, so 300s later = 1550
        assert r.resolve(None, None, now=1500.0) == 21.0  # within new grace
        assert r.resolve(None, None, now=1551.0) is None   # past new grace

    def test_grace_change_applies_to_next_cycle(self):
        """The options flow updates grace_s in place on the live bridge."""
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, None, now=1000.0)
        r.grace_s = 60
        assert r.resolve(None, None, now=1100.0) is None


class TestSensorTimestamp:
    """The grace period counts from the sensor's own report time.

    The coordinator passes ``room_temp_ts`` (the state's ``last_reported``);
    an earlier mirror of this logic stored the poll time instead and so
    overestimated how long a reading may still be bridged.
    """

    def test_sensor_timestamp_is_stored(self):
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, 900.0, now=1000.0)
        assert r.last_valid_ts == 900.0

    def test_grace_measured_from_sensor_timestamp(self):
        """Reported at 900, polled at 1000: the gap at 1250 is 350s old."""
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, 900.0, now=1000.0)
        assert r.resolve(None, None, now=1200.0) == 20.5  # 300s after report
        assert r.resolve(None, None, now=1201.0) is None   # 301s after report
        assert r.degraded is True

    def test_missing_timestamp_falls_back_to_poll_time(self):
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, None, now=1000.0)
        assert r.last_valid_ts == 1000.0

    def test_zero_timestamp_falls_back_to_poll_time(self):
        """A 0 timestamp is treated as missing (never a real report time)."""
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, 0.0, now=1000.0)
        assert r.last_valid_ts == 1000.0

    def test_timestamp_ignored_while_sensor_missing(self):
        """Only valid readings move the grace reference point."""
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, 900.0, now=1000.0)
        r.resolve(None, 1100.0, now=1100.0)
        assert r.last_valid_ts == 900.0


class TestSensorLogging:
    """Bridging logs at debug level, an expired grace period warns."""

    def test_bridging_logs_debug(self, caplog):
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, None, now=1000.0)
        with caplog.at_level(logging.DEBUG, logger=_ctrl.__name__):
            r.resolve(None, None, now=1100.0)
        assert "using last valid 20.5°C (age 100s, grace 300s)" in caplog.text

    def test_expired_grace_logs_warning(self, caplog):
        r = RoomTempBridge(grace_s=300)
        r.resolve(20.5, None, now=1000.0)
        with caplog.at_level(logging.WARNING, logger=_ctrl.__name__):
            r.resolve(None, None, now=1400.0)
        assert "grace period expired" in caplog.text
        assert "400s ago" in caplog.text

    def test_never_valid_does_not_warn(self, caplog):
        r = RoomTempBridge(grace_s=300)
        with caplog.at_level(logging.WARNING, logger=_ctrl.__name__):
            r.resolve(None, None, now=1000.0)
        assert caplog.text == ""


class TestDefaultGraceValue:
    """Verify the default sensor grace constant."""

    def test_default_is_300_seconds(self):
        assert DEFAULT_SENSOR_GRACE_S == 300
