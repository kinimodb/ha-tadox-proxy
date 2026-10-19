"""Internal state-machine controllers and decision logic for TadoXProxyClimate.

These classes and functions hold the climate entity's decision logic and can
be tested independently of Home Assistant.  They only import HA helpers
lazily (inside methods that schedule timers), so a plain ``import`` in tests
works without an HA bootstrap.

Architecture
------------
- ``WindowAutomationController``  – window-open/close delays & state
- ``PresenceAutomationController`` – presence-away delay, guest mode & state
- ``BoostController``             – boost timer & pre-boost state
- ``FollowPhysicalController``    – pure-logic helper (no state, static method)
- ``ControlModeResolver``         – room-sensor grace period & sensor-loss action
- ``decide_send``                 – send / rate-limit decision (pure function)
- ``preset_target_c``             – preset → room target mapping (pure function)
- ``preset_after_restart``        – preset to start with after a restart (pure function)
- ``SavedState``                  – lightweight snapshot dataclass
- ``is_binary_sensor_transition``  – window/presence event filter (pure function)
"""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from .parameters import PresetConfig

_LOGGER = logging.getLogger(__name__)

# Type alias for the cancel-callback returned by async_call_later
_CancelFn = Callable[[], None]
# Signature of async_call_later (or test stub)
_CallLaterFn = Callable[[Any, float, Callable], _CancelFn]

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
# the proxy in frost protection; "off" switches the TRV off instead of
# sending the frost protection target.
WINDOW_ACTION_FROST = "frost_protection"
WINDOW_ACTION_OFF = "off"
WINDOW_ACTIONS = (WINDOW_ACTION_FROST, WINDOW_ACTION_OFF)
DEFAULT_WINDOW_ACTION = WINDOW_ACTION_FROST


@dataclass
class SavedState:
    """Snapshot of preset and temperature for later restoration."""

    preset: str | None = None
    temp: float | None = None


# ---------------------------------------------------------------------------
# Window automation
# ---------------------------------------------------------------------------

