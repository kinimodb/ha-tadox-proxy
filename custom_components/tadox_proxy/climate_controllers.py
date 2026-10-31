"""Decision logic for TadoXProxyClimate that stays importable without HA.

Which preset the room runs lives in ``preset_layers``; this module holds the
rest of the climate entity's decisions:

- ``FollowPhysicalController``    – pure-logic helper (no state, static method)
- ``ControlModeResolver``         – room-sensor grace period & sensor-loss action
- ``trv_drifted_setpoint``       – setpoint the TRV left our command for (pure function)
- ``decide_send``                 – send / rate-limit decision (pure function)
- ``preset_target_c``             – preset → room target mapping (pure function)
- ``NeedsHeat``                   – needs heat for a boiler: hysteresis on error_c
- ``safe_float``                  – tolerant number parsing for HA state values
"""
from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from .parameters import PresetConfig

_LOGGER = logging.getLogger(__name__)

# Preset names.  The HA built-ins are identical to the PRESET_* strings in
# homeassistant.components.climate (stable, persisted in state attributes);
# they are redefined here so this module stays importable without HA.
PRESET_NONE = "none"
PRESET_COMFORT = "comfort"
PRESET_ECO = "eco"
PRESET_BOOST = "boost"
PRESET_AWAY = "away"
PRESET_FROST_PROTECTION = "frost_protection"

# What an open window does to the TRV (option ``window_action``).  Both keep
# the active preset at frost protection; "off" switches the TRV off instead
# of sending the frost protection target.
WINDOW_ACTION_FROST = "frost_protection"
WINDOW_ACTION_OFF = "off"
WINDOW_ACTIONS = (WINDOW_ACTION_FROST, WINDOW_ACTION_OFF)
DEFAULT_WINDOW_ACTION = WINDOW_ACTION_FROST


