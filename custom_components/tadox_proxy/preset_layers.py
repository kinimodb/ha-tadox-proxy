"""Which preset the room runs: the user choice plus ranked layers (ADR 0001).

The room keeps one **user choice** – the preset a person last picked, for
Manual together with its temperature.  An open window, nobody home and a
boost are **layers** that hold the room for a while in a fixed rank: window
over nobody home over boost over the user choice.  The **active preset** is
always derived from these; no layer writes into the user choice.  A boost only
counts down while it heats the room; paused by heating that is off it still
shows as the active preset – it is what holds the room once the heating is
back on.

The module never schedules anything: every event returns ``Effects`` with the
timers to start or cancel, which the caller reports back as elapsed.  This
module stays importable without Home Assistant.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .climate_controllers import (
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_FROST_PROTECTION,
    PRESET_NONE,
    WINDOW_ACTION_OFF,
    preset_target_c,
    safe_float,
)

if TYPE_CHECKING:
    from .parameters import PresetConfig

# Timer ids.  The caller schedules them and reports them back as elapsed.
TIMER_WINDOW_OPEN = "window_open"
TIMER_WINDOW_CLOSE = "window_close"
TIMER_AWAY = "away"
TIMER_HOME = "home"
TIMER_BOOST = "boost"

# State attributes of the climate entity, read back after a restart.
ATTR_USER_PRESET = "user_preset"
ATTR_MANUAL_TARGET = "manual_target_c"
ATTR_WINDOW_OPEN_ACTIVE = "window_open_active"
ATTR_WINDOW_CLOSE_DELAY_ACTIVE = "window_close_delay_active"
ATTR_PRESENCE_AWAY_ACTIVE = "presence_away_active"
ATTR_GUEST_MODE = "guest_mode"
ATTR_WINDOW_TRV_OFF = "window_trv_off"
# Written up to v1.4.x (what an open window kept to restore); read once
# after the update.
ATTR_V14_WINDOW_RESTORE_PRESET = "window_restore_preset"
ATTR_V14_WINDOW_RESTORE_TARGET = "window_restore_target_c"
# Home Assistant's own climate state values (stable, persisted); redefined so
# this module stays importable without HA.
_HA_ATTR_PRESET_MODE = "preset_mode"
_HA_ATTR_TEMPERATURE = "temperature"
_HVAC_OFF = "off"

_ON = "on"
_OFF = "off"

# Named presets a user choice can hold; Manual carries a temperature, a
# boost is a layer.
_CHOOSABLE = (PRESET_COMFORT, PRESET_ECO, PRESET_AWAY, PRESET_FROST_PROTECTION)


@dataclass(frozen=True)
class LayersConfig:
    """Options PresetLayers works with."""

    presets: PresetConfig
    comfort_c: float
    # A temperature a person sets is kept within these limits.
    min_target_c: float
    max_target_c: float
    window_delay_s: float
    window_close_delay_s: float
    away_delay_s: float
    home_delay_s: float
    window_action: str
    # Whether a window / presence sensor is configured at all.
    has_window_sensor: bool
    has_presence_sensor: bool


@dataclass(frozen=True)
class UserChoice:
    """The preset a person last picked; *manual_c* only for Manual."""

    preset: str
    manual_c: float | None = None


@dataclass(frozen=True)
class StartSnapshot:
    """What the room knew before a restart or reload, and its sensors now.

    The first group comes from the state attributes written before; the
    sensor states are raw (None while the entity is missing).
    """

    heating_on: bool = True
    # The active preset shown before the restart.
    preset_mode: str | None = None
    # The user choice; None for a state written by v1.4.x (or no state).
    user_preset: str | None = None
    manual_c: float | None = None
    window_held: bool = False
    nobody_home_held: bool = False
    valve_off_by_window: bool = False
    guest_mode: bool = False
    window_state: str | None = None
    presence_state: str | None = None
    # Only in states written by v1.4.x and older: what an open window kept
    # to restore, and the shown target (before manual_target_c existed).
    window_kept_preset: str | None = None
    window_kept_c: float | None = None
    target_c: float | None = None

    @classmethod
    def from_state(
        cls,
        state: str | None,
        attributes: Mapping[str, Any],
        *,
        window_state: str | None,
        presence_state: str | None,
    ) -> StartSnapshot:
        """Read the climate state written before the restart.

        *state* is the HVAC mode it was written with, None when there is no
        state to restore.  The sensor states are the raw states now.
        """
        if state is None:
            return cls(window_state=window_state, presence_state=presence_state)
        return cls(
            heating_on=state != _HVAC_OFF,
            preset_mode=attributes.get(_HA_ATTR_PRESET_MODE),
            user_preset=attributes.get(ATTR_USER_PRESET),
            manual_c=safe_float(attributes.get(ATTR_MANUAL_TARGET)),
            window_held=attributes.get(ATTR_WINDOW_OPEN_ACTIVE) is True,
            nobody_home_held=attributes.get(ATTR_PRESENCE_AWAY_ACTIVE) is True,
            valve_off_by_window=attributes.get(ATTR_WINDOW_TRV_OFF) is True,
            guest_mode=attributes.get(ATTR_GUEST_MODE) is True,
            window_state=window_state,
            presence_state=presence_state,
            window_kept_preset=attributes.get(ATTR_V14_WINDOW_RESTORE_PRESET),
            window_kept_c=safe_float(attributes.get(ATTR_V14_WINDOW_RESTORE_TARGET)),
            target_c=safe_float(attributes.get(_HA_ATTR_TEMPERATURE)),
        )


@dataclass
class Effects:
    """What the caller has to do after an event."""

    # Timer id → delay in seconds to (re)start it, or None to cancel it.
    timers: dict[str, float | None] = field(default_factory=dict)
    # Trigger reason of the regulation cycle to run now, None for none.
    cycle: str | None = None
    # The state shown in Home Assistant changed.
    changed: bool = False


class PresetLayers:
    """Owns the user choice and the layers, and derives the active preset."""

    def __init__(self, config: LayersConfig) -> None:
        self._config = config
        self._user = UserChoice(PRESET_COMFORT)
        # Last raw state the window sensor reported.
        self._window_state: str | None = None
        self._window_holds = False
        # Last raw state the presence sensor reported.
        self._presence_state: str | None = None
        self._nobody_home_holds = False
        self._guest_mode = False
        self._heating = True
        # The window action switched the valve off and it has not heated
        # since; and switching it off failed for this window opening.
        self._valve_off_by_window = False
        self._valve_off_failed = False
        # Boost time left (None: no boost) and since when it runs (None:
        # paused).
        self._boost_left_s: float | None = None
        self._boost_since: float | None = None
        # Timers the caller runs for this module, and what to tell the
        # caller about them after the current event.
        self._armed: set[str] = set()
        self._timer_ops: dict[str, float | None] = {}
        # Time of the current event, and what it changes is compared with.
        self._now = 0.0
        self._regulation_before: tuple = ()
        self._shown_before: tuple = ()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def pick_preset(self, preset: str, now: float) -> Effects:
        """A person picked *preset*.

        Picking the preset an active layer forces right now does not become
        the user choice: confirming what is shown must not leave the room
        cold later.  It is still a change by a person, so a running close
        delay ends and a boost does not outlive the tap.
        """
        self._begin(now)
        if preset not in (*_CHOOSABLE, PRESET_BOOST):
            return self._effects("preset_change")
        self._change_by_person()
        if preset == PRESET_BOOST:
            self._start_boost()
            return self._effects("preset_change")
        self._end_boost()
        if not self._layer_forces(preset):
            self._user = UserChoice(preset)
        return self._effects("preset_change")

    def set_temperature(self, temp_c: float, now: float) -> Effects:
        """A person set *temp_c*: Manual with that temperature."""
        return self._set_manual(temp_c, now, "set_temperature")

    def valve_changed(self, temp_c: float, now: float) -> Effects:
        """A person turned the dial on the valve to *temp_c*.

        Counts exactly like setting the temperature in the app.
        """
        return self._set_manual(temp_c, now, "follow_tado")

    def window_reported(self, state: str | None, now: float) -> Effects:
        """The window sensor reported *state* (raw, None when missing)."""
        self._begin(now)
        previous, self._window_state = self._window_state, state
        if not _is_transition(previous, state):
            return self._effects(None)
        if state == _ON:
            if self._window_holds:
                # Reopened during the close delay: keeps holding, no new
                # open delay.
                self._disarm(TIMER_WINDOW_CLOSE)
            elif TIMER_WINDOW_OPEN not in self._armed:
                self._delay(TIMER_WINDOW_OPEN, self._config.window_delay_s)
            return self._effects("window_open")
        self._disarm(TIMER_WINDOW_OPEN)
        if self._window_holds and TIMER_WINDOW_CLOSE not in self._armed:
            self._delay(TIMER_WINDOW_CLOSE, self._config.window_close_delay_s)
        return self._effects("window_closed")

    def presence_reported(self, state: str | None, now: float) -> Effects:
        """The presence sensor reported *state* (raw, None when missing)."""
        self._begin(now)
        previous, self._presence_state = self._presence_state, state
        if not _is_transition(previous, state):
            return self._effects(None)
        if state == _OFF:
            if not self._guest_mode:
                # Nobody home during the home delay: the return is off, Away
                # stays.
                self._disarm(TIMER_HOME)
                self._start_away_delay()
            return self._effects("presence_away")
        self._disarm(TIMER_AWAY)
        if self._nobody_home_holds and TIMER_HOME not in self._armed:
            self._delay(TIMER_HOME, self._config.home_delay_s)
        return self._effects("presence_home")

    def set_guest_mode(self, enabled: bool, now: float) -> Effects:
        """Guest mode was switched on or off.

        On: nobody home no longer holds the room, Away ends right away.
        Off: if nobody is home, Away follows after the away delay.
        """
        self._begin(now)
        self._guest_mode = enabled
        if enabled:
            self._disarm(TIMER_AWAY)
            self._disarm(TIMER_HOME)
            self._nobody_home_holds = False
        elif self._presence_state == _OFF:
            self._start_away_delay()
        return self._effects("guest_mode")

    def options_changed(self, config: LayersConfig, now: float) -> Effects:
        """The options changed, e.g. a preset target from its number entity.

        Targets apply right away.  Running delays keep their length; the
        sensors are reload options, so a changed sensor means a fresh module.
        """
        self._begin(now)
        self._config = config
        return self._effects("options_changed")

    def heating_off(self, now: float) -> Effects:
        """The valve confirmed that the heating is off.

        Only a boost pauses; window and presence detection keep following
        their sensors.
        """
        self._begin(now)
        self._heating = False
        return self._effects("hvac_mode_change")

    def heating_on(self, now: float) -> Effects:
        """The heating is switched on again.

        Report it before the heat command: while the window holds the room
        with the window action "off", the valve stays off and no heat
        command goes out (see ``valve_held_off``).
        """
        self._begin(now)
        if not self._heating and self._window_wants_valve_off():
            # Off since the heating was off – and it stays off.
            self._valve_off_by_window = True
        self._heating = True
        return self._effects("hvac_mode_change")

    def valve_switched_off(self, now: float) -> Effects:
        """The off command for the window action goes out now.

        Reported before the command, so a window that closes meanwhile
        still gets the valve switched on again.
        """
        self._begin(now)
        self._valve_off_by_window = True
        return self._effects(None)

    def valve_switch_off_failed(self, now: float) -> Effects:
        """Switching the valve off failed: frost protection takes over until
        the window opens again, instead of an off command every cycle."""
        self._begin(now)
        self._valve_off_by_window = False
        self._valve_off_failed = self._window_holds
        return self._effects(None)

    def heat_command_sent(self, now: float) -> Effects:
        """A command that makes the valve heat went out successfully."""
        self._begin(now)
        self._valve_off_by_window = False
        return self._effects(None)

    def start(self, snapshot: StartSnapshot, now: float) -> Effects:
        """Start after a restart or reload from *snapshot*.

        The user choice survives; a boost does not.  A layer that held the
        room before goes on without a new delay while its sensor does not
        say otherwise – a sensor that has not reported yet is no news.
        """
        self._begin(now)
        self._heating = snapshot.heating_on
        self._guest_mode = snapshot.guest_mode
        self._window_state = snapshot.window_state
        self._presence_state = snapshot.presence_state
        self._valve_off_by_window = snapshot.valve_off_by_window
        if snapshot.user_preset is None:
            self._user, window_held, nobody_home_held = self._upgrade_from_v14(snapshot)
        else:
            self._user = _user_choice(snapshot.user_preset, snapshot.manual_c)
            window_held = snapshot.window_held
            nobody_home_held = snapshot.nobody_home_held
        if self._config.has_window_sensor:
            if window_held and snapshot.window_state != _OFF:
                self._window_holds = True
                # The valve's state is unknown after the restart: the window
                # action switches it off once more.
                self._valve_off_by_window = False
            elif snapshot.window_state == _ON:
                self._delay(TIMER_WINDOW_OPEN, self._config.window_delay_s)
        if self._config.has_presence_sensor and not self._guest_mode:
            if nobody_home_held and snapshot.presence_state != _ON:
                self._nobody_home_holds = True
            elif snapshot.presence_state == _OFF:
                self._start_away_delay()
        return self._effects(None)

    def timer_elapsed(self, timer_id: str, now: float) -> Effects:
        """The timer *timer_id* the caller ran for this module has elapsed."""
        self._begin(now)
        if timer_id not in self._armed:
            # Cancelled or started anew meanwhile: no news.
            return self._effects(None)
        self._armed.discard(timer_id)
        return self._effects(self._elapsed(timer_id))

    # ------------------------------------------------------------------
    # Readable state
    # ------------------------------------------------------------------

    @property
    def active_preset(self) -> str:
        """The preset the room runs on right now."""
        if self._window_holds:
            return PRESET_FROST_PROTECTION
        if self._nobody_home_holds:
            return PRESET_AWAY
        if self._boost_left_s is not None:
            return PRESET_BOOST
        return self._user.preset

    @property
    def user_choice(self) -> UserChoice:
        """The preset a person last picked; comes back once no layer holds."""
        return self._user

    @property
    def window_holds(self) -> bool:
        """An open window holds the room (until its close delay is over)."""
        return self._window_holds

    @property
    def close_delay_running(self) -> bool:
        """The window has closed and its close delay is running."""
        return TIMER_WINDOW_CLOSE in self._armed

    @property
    def nobody_home_holds(self) -> bool:
        """Nobody home holds the room (until the home delay is over)."""
        return self._nobody_home_holds

    @property
    def guest_mode(self) -> bool:
        """Guest mode keeps nobody home from holding the room."""
        return self._guest_mode

    def attributes(self) -> dict[str, object]:
        """State attributes to show and to persist for a restart."""
        attrs: dict[str, object] = {
            ATTR_USER_PRESET: self._user.preset,
            ATTR_WINDOW_OPEN_ACTIVE: self._window_holds,
            ATTR_WINDOW_CLOSE_DELAY_ACTIVE: self.close_delay_running,
            ATTR_PRESENCE_AWAY_ACTIVE: self._nobody_home_holds,
            ATTR_GUEST_MODE: self._guest_mode,
        }
        if self._user.preset == PRESET_NONE:
            attrs[ATTR_MANUAL_TARGET] = self._user.manual_c
        if self._valve_off_by_window:
            attrs[ATTR_WINDOW_TRV_OFF] = True
        return attrs

    def boost_remaining_min(self, now: float) -> int:
        """Minutes of boost left at *now*, rounded up; 0 without a boost."""
        if self._boost_left_s is None:
            return 0
        left = self._boost_left_s
        if self._boost_since is not None:
            left -= now - self._boost_since
        return math.ceil(max(0.0, left) / 60)

    @property
    def valve_off_due(self) -> bool:
        """The window action should switch the valve off now."""
        return self._heating and self._window_wants_valve_off() and not self._valve_off_by_window

    @property
    def valve_on_due(self) -> bool:
        """The valve the window had switched off should heat again now."""
        return self._heating and self._valve_off_by_window and not self._window_wants_valve_off()

    @property
    def valve_held_off(self) -> bool:
        """The window keeps the valve off: no setpoint may go out, since
        every setpoint command makes the valve heat."""
        return self._valve_off_by_window and self._window_wants_valve_off()

    @property
    def valve_off_by_window(self) -> bool:
        """The window action switched the valve off and it has not heated
        since (also after the window let go, until it is switched on)."""
        return self._valve_off_by_window

    @property
    def room_target_c(self) -> float:
        """The room target of the active preset."""
        return preset_target_c(
            self.active_preset,
            self._config.presets,
            comfort_c=self._config.comfort_c,
            # Manual is only active as a user choice, which carries it.
            manual_c=self._user.manual_c if self._user.manual_c is not None else 0.0,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _arm(self, timer_id: str, delay_s: float) -> None:
        self._armed.add(timer_id)
        self._timer_ops[timer_id] = delay_s

    def _disarm(self, timer_id: str) -> None:
        if timer_id in self._armed:
            self._armed.discard(timer_id)
            self._timer_ops[timer_id] = None

    def _set_manual(self, temp_c: float, now: float, trigger: str) -> Effects:
        self._begin(now)
        self._change_by_person()
        self._end_boost()
        limited = max(self._config.min_target_c, min(self._config.max_target_c, temp_c))
        self._user = UserChoice(PRESET_NONE, limited)
        return self._effects(trigger)

    def _change_by_person(self) -> None:
        """A person changed something: a running close delay ends now – the
        person is obviously back in the room."""
        if TIMER_WINDOW_CLOSE in self._armed:
            self._end_window()

    def _layer_forces(self, preset: str) -> bool:
        """*preset* is what an active layer shows right now, so picking it
        only confirms that layer.

        Read after ``_change_by_person``: a close delay that just ended no
        longer forces anything, which makes the same tap a real choice.
        """
        return (preset == PRESET_FROST_PROTECTION and self._window_holds) or (
            preset == PRESET_AWAY and self._nobody_home_holds
        )

    def _upgrade_from_v14(self, snapshot: StartSnapshot) -> tuple[UserChoice, bool, bool]:
        """User choice, window held and nobody home held from a v1.4.x state.

        v1.4.x kept no user choice: an open window kept the preset to restore.
        Away – shown, or kept by the window – was either picked by hand or
        held by nobody home, which presence_away_active tells apart.  What
        nobody home would bring back was not written, so Comfort comes back.
        """
        preset, manual_c = snapshot.preset_mode, snapshot.manual_c
        if manual_c is None and snapshot.heating_on:
            # Before manual_target_c existed; while off the shown target is
            # the 5 °C of the off state.
            manual_c = snapshot.target_c
        window_held = (
            preset == PRESET_FROST_PROTECTION and snapshot.window_kept_preset is not None
        )
        if window_held:
            preset, manual_c = snapshot.window_kept_preset, snapshot.window_kept_c
        nobody_home_held = preset == PRESET_AWAY and snapshot.nobody_home_held
        if nobody_home_held or preset in (PRESET_BOOST, PRESET_FROST_PROTECTION):
            # A boost lost its timer; frost protection had no window left.
            preset = PRESET_COMFORT
        return _user_choice(preset, manual_c), window_held, nobody_home_held

    def _end_window(self) -> None:
        """The window lets go of the room."""
        self._disarm(TIMER_WINDOW_CLOSE)
        self._window_holds = False
        self._valve_off_failed = False

    def _window_wants_valve_off(self) -> bool:
        return (
            self._window_holds
            and self._config.window_action == WINDOW_ACTION_OFF
            and not self._valve_off_failed
        )

    def _start_away_delay(self) -> None:
        if not self._nobody_home_holds and TIMER_AWAY not in self._armed:
            self._delay(TIMER_AWAY, self._config.away_delay_s)

    def _delay(self, timer_id: str, delay_s: float) -> None:
        """Act after *delay_s*: via a timer, or right away for no delay."""
        if delay_s > 0:
            self._arm(timer_id, delay_s)
        else:
            self._elapsed(timer_id)

    def _elapsed(self, timer_id: str) -> str:
        """Act on the end of *timer_id*'s delay; return the cycle trigger."""
        if timer_id == TIMER_WINDOW_OPEN:
            # Only if the window is still open: a glitchy reading in between
            # must not flip the room into frost protection.
            if self._window_state == _ON:
                self._window_holds = True
            return "window_open"
        if timer_id == TIMER_WINDOW_CLOSE:
            self._end_window()
            return "window_closed"
        if timer_id == TIMER_AWAY:
            # Only if the sensor still reports nobody home.
            if self._presence_state == _OFF:
                self._nobody_home_holds = True
            return "presence_away"
        if timer_id == TIMER_HOME:
            # Skipped if the sensor reports nobody home again or is gone; a
            # sensor that is just unavailable does not undo the return.
            if self._presence_state not in (_OFF, None):
                self._nobody_home_holds = False
            return "presence_home"
        self._end_boost()
        return "boost_expired"

    def _start_boost(self) -> None:
        """Start the boost with its full time (also when one is running)."""
        self._disarm(TIMER_BOOST)
        self._boost_left_s = self._config.presets.boost_duration_min * 60
        self._boost_since = None

    def _end_boost(self) -> None:
        self._disarm(TIMER_BOOST)
        self._boost_left_s = None
        self._boost_since = None

    def _settle_boost(self) -> None:
        """Run the boost while it is what heats the room, else pause it."""
        if self._boost_left_s is None:
            return
        runs = self._heating and not self._window_holds and not self._nobody_home_holds
        if runs and self._boost_since is None:
            self._boost_since = self._now
            self._arm(TIMER_BOOST, self._boost_left_s)
        elif not runs and self._boost_since is not None:
            self._boost_left_s -= self._now - self._boost_since
            self._boost_since = None
            self._disarm(TIMER_BOOST)
            if self._boost_left_s <= 0:
                # Its time was up just as a layer took over.
                self._end_boost()

    def _regulation_view(self) -> tuple:
        """What a regulation cycle depends on."""
        return (
            self.active_preset,
            self.room_target_c,
            self.valve_off_due,
            self.valve_on_due,
        )

    def _shown_view(self) -> tuple:
        """What Home Assistant shows."""
        return (
            self._regulation_view(),
            self.attributes(),
            self._boost_left_s is not None,
            self._boost_since is not None,
        )

    def _begin(self, now: float) -> None:
        """Start an event at *now*: remember what to compare with at its end."""
        self._now = now
        self._timer_ops = {}
        self._regulation_before = self._regulation_view()
        self._shown_before = self._shown_view()

    def _effects(self, trigger: str | None) -> Effects:
        """End the current event; *trigger* names the regulation cycle it
        asks for if the room target or the valve changed (None: none)."""
        self._settle_boost()
        return Effects(
            timers=self._timer_ops,
            cycle=trigger if self._regulation_view() != self._regulation_before else None,
            changed=self._shown_view() != self._shown_before,
        )


def _user_choice(preset: str | None, manual_c: float | None) -> UserChoice:
    """The user choice read back after a restart; Comfort if unusable."""
    if preset in _CHOOSABLE:
        return UserChoice(preset)
    if preset == PRESET_NONE and manual_c is not None:
        return UserChoice(PRESET_NONE, manual_c)
    return UserChoice(PRESET_COMFORT)


def _is_transition(previous: str | None, state: str | None) -> bool:
    """True if a sensor report is a real change to on or off.

    Unavailable, unknown and a missing entity are no news, and attribute-only
    updates (battery, link quality) repeat the state they had.
    """
    return state in (_ON, _OFF) and state != previous