class WindowAutomationController:
    """Manages window-sensor automation with configurable open/close delays.

    State transitions::

        idle ──(open)──► open_pending ──(delay)──► active
                ▲                                      │
                │        (close)                       │
                └───────── close_pending ◄─────────────┘
                                │ (delay expires)
                                ▼
                              idle
    """

    def __init__(self) -> None:
        self.is_active: bool = False
        # The window action switched the TRV off (instead of sending the
        # frost target) and it has not been switched on since.  Outlives the
        # window automation: once the window closes, switching on is due.
        self.trv_off: bool = False
        # Switching off failed: frost protection takes over until the window
        # opens again, instead of an off command every cycle.
        self.trv_off_failed: bool = False
        self._open_timer: _CancelFn | None = None
        self._close_timer: _CancelFn | None = None
        self._saved: SavedState = SavedState()

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def close_delay_active(self) -> bool:
        """True while the post-close restoration timer is running."""
        return self._close_timer is not None

    def get_saved(self) -> SavedState:
        """Return a copy of the currently saved preset/temperature."""
        return SavedState(preset=self._saved.preset, temp=self._saved.temp)

    def trv_off_due(self, action: str) -> bool:
        """True when the window action *action* should switch the TRV off now."""
        return (
            self.is_active
            and action == WINDOW_ACTION_OFF
            and not self.trv_off
            and not self.trv_off_failed
        )

    def trv_on_due(self) -> bool:
        """True when the window has closed but the TRV is still off."""
        return self.trv_off and not self.is_active

    # ------------------------------------------------------------------
    # TRV switched off by the window action
    # ------------------------------------------------------------------

    def mark_trv_off(self) -> None:
        """Record the TRV as off – before the command is sent, so a window
        that closes meanwhile still gets the TRV switched on again."""
        self.trv_off = True

    def fail_trv_off(self) -> None:
        """Switching off failed: frost protection takes over until the window
        opens again (no off command every cycle)."""
        self.trv_off = False
        self.trv_off_failed = self.is_active

    def mark_trv_on(self) -> None:
        """Record that the TRV heats again."""
        self.trv_off = False

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def handle_window_opened(
        self,
        hass: Any,
        delay_s: float,
        on_open_action: Callable,
        *,
        call_later: _CallLaterFn | None = None,
    ) -> None:
        """React to a window-opened event.

        Special case: if a close-delay timer is running (window reopened
        during the restore countdown), cancel that timer and stay in frost
        protection without restarting the open-delay countdown.

        If frost protection is already active (e.g. sensor flicker
        on → unavailable → on), do nothing: a new open timer would make the
        window action overwrite the saved pre-open preset.
        """
        if self._close_timer is not None:
            self._close_timer()
            self._close_timer = None
            _LOGGER.debug("Window reopened during close delay – staying in frost protection")
            return

        if self.is_active:
            _LOGGER.debug("Window open event ignored – frost protection already active")
            return

        # Keep a pending open delay: a repeated "on" (e.g. after a brief
        # unavailable) must not push the action further out.
        if self._open_timer is not None:
            _LOGGER.debug("Window open event ignored – open delay already running")
            return
        _cl = call_later or _get_call_later()
        self._open_timer = _cl(hass, delay_s, on_open_action)
        _LOGGER.debug("Window opened – frost-protection action in %ds", delay_s)

    def handle_window_closed(
        self,
        hass: Any,
        close_delay_s: float,
        on_timer_expire: Callable,
        *,
        call_later: _CallLaterFn | None = None,
    ) -> bool:
        """React to a window-closed event.

        Returns ``True`` when the caller should trigger an *immediate* restore
        (``close_delay_s == 0`` and the window-open mode was active).
        Returns ``False`` in all other cases (timer scheduled, nothing to do).
        """
        if self._open_timer is not None:
            self._open_timer()
            self._open_timer = None
        # Keep a pending close delay: a repeated "off" (e.g. after a brief
        # unavailable) must not push the restore further out.
        if self._close_timer is not None:
            _LOGGER.debug("Window close event ignored – close delay already running")
            return False

        if not self.is_active:
            # Window closed before the open-delay fired – nothing to restore.
            return False

        if close_delay_s > 0:
            _cl = call_later or _get_call_later()
            self._close_timer = _cl(hass, close_delay_s, on_timer_expire)
            _LOGGER.debug("Window closed – restoring previous preset in %ds", close_delay_s)
            return False

        # Zero delay: caller should restore immediately (synchronously)
        return True

    # ------------------------------------------------------------------
    # State mutations called by the climate entity
    # ------------------------------------------------------------------

    def activate(self, preset: str, temp: float | None) -> None:
        """Record the pre-open state and mark window automation as active."""
        self._open_timer = None
        self._saved = SavedState(preset=preset, temp=temp)
        self.is_active = True

    def restore(self) -> SavedState:
        """Clear active state and return the saved preset/temp for restoration."""
        saved = SavedState(preset=self._saved.preset, temp=self._saved.temp)
        self._close_timer = None
        self._saved = SavedState()
        self.is_active = False
        self.trv_off_failed = False
        return saved

    def update_saved(self, preset: str, temp: float | None) -> None:
        """Update the saved preset/temp without changing active state.

        Used when the user changes preset while frost protection is active
        so that the new preset is restored when the window closes.
        """
        self._saved = SavedState(preset=preset, temp=temp)

    def cancel_all(self) -> None:
        """Cancel all timers and reset to idle state (e.g. user override)."""
        if self._open_timer:
            self._open_timer()
            self._open_timer = None
        if self._close_timer:
            self._close_timer()
            self._close_timer = None
        self.is_active = False
        self.trv_off_failed = False
        self._saved = SavedState()