def safe_float(value: Any) -> float | None:
    """Convert value to float, returning None for non-finite or unparseable values."""
    if value is None:
        return None
    try:
        f = float(value)
    except (ValueError, TypeError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------
# Follow-physical helper (pure logic, no state)
# ---------------------------------------------------------------------------

class FollowPhysicalController:
    """Pure-logic helper for detecting physical Tado setpoint changes.

    Contains no mutable state – all inputs are passed per call, making it
    trivially testable without any mocking.
    """

    @staticmethod
    def should_follow(
        tado_setpoint: float,
        last_sent: float | None,
        last_sent_ts: float,
        threshold_c: float,
        grace_s: float,
        now: float | None = None,
    ) -> bool:
        """Return ``True`` if the Tado change looks like physical user input.

        Returns ``False`` when:

        - ``last_sent`` is ``None`` (no baseline yet).
        - The new setpoint is within ``threshold_c`` of our last command.
        - We are within ``grace_s`` of our last command (Tado still
          acknowledging via Thread/cloud).
        """
        if last_sent is None:
            return False
        if abs(tado_setpoint - last_sent) <= threshold_c:
            return False
        _now = now if now is not None else time.time()
        if _now - last_sent_ts < grace_s:
            return False
        return True


# ---------------------------------------------------------------------------
# Control mode: room sensor grace period and sensor-loss action
# ---------------------------------------------------------------------------

# How the proxy drives the TRV in a regulation cycle.  The sensor-loss action
# (option ``sensor_loss_action``) names the mode used once the room sensor's
# grace period has expired.
CONTROL_ROOM_SENSOR = "room_sensor"      # regulate on the room sensor
CONTROL_BACKUP_SENSOR = "backup_sensor"  # regulate on the backup room sensor
CONTROL_TRV = "trv"                      # send the room target, the TRV regulates
CONTROL_HOLD = "hold"                    # send nothing, the TRV keeps its command

SENSOR_LOSS_ACTIONS = (CONTROL_HOLD, CONTROL_BACKUP_SENSOR, CONTROL_TRV)
# Entries created before the option existed keep holding the last command;
# the config flow stores NEW_ENTRY_SENSOR_LOSS_ACTION for new entries.
DEFAULT_SENSOR_LOSS_ACTION = CONTROL_HOLD
NEW_ENTRY_SENSOR_LOSS_ACTION = CONTROL_TRV


class ControlDecision(NamedTuple):
    """Outcome of the control-mode decision for one regulation cycle."""

    mode: str
    # Temperature the PI regulator runs on; None means do not regulate.
    room_temp_c: float | None
    # The mode differs from the previous cycle: the PI state no longer fits
    # (other sensor, or regulation resumes after a pause) and is reset.
    mode_changed: bool


class ControlModeResolver:
    """Decides each cycle how the proxy controls the TRV.

    Short room-sensor outages are bridged with the last valid reading.  A
    valid reading is stored with the sensor's own timestamp (the
    coordinator's ``room_temp_ts``, i.e. ``last_reported``), so the grace
    period counts from when the sensor last reported, not from when the
    proxy polled it.  Without a sensor timestamp the poll time is used.  A
    sensor that has not reported since startup gets the same grace period,
    counted from the first cycle; frost protection hands control to the TRV
    right away instead of waiting.

    Once the grace period has expired, ``loss_action`` picks the mode:
    regulate on the backup sensor, let the TRV regulate on its own, or hold
    the last command.  Whenever that is not possible the TRV regulates.

    ``degraded`` is True while a bridged value is in use and after the grace
    period has expired.
    """

    def __init__(
        self, grace_s: float, loss_action: str = DEFAULT_SENSOR_LOSS_ACTION
    ) -> None:
        self.grace_s = grace_s
        self.loss_action = loss_action
        self.mode: str | None = None
        self.last_valid: float | None = None
        self.last_valid_ts: float = 0.0
        self.degraded: bool = False
        self._missing_since: float | None = None
        self._expired: bool = False

    def resolve(
        self,
        *,
        room_temp: float | None,
        room_temp_ts: float | None,
        backup_temp: float | None,
        tado_internal_c: float | None,
        frost_protection: bool,
        trv_command_known: bool,
        now: float,
    ) -> ControlDecision:
        """Return the control mode and room temperature for this cycle.

        *trv_command_known* is False while the proxy does not know which
        setpoint the TRV holds (after HVAC OFF).
        """
        mode, temp = self._select(
            self._bridge(room_temp, room_temp_ts, now),
            backup_temp,
            tado_internal_c,
            frost_protection,
            trv_command_known,
        )
        changed = mode != self.mode
        if changed:
            _LOGGER.info("Control mode: %s → %s", self.mode, mode)
        self.mode = mode
        return ControlDecision(mode, temp, changed)

    def missing_reading(self, now: float) -> tuple[float, int] | None:
        """The last valid reading and its age in seconds, while the sensor is missing.

        None while the sensor reports, and for one that never did.  Shown all
        the time, the ever-growing age would make every state write a new
        recorder row.
        """
        if not self.degraded or self.last_valid is None:
            return None
        return self.last_valid, int(now - self.last_valid_ts)

    def _select(
        self,
        room_temp: float | None,
        backup_temp: float | None,
        tado_internal_c: float | None,
        frost_protection: bool,
        trv_command_known: bool,
    ) -> tuple[str, float | None]:
        if not self._expired:
            # Without a reading (startup) or the TRV's own reading the cycle
            # waits – except for frost protection, which must reach the TRV
            # right away.  After that direct command the TRV keeps regulating
            # until the room sensor reports, so the frost target is not left
            # on the TRV (or held) once the window closes.
            if room_temp is None and (frost_protection or self.mode == CONTROL_TRV):
                return CONTROL_TRV, None
            if room_temp is None or tado_internal_c is None:
                return CONTROL_ROOM_SENSOR, None
            return CONTROL_ROOM_SENSOR, room_temp
        if self.loss_action == CONTROL_BACKUP_SENSOR:
            # Feedforward needs the TRV's own reading as well.
            if backup_temp is not None and tado_internal_c is not None:
                return CONTROL_BACKUP_SENSOR, backup_temp
            return CONTROL_TRV, None
        if self.loss_action == CONTROL_HOLD and (
            # Hold only a regulated command: not with an open window, not
            # when the TRV's command is unknown (HVAC was OFF) and not once
            # the TRV got a direct command (e.g. the frost target).
            not frost_protection and trv_command_known and self.mode != CONTROL_TRV
        ):
            return CONTROL_HOLD, None
        return CONTROL_TRV, None

    def _bridge(
        self, room_temp: float | None, room_temp_ts: float | None, now: float
    ) -> float | None:
        """Return the room temperature to regulate on, or None if unusable.

        Updates ``degraded`` and whether the grace period has expired.
        """
        if room_temp is not None:
            self.last_valid = room_temp
            self.last_valid_ts = room_temp_ts or now
            self.degraded = False
            self._missing_since = None
            self._expired = False
            return room_temp

        if self.last_valid is None:
            if self._missing_since is None:
                self._missing_since = now
            if now - self._missing_since > self.grace_s:
                if not self._expired:
                    _LOGGER.warning(
                        "Room sensor has not reported since startup (%ds, grace: %ds)",
                        int(now - self._missing_since), self.grace_s,
                    )
                self._expired = self.degraded = True
            return None

        self.degraded = True
        if now - self.last_valid_ts <= self.grace_s:
            _LOGGER.debug(
                "Room temp unavailable – using last valid %.1f°C (age %ds, grace %ds)",
                self.last_valid, int(now - self.last_valid_ts), self.grace_s,
            )
            return self.last_valid

        if not self._expired:
            _LOGGER.warning(
                "Room sensor unavailable and grace period expired "
                "(last valid: %.1f°C, %ds ago, grace: %ds)",
                self.last_valid,
                int(now - self.last_valid_ts),
                self.grace_s,
            )
        self._expired = True
        return None


# ---------------------------------------------------------------------------
# Send / rate-limit decision (pure logic)
# ---------------------------------------------------------------------------

class SendDecision(NamedTuple):
    """Outcome of the send decision for one regulation cycle."""

    should_send: bool
    reason: str


def _differ_by_at_least(a_c: float, b_c: float, threshold_c: float) -> bool:
    """True if *a_c* and *b_c* are at least *threshold_c* apart.

    Rounded to hundredths first: temperatures come in tenths, and binary
    floats make an exact 0.3 K step (19.9 -> 19.6) 0.2999... apart.
    """
    return round(abs(a_c - b_c), 2) >= threshold_c


def trv_drifted_setpoint(
    *,
    last_sent_c: float | None,
    trv_setpoint_c: float | None,
    last_command_ts: float,
    now: float,
    drift_threshold_c: float,
    grace_s: float,
) -> float | None:
    """Return the setpoint the TRV left our command for, or ``None``.

    The proxy decides what to send by comparing against its own last command
    (always fresh, unlike the coordinator's reading).  But the TRV can leave
    that setpoint without us: someone turns the dial, or a Tado schedule
    writes its own block.  Then our own value says "nothing to do" while the
    TRV heats to something else entirely – an open window held at frost
    protection, with the dial at 22 °C, stayed that way forever.

    ``None`` means the TRV still holds (or is believed to hold) our command,
    and the cycle decides exactly as before.

    *grace_s* keeps the proxy from mistaking the echo of its own command
    (Tado acknowledges via Thread/cloud) for a dial change.  Drift is read
    with the same predicate as follow-Tado, so both features agree on what
    counts as the TRV moving on its own.
    """
    if last_sent_c is None or trv_setpoint_c is None:
        # Nothing of ours to drift away from, or the TRV reports no setpoint
        # (off/unavailable): the plain baseline rules.
        return None
    if FollowPhysicalController.should_follow(
        tado_setpoint=trv_setpoint_c,
        last_sent=last_sent_c,
        last_sent_ts=last_command_ts,
        threshold_c=drift_threshold_c,
        grace_s=grace_s,
        now=now,
    ):
        return trv_setpoint_c
    return None


def decide_send(
    *,
    target_c: float,
    baseline_c: float | None,
    last_command_ts: float,
    now: float,
    min_interval_s: float,
    min_change_c: float,
    urgent_decrease_c: float,
    overlay_refresh_s: float,
    drifted_setpoint_c: float | None = None,
) -> SendDecision:
    """Decide whether *target_c* is sent to the TRV in this cycle.

    *baseline_c* is the setpoint the TRV is believed to hold.  ``None`` means
    unknown – do NOT substitute 0.0: the urgent-decrease check
    (target < baseline - threshold) would then always be False for realistic
    temperatures and suppress a needed fast cooldown.  Without a baseline the
    command is sent to establish one.

    *drifted_setpoint_c* is what the TRV holds instead of our command, when it
    has left it on its own (see ``trv_drifted_setpoint``).  The target then
    has to match both values to count as reached, so a dialled TRV is
    corrected even while our own target stands still.  The correction waits
    for the rate limit: the urgent-decrease bypass stays tied to *baseline_c*,
    our own command, so a TRV that never adopts our setpoint cannot push
    commands past the limit (battery).

    *last_command_ts* is 0 before the first command.  *overlay_refresh_s* > 0
    resends an unchanged setpoint once that many seconds have passed.

    Reasons: ``no_baseline``, ``already_at_target``, ``overlay_refresh``,
    ``urgent_decrease``, ``normal_update``, ``trv_drift`` and
    ``rate_limited(<n>s)``.
    """
    time_since_last = now - last_command_ts
    is_rate_limited = time_since_last < min_interval_s
    rate_limited_reason = f"rate_limited({int(min_interval_s - time_since_last)}s)"

    if baseline_c is None:
        # The very first attempt is sent immediately.  Retries (e.g. after a
        # failed send) honour the rate limiter so a transient TRV/backend
        # outage does not cause command spam every regulation cycle.
        if is_rate_limited and last_command_ts > 0:
            return SendDecision(False, rate_limited_reason)
        return SendDecision(True, "no_baseline")

    at_baseline = not _differ_by_at_least(target_c, baseline_c, min_change_c)
    drifted = drifted_setpoint_c is not None and _differ_by_at_least(
        target_c, drifted_setpoint_c, min_change_c
    )

    if at_baseline and not drifted:
        # Overlay refresh keeps timer-based overlays (cloud-API integrations)
        # alive by resending the same setpoint.
        if overlay_refresh_s > 0 and time_since_last >= overlay_refresh_s:
            return SendDecision(True, "overlay_refresh")
        return SendDecision(False, "already_at_target")

    if is_rate_limited:
        if target_c < baseline_c - urgent_decrease_c:
            return SendDecision(True, "urgent_decrease")
        return SendDecision(False, rate_limited_reason)

    # Only the TRV moved: the send is a correction, not a new target.
    if at_baseline:
        return SendDecision(True, "trv_drift")
    return SendDecision(True, "normal_update")


# ---------------------------------------------------------------------------
# Preset → target mapping (pure logic)
# ---------------------------------------------------------------------------

def preset_target_c(
    preset: str,
    presets: PresetConfig,
    *,
    comfort_c: float,
    manual_c: float,
) -> float:
    """Return the room target for *preset*.

    COMFORT resolves to *comfort_c* and manual mode (``PRESET_NONE``) to
    *manual_c*; every other preset has a fixed target in *presets*.  Unknown
    names fall back to frost protection, the safest target.
    """
    if preset == PRESET_COMFORT:
        return comfort_c
    if preset == PRESET_NONE:
        return manual_c
    fixed = {
        PRESET_ECO: presets.eco_target_c,
        PRESET_BOOST: presets.boost_target_c,
        PRESET_AWAY: presets.away_target_c,
        PRESET_FROST_PROTECTION: presets.frost_protection_target_c,
    }
    return fixed.get(preset, presets.frost_protection_target_c)


# ---------------------------------------------------------------------------
# Needs heat: hysteresis on error_c, for a boiler that runs on demand
# ---------------------------------------------------------------------------

def _above(value_c: float, threshold_c: float) -> bool:
    """True if *value_c* lies above *threshold_c*, compared in hundredths.

    error_c comes in hundredths; binary floats would put a gap of exactly the
    threshold (target 20.3, room 20.0: 0.3000...07) above it.
    """
    return round(value_c - threshold_c, 2) > 0


class NeedsHeat:
    """Whether the room needs heat – the signal for a boiler that runs on demand.

    Follows the regulator's ``error_c`` (target minus room temperature) with
    hysteresis: on once it rises above ``on_above_c``, off once it falls to
    ``off_at_c`` or below, unchanged in between.  With the default thresholds
    the boiler neither starts for a tiny gap nor stops before the room has
    reached its target; a positive ``off_at_c`` stops it a little earlier,
    for radiators whose stored heat finishes the job.

    Off without an ``error_c`` (the proxy is not regulating), while the
    heating is off and while the room sensor is degraded; the next decision
    then starts from off.  The thresholds may change between updates (the
    options apply live); the options flow keeps ``on_above_c`` above
    ``off_at_c``.
    """

    def __init__(self, *, on_above_c: float, off_at_c: float) -> None:
        self.on_above_c = on_above_c
        self.off_at_c = off_at_c
        self.is_on = False

    def update(self, error_c: float | None, *, heating: bool, degraded: bool) -> bool:
        """Decide from the latest published error_c and return the new state.

        Repeating an update with the same values changes nothing, so it may
        run on every state write.
        """
        if error_c is None or not heating or degraded:
            self.is_on = False
        elif _above(error_c, self.on_above_c):
            self.is_on = True
        elif not _above(error_c, self.off_at_c):
            self.is_on = False
        return self.is_on
