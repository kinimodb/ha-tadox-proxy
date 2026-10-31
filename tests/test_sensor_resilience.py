"""Tests for sensor resilience: grace bridging and the sensor-loss action.

Exercises the production ``ControlModeResolver`` from climate_controllers.py,
which the regulation cycle (climate_regulation.py) calls once per cycle.
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
ControlModeResolver = _ctrl.ControlModeResolver
CONTROL_ROOM_SENSOR = _ctrl.CONTROL_ROOM_SENSOR
CONTROL_BACKUP_SENSOR = _ctrl.CONTROL_BACKUP_SENSOR
CONTROL_TRV = _ctrl.CONTROL_TRV
CONTROL_HOLD = _ctrl.CONTROL_HOLD


def _resolve(
    resolver,
    room_temp,
    room_temp_ts=None,
    *,
    now,
    backup_temp=None,
    tado_internal_c=22.0,
    frost_protection=False,
    trv_command_known=True,
):
    """Run one cycle; the TRV reports its temperature unless told otherwise."""
    return resolver.resolve(
        room_temp=room_temp,
        room_temp_ts=room_temp_ts,
        backup_temp=backup_temp,
        tado_internal_c=tado_internal_c,
        frost_protection=frost_protection,
        trv_command_known=trv_command_known,
        now=now,
    )


def _room_temp(resolver, room_temp, room_temp_ts=None, *, now):
    """Return the temperature the regulator runs on in this cycle."""
    return _resolve(resolver, room_temp, room_temp_ts, now=now).room_temp_c


# ---------------------------------------------------------------------------
# Tests: Last-valid grace period
# ---------------------------------------------------------------------------


class TestSensorGracePeriod:
    """Verify that short sensor gaps are bridged with the last valid value.

    These readings carry no sensor timestamp, so the poll time counts.
    """

    def test_valid_reading_updates_last_valid(self):
        r = ControlModeResolver(DEFAULT_SENSOR_GRACE_S)
        result = _room_temp(r, 20.5, None, now=1000.0)
        assert result == 20.5
        assert r.last_valid == 20.5
        assert r.last_valid_ts == 1000.0
        assert r.degraded is False

    def test_none_within_grace_returns_last_valid(self):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        result = _room_temp(r, None, None, now=1100.0)  # 100s later
        assert result == 20.5
        assert r.degraded is True

    def test_none_at_grace_boundary_returns_last_valid(self):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        result = _room_temp(r, None, None, now=1300.0)  # exactly 300s
        assert result == 20.5
        assert r.degraded is True

    def test_none_past_grace_returns_none(self):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        result = _room_temp(r, None, None, now=1301.0)  # 301s > 300s
        assert result is None
        assert r.degraded is True  # was valid, now expired

    def test_no_previous_value_returns_none(self):
        r = ControlModeResolver(DEFAULT_SENSOR_GRACE_S)
        result = _room_temp(r, None, None, now=1000.0)
        assert result is None
        assert r.degraded is False  # never had a valid value

    def test_valid_reading_after_gap_clears_degraded(self):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        _room_temp(r, None, None, now=1100.0)  # gap
        assert r.degraded is True
        _room_temp(r, 21.0, None, now=1200.0)  # recovered
        assert r.degraded is False
        assert r.last_valid == 21.0

    def test_multiple_gaps_within_grace(self):
        """Multiple None readings within grace all return last valid."""
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        assert _room_temp(r, None, None, now=1050.0) == 20.5
        assert _room_temp(r, None, None, now=1100.0) == 20.5
        assert _room_temp(r, None, None, now=1200.0) == 20.5
        # Grace timer is from the LAST valid reading, not from the first None
        assert _room_temp(r, None, None, now=1300.0) == 20.5  # exactly 300s
        assert _room_temp(r, None, None, now=1301.0) is None  # past grace

    def test_zero_grace_disables_bridging(self):
        """With grace_s=0, any gap immediately returns None."""
        r = ControlModeResolver(grace_s=0)
        _room_temp(r, 20.5, None, now=1000.0)
        result = _room_temp(r, None, None, now=1000.1)
        assert result is None

    def test_grace_with_new_valid_resets_timer(self):
        """A new valid reading resets the grace timer."""
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        _room_temp(r, None, None, now=1200.0)  # 200s into grace
        _room_temp(r, 21.0, None, now=1250.0)  # new valid reading
        # Now grace starts from 1250, so 300s later = 1550
        assert _room_temp(r, None, None, now=1500.0) == 21.0  # within new grace
        assert _room_temp(r, None, None, now=1551.0) is None   # past new grace

    def test_grace_change_applies_to_next_cycle(self):
        """The options flow updates grace_s in place on the live bridge."""
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        r.grace_s = 60
        assert _room_temp(r, None, None, now=1100.0) is None


class TestMissingReading:
    """The last valid reading shows only while the room sensor is missing.

    Shown all the time, its ever-growing age made every state write a new
    recorder row - about two a minute per room.
    """

    def test_nothing_while_the_sensor_reports(self):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, 900.0, now=1000.0)
        assert r.missing_reading(now=1060.0) is None

    def test_last_valid_reading_and_its_age_while_the_sensor_is_missing(self):
        """Reported at 900, missing at 1100: the reading is 200 s old."""
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, 900.0, now=1000.0)
        _room_temp(r, None, None, now=1100.0)
        assert r.missing_reading(now=1100.0) == (20.5, 200)

    def test_nothing_to_show_from_a_sensor_that_never_reported(self):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, None, None, now=1000.0)
        _room_temp(r, None, None, now=1400.0)  # grace expired: degraded
        assert r.degraded is True
        assert r.missing_reading(now=1400.0) is None


class TestSensorTimestamp:
    """The grace period counts from the sensor's own report time.

    The coordinator passes ``room_temp_ts`` (the state's ``last_reported``);
    an earlier mirror of this logic stored the poll time instead and so
    overestimated how long a reading may still be bridged.
    """

    def test_sensor_timestamp_is_stored(self):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, 900.0, now=1000.0)
        assert r.last_valid_ts == 900.0

    def test_grace_measured_from_sensor_timestamp(self):
        """Reported at 900, polled at 1000: the gap at 1250 is 350s old."""
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, 900.0, now=1000.0)
        assert _room_temp(r, None, None, now=1200.0) == 20.5  # 300s after report
        assert _room_temp(r, None, None, now=1201.0) is None   # 301s after report
        assert r.degraded is True

    def test_missing_timestamp_falls_back_to_poll_time(self):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        assert r.last_valid_ts == 1000.0

    def test_zero_timestamp_falls_back_to_poll_time(self):
        """A 0 timestamp is treated as missing (never a real report time)."""
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, 0.0, now=1000.0)
        assert r.last_valid_ts == 1000.0

    def test_timestamp_ignored_while_sensor_missing(self):
        """Only valid readings move the grace reference point."""
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, 900.0, now=1000.0)
        _room_temp(r, None, 1100.0, now=1100.0)
        assert r.last_valid_ts == 900.0


class TestSensorLogging:
    """Bridging logs at debug level, an expired grace period warns."""

    def test_bridging_logs_debug(self, caplog):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        with caplog.at_level(logging.DEBUG, logger=_ctrl.__name__):
            _room_temp(r, None, None, now=1100.0)
        assert "using last valid 20.5°C (age 100s, grace 300s)" in caplog.text

    def test_expired_grace_logs_warning(self, caplog):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        with caplog.at_level(logging.WARNING, logger=_ctrl.__name__):
            _room_temp(r, None, None, now=1400.0)
        assert "grace period expired" in caplog.text
        assert "400s ago" in caplog.text

    def test_never_valid_does_not_warn(self, caplog):
        r = ControlModeResolver(grace_s=300)
        with caplog.at_level(logging.WARNING, logger=_ctrl.__name__):
            _room_temp(r, None, None, now=1000.0)
        assert caplog.text == ""

    def test_never_valid_warns_after_grace(self, caplog):
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, None, None, now=1000.0)
        with caplog.at_level(logging.WARNING, logger=_ctrl.__name__):
            _room_temp(r, None, None, now=1301.0)
        assert "has not reported since startup" in caplog.text

    def test_expired_grace_warns_once_per_outage(self, caplog):
        """The cycle runs every minute; one warning per outage is enough."""
        r = ControlModeResolver(grace_s=300)
        _room_temp(r, 20.5, None, now=1000.0)
        with caplog.at_level(logging.WARNING, logger=_ctrl.__name__):
            _room_temp(r, None, None, now=1400.0)
            _room_temp(r, None, None, now=1460.0)
            _room_temp(r, 20.5, None, now=1520.0)
            _room_temp(r, None, None, now=1900.0)
        assert caplog.text.count("grace period expired") == 2


def _expired(loss_action, **kwargs):
    """Return a resolver whose room sensor was last valid at 1000 (grace 300)."""
    r = ControlModeResolver(grace_s=300, loss_action=loss_action)
    _resolve(r, 20.5, now=1000.0, **kwargs)
    return r


class TestSensorLossAction:
    """Once the grace period has expired, the configured action applies."""

    def test_trv_action_lets_the_trv_regulate(self):
        r = _expired(CONTROL_TRV)
        d = _resolve(r, None, now=1301.0)
        assert d.mode == CONTROL_TRV
        assert d.room_temp_c is None

    def test_action_waits_for_the_grace_period(self):
        r = _expired(CONTROL_TRV)
        d = _resolve(r, None, now=1300.0)
        assert d.mode == CONTROL_ROOM_SENSOR
        assert d.room_temp_c == 20.5

    def test_backup_action_regulates_on_the_backup_sensor(self):
        r = _expired(CONTROL_BACKUP_SENSOR)
        d = _resolve(r, None, now=1301.0, backup_temp=19.0)
        assert d.mode == CONTROL_BACKUP_SENSOR
        assert d.room_temp_c == 19.0

    def test_missing_backup_sensor_falls_back_to_the_trv(self):
        r = _expired(CONTROL_BACKUP_SENSOR)
        d = _resolve(r, None, now=1301.0, backup_temp=None)
        assert d.mode == CONTROL_TRV
        assert d.room_temp_c is None

    def test_backup_without_trv_temperature_falls_back_to_the_trv(self):
        """Feedforward needs the TRV's own reading; without it the TRV regulates."""
        r = _expired(CONTROL_BACKUP_SENSOR)
        d = _resolve(r, None, now=1301.0, backup_temp=19.0, tado_internal_c=None)
        assert d.mode == CONTROL_TRV
        assert d.room_temp_c is None

    def test_missing_trv_temperature_waits(self):
        """The action covers the room sensor only; without the TRV's reading
        the cycle waits as before."""
        r = ControlModeResolver(grace_s=300, loss_action=CONTROL_TRV)
        d = _resolve(r, 20.5, now=1000.0, tado_internal_c=None)
        assert d.mode == CONTROL_ROOM_SENSOR
        assert d.room_temp_c is None


class TestSensorMissingSinceStartup:
    """A sensor that has not reported since startup gets the same grace
    period, counted from the first cycle – it may simply load later."""

    def test_waits_during_grace(self):
        r = ControlModeResolver(grace_s=300, loss_action=CONTROL_TRV)
        _resolve(r, None, now=1000.0)
        d = _resolve(r, None, now=1300.0)
        assert d.mode == CONTROL_ROOM_SENSOR
        assert d.room_temp_c is None
        assert r.degraded is False

    def test_action_applies_after_grace(self):
        r = ControlModeResolver(grace_s=300, loss_action=CONTROL_TRV)
        _resolve(r, None, now=1000.0)
        d = _resolve(r, None, now=1301.0)
        assert d.mode == CONTROL_TRV
        assert r.degraded is True

    def test_late_sensor_regulates_normally(self):
        r = ControlModeResolver(grace_s=300, loss_action=CONTROL_TRV)
        _resolve(r, None, now=1000.0)
        d = _resolve(r, 20.5, now=1060.0)
        assert d.mode == CONTROL_ROOM_SENSOR
        assert d.room_temp_c == 20.5

    def test_frost_uses_trv_before_first_room_reading(self):
        r = ControlModeResolver(grace_s=300, loss_action=CONTROL_HOLD)
        d = _resolve(r, None, now=1000.0, frost_protection=True)
        assert d.mode == CONTROL_TRV
        assert d.room_temp_c is None

    def test_first_room_reading_resumes_regulation_after_frost(self):
        r = ControlModeResolver(grace_s=300, loss_action=CONTROL_HOLD)
        _resolve(r, None, now=1000.0, frost_protection=True)
        d = _resolve(r, 20.5, now=1030.0, frost_protection=True)
        assert d.mode == CONTROL_ROOM_SENSOR
        assert d.room_temp_c == 20.5
        assert d.mode_changed is True

    def test_window_close_without_room_reading_keeps_direct_control(self):
        r = ControlModeResolver(grace_s=300, loss_action=CONTROL_HOLD)
        _resolve(r, None, now=1000.0, frost_protection=True)
        assert _resolve(r, None, now=1060.0).mode == CONTROL_TRV
        assert _resolve(r, None, now=1301.0).mode == CONTROL_TRV


class TestHoldAction:
    """Hold keeps the TRV on the last regulated command – but only while the
    TRV still has one.  Otherwise the TRV gets the room target directly."""

    def test_hold_sends_nothing(self):
        r = _expired(CONTROL_HOLD)
        d = _resolve(r, None, now=1301.0)
        assert d.mode == CONTROL_HOLD
        assert d.room_temp_c is None

    def test_frost_protection_overrides_hold(self):
        """An open window must still lower the TRV."""
        r = _expired(CONTROL_HOLD)
        d = _resolve(r, None, now=1301.0, frost_protection=True)
        assert d.mode == CONTROL_TRV

    def test_trv_keeps_control_after_frost_protection(self):
        """Holding after the window closed would leave the TRV on the frost target."""
        r = _expired(CONTROL_HOLD)
        _resolve(r, None, now=1301.0, frost_protection=True)
        d = _resolve(r, None, now=1361.0, frost_protection=False)
        assert d.mode == CONTROL_TRV

    def test_unknown_trv_command_is_not_held(self):
        """After HVAC OFF the proxy no longer knows what the TRV holds."""
        r = _expired(CONTROL_HOLD)
        d = _resolve(r, None, now=1301.0, trv_command_known=False)
        assert d.mode == CONTROL_TRV

    def test_returning_room_sensor_ends_direct_control(self):
        r = _expired(CONTROL_HOLD)
        _resolve(r, None, now=1301.0, frost_protection=True)
        assert _resolve(r, 21.0, now=1361.0).mode == CONTROL_ROOM_SENSOR
        # The next outage may hold the freshly regulated command again.
        assert _resolve(r, None, now=1700.0).mode == CONTROL_HOLD


class TestModeChange:
    """``mode_changed`` tells the regulation cycle to reset the PI state."""

    def test_steady_room_sensor_is_no_change(self):
        r = _expired(CONTROL_TRV)
        assert _resolve(r, 20.6, now=1060.0).mode_changed is False

    def test_bridged_gap_is_no_change(self):
        """A short Zigbee gap must not wipe the integral."""
        r = _expired(CONTROL_TRV)
        assert _resolve(r, None, now=1100.0).mode_changed is False

    def test_expired_grace_is_a_change(self):
        r = _expired(CONTROL_TRV)
        assert _resolve(r, None, now=1301.0).mode_changed is True
        assert _resolve(r, None, now=1361.0).mode_changed is False

    def test_returning_room_sensor_is_a_change(self):
        r = _expired(CONTROL_BACKUP_SENSOR)
        _resolve(r, None, now=1301.0, backup_temp=19.0)
        assert _resolve(r, 21.0, now=1361.0).mode_changed is True

    def test_backup_sensor_dropping_out_is_a_change(self):
        r = _expired(CONTROL_BACKUP_SENSOR)
        _resolve(r, None, now=1301.0, backup_temp=19.0)
        assert _resolve(r, None, now=1361.0, backup_temp=None).mode_changed is True


class TestDefaultGraceValue:
    """Verify the default sensor grace constant."""

    def test_default_is_300_seconds(self):
        assert DEFAULT_SENSOR_GRACE_S == 300