# ---------------------------------------------------------------------------
# Presence automation
# ---------------------------------------------------------------------------

class PresenceAutomationController:
    """Manages presence-sensor automation with configurable away/home delays.

    State transitions::

        home ──(away)──► away_pending ──(delay)──► active ──(home)──► home_pending ──(delay)──► home
          ▲                                           │  (away cancels home timer)                 │
          └───────────────────────────────────────────┘                                            │
          ▲                                                                                       │
          └───────────────────────────────────────────────────────────────────────────────────────┘

    Guest mode suppresses the away branch entirely: while it is on, the
    sensor reporting "nobody home" is ignored so guests get normal heating.
    """

    def __init__(self) -> None:
        self.is_active: bool = False
        self.guest_mode: bool = False
        self._away_timer: _CancelFn | None = None
        self._home_timer: _CancelFn | None = None
        self._saved: SavedState = SavedState()

    def set_guest_mode(self, enabled: bool) -> bool:
        """Enable or disable guest mode.

        Enabling cancels any pending away/home timer and returns ``True``
        when presence-away mode is active, so the caller restores the saved
        preset immediately (the user switched explicitly – no home delay).

        Disabling never restores anything and returns ``False``; the caller
        re-evaluates the presence sensor and re-arms the away timer if
        nobody is home.
        """
        self.guest_mode = enabled
        if not enabled:
            return False
        self.cancel_timer()
        return self.is_active

    def handle_presence_away(
        self,
        hass: Any,
        delay_s: float,
        on_away_action: Callable,
        *,
        call_later: _CallLaterFn | None = None,
    ) -> None:
        """React to a presence-away event; schedule the away action.

        If a home-delay timer is pending (presence flickered Home then back to
        Away), cancel it so that the restore never fires and away stays active.

        If already active (rooms already in AWAY mode), only cancel the home
        timer – do **not** schedule a new away timer, because that would
        overwrite the saved pre-away state with the current AWAY preset.

        While guest mode is on, the event is ignored completely.
        """
        if self.guest_mode:
            _LOGGER.debug("Presence away event ignored – guest mode active")
            return

        # Cancel any pending home-delay timer (flicker protection)
        if self._home_timer is not None:
            self._home_timer()
            self._home_timer = None
            _LOGGER.debug("Presence away during home delay – cancelled restore, staying away")

        # Already in away mode: nothing more to do.  Starting a new away
        # timer would cause _async_presence_away_action to overwrite the
        # saved preset with PRESET_AWAY, destroying the original state.
        if self.is_active:
            _LOGGER.debug("Presence away event ignored – already active")
            return

        # Keep a pending away delay: a repeated "off" (e.g. after a brief
        # unavailable) must not push the action further out.
        if self._away_timer is not None:
            _LOGGER.debug("Presence away event ignored – away delay already running")
            return
        _cl = call_later or _get_call_later()
        self._away_timer = _cl(hass, delay_s, on_away_action)
        _LOGGER.debug("Presence away – switching to AWAY preset in %ds", delay_s)

    def handle_presence_home(
        self,
        hass: Any = None,
        delay_s: float = 0,
        on_home_action: Callable | None = None,
        *,
        call_later: _CallLaterFn | None = None,
    ) -> bool:
        """React to a presence-home event; cancel any pending away timer.

        When *delay_s* is 0 (or the controller is not active), behaves as
        before: returns ``True`` when the caller should trigger an immediate
        restore (presence-away mode was active).

        When *delay_s* > 0 **and** the controller is active, a home-delay
        timer is scheduled instead of restoring immediately.  The method
        returns ``False`` in that case so the caller does **not** restore yet.
        """
        if self._away_timer is not None:
            self._away_timer()
            self._away_timer = None

        if not self.is_active:
            return False

        if delay_s > 0 and on_home_action is not None:
            # Keep a pending home delay: a repeated "on" (e.g. after a brief
            # unavailable) must not push the restore further out.
            if self._home_timer is not None:
                _LOGGER.debug("Presence home event ignored – home delay already running")
                return False
            _cl = call_later or _get_call_later()
            self._home_timer = _cl(hass, delay_s, on_home_action)
            _LOGGER.debug("Presence home – restoring preset in %ds", delay_s)
            return False

        # No delay: immediate restore
        return True

    def activate(self, preset: str, temp: float | None) -> None:
        """Record the pre-away state and mark presence automation as active."""
        self._away_timer = None
        self._saved = SavedState(preset=preset, temp=temp)
        self.is_active = True

    def restore(self) -> SavedState:
        """Clear active state and return the saved preset/temp for restoration."""
        if self._home_timer is not None:
            self._home_timer()
            self._home_timer = None
        saved = SavedState(preset=self._saved.preset, temp=self._saved.temp)
        self._saved = SavedState()
        self.is_active = False
        return saved

    def update_saved(self, preset: str, temp: float | None) -> None:
        """Update the saved preset/temp without changing active state.

        Used when an external preset change arrives while away mode is active
        so that the new preset is restored when presence returns.
        """
        self._saved = SavedState(preset=preset, temp=temp)

    def cancel_timer(self) -> None:
        """Cancel the pending away timer without changing the active flag."""
        if self._away_timer is not None:
            self._away_timer()
            self._away_timer = None
        if self._home_timer is not None:
            self._home_timer()
            self._home_timer = None


# ---------------------------------------------------------------------------
# Boost timer
# ---------------------------------------------------------------------------

class BoostController:
    """Holds the boost expiry timer and the state to return to afterwards.

    ``saved`` is the pre-boost preset/temperature; the climate entity decides
    when to update it (re-entering boost while boosting must keep the
    original one, otherwise boost would restore into boost).
    """

    def __init__(self) -> None:
        self.end_ts: float = 0.0
        self.saved: SavedState = SavedState(preset=PRESET_COMFORT)
        self._cancel: _CancelFn | None = None

    @property
    def is_active(self) -> bool:
        """True while the boost timer is running."""
        return self._cancel is not None

    def remaining_minutes(self, now: float | None = None) -> int:
        """Return the remaining boost minutes, rounded up (0 when inactive)."""
        if self._cancel is None:
            return 0
        _now = now if now is not None else time.time()
        return math.ceil(max(0.0, self.end_ts - _now) / 60)

    def save(self, preset: str, temp: float | None) -> None:
        """Record the preset/temperature to revert to when the boost ends."""
        self.saved = SavedState(preset=preset, temp=temp)

    def start(
        self,
        hass: Any,
        duration_s: float,
        on_expire: Callable,
        *,
        call_later: _CallLaterFn | None = None,
        now: float | None = None,
    ) -> None:
        """Start the expiry timer, replacing any timer that is still running."""
        self.cancel()
        self.end_ts = (now if now is not None else time.time()) + duration_s
        _cl = call_later or _get_call_later()
        self._cancel = _cl(hass, duration_s, on_expire)

    def cancel(self) -> None:
        """Stop a running boost timer (no-op when inactive)."""
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
            self.end_ts = 0.0

    def interrupt(self, preset: str, temp: float | None) -> SavedState:
        """Hand over to window/presence automation and return what it saves.

        A running boost is cancelled and its pre-boost state returned, so the
        automation later restores what was active before the boost.  Without
        a running boost the given current preset/temperature is returned.
        """
        if self._cancel is None:
            return SavedState(preset=preset, temp=temp)
        self.cancel()
        return SavedState(preset=self.saved.preset, temp=self.saved.temp)

    def expire(self) -> SavedState:
        """Mark the timer as fired and return the pre-boost state."""
        self._cancel = None
        self.end_ts = 0.0
        return SavedState(preset=self.saved.preset, temp=self.saved.temp)


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
    counted from the first cycle.

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
            # waits, as before.
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
) -> SendDecision:
    """Decide whether *target_c* is sent to the TRV in this cycle.

    *baseline_c* is the setpoint the TRV is believed to hold.  ``None`` means
    unknown – do NOT substitute 0.0: the urgent-decrease check
    (target < baseline - threshold) would then always be False for realistic
    temperatures and suppress a needed fast cooldown.  Without a baseline the
    command is sent to establish one.

    *last_command_ts* is 0 before the first command.  *overlay_refresh_s* > 0
    resends an unchanged setpoint once that many seconds have passed.

    Reasons: ``no_baseline``, ``already_at_target``, ``overlay_refresh``,
    ``urgent_decrease``, ``normal_update`` and ``rate_limited(<n>s)``.
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

    if abs(target_c - baseline_c) < min_change_c:
        # Overlay refresh keeps timer-based overlays (cloud-API integrations)
        # alive by resending the same setpoint.
        if overlay_refresh_s > 0 and time_since_last >= overlay_refresh_s:
            return SendDecision(True, "overlay_refresh")
        return SendDecision(False, "already_at_target")

    if is_rate_limited:
        if target_c < baseline_c - urgent_decrease_c:
            return SendDecision(True, "urgent_decrease")
        return SendDecision(False, rate_limited_reason)

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
# Preset after a restart (pure logic)
# ---------------------------------------------------------------------------

class RestartState(NamedTuple):
    """Preset the climate entity starts with after a restart or reload."""

    preset: str
    # Temperature of the Manual preset (PRESET_NONE); None for other presets.
    manual_c: float | None
    # The window was open before the restart and still is: frost protection
    # resumes at once, with *preset* saved for when the window closes.
    resume_window: bool
    # The window action had switched the TRV off and the window has closed
    # since: the TRV must be switched on again.
    switch_trv_on: bool


def preset_after_restart(
    preset: str | None,
    manual_c: float | None,
    window_saved: SavedState,
    *,
    window_open: bool | None,
    trv_off: bool = False,
) -> RestartState:
    """Return the preset to start with after a restart.

    *preset* and *manual_c* are the restored preset and manual temperature,
    *window_saved* the preset an open window had saved for later (empty when
    no window was open) and *trv_off* whether the window action had switched
    the TRV off.  *window_open* is the window sensor's state now – None while
    it has not reported yet, which keeps frost protection like a sensor that
    drops out while the window is open.
    """
    resume_window = False
    if preset == PRESET_FROST_PROTECTION and window_saved.preset is not None:
        preset, manual_c = window_saved.preset, window_saved.temp
        resume_window = window_open is not False
    # Boost has lost its timer; frost protection without a saved preset (or
    # saved by mistake) has no window to end it.
    if preset not in (PRESET_COMFORT, PRESET_ECO, PRESET_AWAY, PRESET_NONE):
        preset = PRESET_COMFORT
    return RestartState(
        preset,
        manual_c if preset == PRESET_NONE else None,
        resume_window,
        trv_off and not resume_window,
    )


# ---------------------------------------------------------------------------
# Binary sensor event filter (pure logic)
# ---------------------------------------------------------------------------

def is_binary_sensor_transition(old_state: str | None, new_state: str | None) -> bool:
    """Return ``True`` if a window/presence sensor event is a real state change.

    Ignored are events without a usable new state (missing, ``unavailable``,
    ``unknown``) and attribute-only updates where the state did not change –
    many Zigbee sensors re-publish battery/link quality every few minutes,
    which would otherwise restart the open/away delay timers indefinitely.
    """
    if new_state is None or new_state in ("unavailable", "unknown"):
        return False
    return old_state != new_state


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get_call_later() -> _CallLaterFn:
    """Return HA's ``async_call_later`` (imported lazily to stay HA-free at module level)."""
    from homeassistant.helpers.event import async_call_later  # noqa: PLC0415
    return async_call_later
