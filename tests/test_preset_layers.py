"""Scenario tests for PresetLayers: which preset the room runs (ADR 0001).

Each test drives the module only through its public events and reads only
its public state and the effects it returns.  Time passes through "timer
elapsed" events and the time passed with an event – no fake scheduler.

Loaded via importlib so that no Home Assistant bootstrap is needed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Module loading (HA-free)
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent.parent / "custom_components" / "tadox_proxy"


def _load(name: str):
    """Load a module as part of a stand-in ``tadox_proxy`` package."""
    full_name = f"tadox_proxy.{name}"
    spec = importlib.util.spec_from_file_location(full_name, _ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


_params = _load("parameters")
_ctrl = _load("climate_controllers")
_layers = _load("preset_layers")

PresetConfig = _params.PresetConfig
PresetLayers = _layers.PresetLayers
LayersConfig = _layers.LayersConfig
StartSnapshot = _layers.StartSnapshot

COMFORT = "comfort"
ECO = "eco"
MANUAL = "none"
FROST = "frost_protection"
AWAY_PRESET = "away"
BOOST_PRESET = "boost"

ON = "on"
OFF = "off"
UNAVAILABLE = "unavailable"

WINDOW_OPEN = _layers.TIMER_WINDOW_OPEN
WINDOW_CLOSE = _layers.TIMER_WINDOW_CLOSE
AWAY = _layers.TIMER_AWAY
HOME = _layers.TIMER_HOME
BOOST = _layers.TIMER_BOOST

T0 = 1_000_000.0


def _config(**changes) -> LayersConfig:
    """The configuration every scenario starts from."""
    values = {
        "presets": PresetConfig(
            eco_target_c=17.0,
            boost_target_c=25.0,
            boost_duration_min=30,
            away_target_c=16.0,
            frost_protection_target_c=7.0,
        ),
        "comfort_c": 20.0,
        "min_target_c": 5.0,
        "max_target_c": 30.0,
        "window_delay_s": 30,
        "window_close_delay_s": 120,
        "away_delay_s": 600,
        "home_delay_s": 30,
        "window_action": "frost_protection",
        "has_window_sensor": True,
        "has_presence_sensor": True,
    }
    values.update(changes)
    return LayersConfig(**values)


def _room(**changes) -> PresetLayers:
    return PresetLayers(_config(**changes))


def _open_window(room: PresetLayers, now: float) -> None:
    """The window opens and stays open for the open delay (30 s)."""
    room.window_reported(ON, now)
    room.timer_elapsed(WINDOW_OPEN, now + 30)


def _close_window(room: PresetLayers, now: float) -> None:
    """The window closes and stays closed for the close delay (120 s)."""
    room.window_reported(OFF, now)
    room.timer_elapsed(WINDOW_CLOSE, now + 120)


def _leave(room: PresetLayers, now: float) -> None:
    """Everybody leaves and stays away for the away delay (600 s)."""
    room.presence_reported(OFF, now)
    room.timer_elapsed(AWAY, now + 600)


def _come_home(room: PresetLayers, now: float) -> None:
    """Someone comes home and stays for the home delay (30 s)."""
    room.presence_reported(ON, now)
    room.timer_elapsed(HOME, now + 30)


# ---------------------------------------------------------------------------
# User choice and layers
# ---------------------------------------------------------------------------

class TestUserChoice:

    def test_a_new_room_runs_comfort_at_the_comfort_target(self):
        room = _room()

        assert room.active_preset == COMFORT
        assert room.room_target_c == 20.0

    def test_picking_a_preset_runs_it_right_away(self):
        room = _room()

        effects = room.pick_preset(ECO, T0)

        assert room.active_preset == ECO
        assert room.room_target_c == 17.0
        assert effects.cycle == "preset_change"

    def test_setting_a_temperature_switches_to_manual_with_that_temperature(self):
        room = _room()

        effects = room.set_temperature(21.5, T0)

        assert room.active_preset == MANUAL
        assert room.room_target_c == 21.5
        assert effects.cycle == "set_temperature"

    def test_temperature_beyond_the_limits_is_clamped(self):
        room = _room()

        room.set_temperature(35.0, T0)
        assert room.room_target_c == 30.0

        room.set_temperature(2.0, T0 + 10)
        assert room.room_target_c == 5.0

    def test_unknown_preset_changes_nothing(self):
        room = _room()
        room.pick_preset(ECO, T0)

        effects = room.pick_preset("party", T0 + 10)

        assert effects.changed is False
        assert room.active_preset == ECO


# ---------------------------------------------------------------------------
# Window layer
# ---------------------------------------------------------------------------

class TestWindow:

    def test_open_window_holds_the_room_in_frost_protection_after_the_open_delay(self):
        room = _room()

        effects = room.window_reported(ON, T0)
        assert effects.timers == {WINDOW_OPEN: 30}
        assert room.active_preset == COMFORT

        effects = room.timer_elapsed(WINDOW_OPEN, T0 + 30)

        assert room.active_preset == FROST
        assert room.room_target_c == 7.0
        assert room.window_holds
        assert effects.cycle == "window_open"

    def test_closed_window_brings_back_the_user_choice_after_the_close_delay(self):
        room = _room()
        room.pick_preset(ECO, T0)
        _open_window(room, T0)

        effects = room.window_reported(OFF, T0 + 100)
        assert effects.timers == {WINDOW_CLOSE: 120}
        assert room.active_preset == FROST
        assert room.close_delay_running

        effects = room.timer_elapsed(WINDOW_CLOSE, T0 + 220)

        assert room.active_preset == ECO
        assert not room.window_holds
        assert not room.close_delay_running
        assert effects.cycle == "window_closed"

    def test_window_shut_before_the_open_delay_is_up_changes_nothing(self):
        room = _room()
        room.window_reported(ON, T0)

        effects = room.window_reported(OFF, T0 + 10)

        assert effects.timers == {WINDOW_OPEN: None}
        assert effects.cycle is None
        assert room.active_preset == COMFORT

    def test_open_delay_acts_only_if_the_window_is_still_open(self):
        """A single glitchy reading must not flip the room into frost protection."""
        room = _room()
        room.window_reported(ON, T0)
        room.window_reported(UNAVAILABLE, T0 + 10)

        effects = room.timer_elapsed(WINDOW_OPEN, T0 + 30)

        assert room.active_preset == COMFORT
        assert not room.window_holds
        assert effects.cycle is None

    def test_window_reopened_during_the_close_delay_keeps_frost_without_a_new_open_delay(self):
        room = _room()
        _open_window(room, T0)
        room.window_reported(OFF, T0 + 100)

        effects = room.window_reported(ON, T0 + 150)

        assert effects.timers == {WINDOW_CLOSE: None}
        assert room.active_preset == FROST
        assert room.window_holds
        assert not room.close_delay_running

    def test_a_cancelled_close_delay_that_fires_anyway_changes_nothing(self):
        room = _room()
        _open_window(room, T0)
        room.window_reported(OFF, T0 + 100)
        room.window_reported(ON, T0 + 150)

        effects = room.timer_elapsed(WINDOW_CLOSE, T0 + 220)

        assert room.active_preset == FROST
        assert effects.cycle is None

    def test_repeated_open_reports_do_not_restart_the_open_delay(self):
        """Attribute updates (battery, link quality) and a brief unavailable."""
        room = _room()
        room.window_reported(ON, T0)

        assert room.window_reported(ON, T0 + 10).timers == {}
        assert room.window_reported(UNAVAILABLE, T0 + 15).timers == {}
        assert room.window_reported(ON, T0 + 20).timers == {}

        room.timer_elapsed(WINDOW_OPEN, T0 + 30)
        assert room.active_preset == FROST

    def test_repeated_close_reports_do_not_restart_the_close_delay(self):
        room = _room()
        _open_window(room, T0)
        room.window_reported(OFF, T0 + 100)

        assert room.window_reported(OFF, T0 + 110).timers == {}
        assert room.window_reported(UNAVAILABLE, T0 + 115).timers == {}
        assert room.window_reported(OFF, T0 + 120).timers == {}

        room.timer_elapsed(WINDOW_CLOSE, T0 + 220)
        assert room.active_preset == COMFORT

    def test_close_delay_lets_go_although_the_contact_dropped_out(self):
        """Unlike the open, away and home delays, the close delay does not
        check its sensor again: a dead contact must not keep the room in frost
        protection."""
        room = _room()
        _open_window(room, T0)
        room.window_reported(OFF, T0 + 100)
        room.window_reported(UNAVAILABLE, T0 + 110)

        room.timer_elapsed(WINDOW_CLOSE, T0 + 220)

        assert room.active_preset == COMFORT
        assert not room.window_holds

    def test_preset_picked_while_the_window_is_open_takes_effect_once_it_closes(self):
        room = _room()
        _open_window(room, T0)

        effects = room.pick_preset(ECO, T0 + 60)
        assert room.active_preset == FROST
        assert room.user_choice.preset == ECO
        assert effects.cycle is None

        _close_window(room, T0 + 100)
        assert room.active_preset == ECO

    def test_temperature_set_while_the_window_is_open_takes_effect_once_it_closes(self):
        room = _room()
        _open_window(room, T0)

        room.set_temperature(21.5, T0 + 60)
        assert room.active_preset == FROST

        _close_window(room, T0 + 100)
        assert room.active_preset == MANUAL
        assert room.room_target_c == 21.5

    def test_tapping_frost_protection_while_the_window_forces_it_changes_nothing(self):
        """Confirming what is shown must not leave the room cold later."""
        room = _room()
        room.pick_preset(ECO, T0)
        _open_window(room, T0)

        effects = room.pick_preset(FROST, T0 + 60)
        assert effects.changed is False
        assert room.user_choice.preset == ECO

        _close_window(room, T0 + 100)
        assert room.active_preset == ECO

    def test_preset_picked_during_the_close_delay_ends_it_and_applies_right_away(self):
        room = _room()
        _open_window(room, T0)
        room.window_reported(OFF, T0 + 100)

        effects = room.pick_preset(ECO, T0 + 130)

        assert room.active_preset == ECO
        assert not room.window_holds
        assert not room.close_delay_running
        assert effects.timers == {WINDOW_CLOSE: None}
        assert effects.cycle == "preset_change"

    def test_temperature_set_during_the_close_delay_ends_it_and_applies_right_away(self):
        room = _room()
        _open_window(room, T0)
        room.window_reported(OFF, T0 + 100)

        room.set_temperature(21.5, T0 + 130)

        assert room.active_preset == MANUAL
        assert room.room_target_c == 21.5
        assert not room.close_delay_running

    def test_tapping_frost_protection_during_the_close_delay_ends_it_and_applies(self):
        """A change by a person ends the close delay, so frost protection is
        a real choice here and not a layer being confirmed."""
        room = _room()
        _open_window(room, T0)
        room.window_reported(OFF, T0 + 100)

        effects = room.pick_preset(FROST, T0 + 130)

        assert room.active_preset == FROST
        assert room.user_choice.preset == FROST
        assert not room.window_holds
        assert not room.close_delay_running
        assert effects.timers == {WINDOW_CLOSE: None}
        # The room target does not move (frost protection either way), so the
        # module asks for no cycle: nothing is sent to the TRV.
        assert effects.cycle is None
        assert effects.changed is True

    def test_window_zero_delays_act_right_away_without_a_timer(self):
        room = _room(window_delay_s=0, window_close_delay_s=0)

        effects = room.window_reported(ON, T0)
        assert effects.timers == {}
        assert room.active_preset == FROST
        assert effects.cycle == "window_open"

        effects = room.window_reported(OFF, T0 + 60)
        assert effects.timers == {}
        assert room.active_preset == COMFORT
        assert effects.cycle == "window_closed"


# ---------------------------------------------------------------------------
# Nobody home layer
# ---------------------------------------------------------------------------

class TestNobodyHome:

    def test_nobody_home_holds_the_room_in_away_after_the_away_delay(self):
        room = _room()
        room.presence_reported(ON, T0)

        effects = room.presence_reported(OFF, T0 + 10)
        assert effects.timers == {AWAY: 600}
        assert room.active_preset == COMFORT

        effects = room.timer_elapsed(AWAY, T0 + 610)

        assert room.active_preset == AWAY_PRESET
        assert room.room_target_c == 16.0
        assert room.nobody_home_holds
        assert effects.cycle == "presence_away"

    def test_coming_home_brings_back_the_user_choice_after_the_home_delay(self):
        room = _room()
        room.pick_preset(ECO, T0)
        _leave(room, T0)

        effects = room.presence_reported(ON, T0 + 1000)
        assert effects.timers == {HOME: 30}
        assert room.active_preset == AWAY_PRESET

        effects = room.timer_elapsed(HOME, T0 + 1030)

        assert room.active_preset == ECO
        assert not room.nobody_home_holds
        assert effects.cycle == "presence_home"

    def test_nobody_home_during_the_home_delay_cancels_the_return(self):
        room = _room()
        _leave(room, T0)
        room.presence_reported(ON, T0 + 1000)

        effects = room.presence_reported(OFF, T0 + 1010)

        assert effects.timers == {HOME: None}
        assert room.active_preset == AWAY_PRESET
        assert room.nobody_home_holds

    def test_away_delay_acts_only_if_nobody_is_still_reported_home(self):
        """A single glitchy reading must not flip the room into Away."""
        room = _room()
        room.presence_reported(ON, T0)
        room.presence_reported(OFF, T0 + 10)
        room.presence_reported(UNAVAILABLE, T0 + 300)

        effects = room.timer_elapsed(AWAY, T0 + 610)

        assert room.active_preset == COMFORT
        assert effects.cycle is None

    def test_home_delay_acts_only_if_someone_is_still_reported_home(self):
        """A sensor that dropped out does not confirm anyone home: Away stays."""
        for state in (UNAVAILABLE, "unknown", None):
            room = _room()
            _leave(room, T0)
            room.presence_reported(ON, T0 + 1000)
            room.presence_reported(state, T0 + 1010)

            effects = room.timer_elapsed(HOME, T0 + 1030)

            assert room.active_preset == AWAY_PRESET, state
            assert room.nobody_home_holds, state
            assert effects.cycle is None, state

    def test_fresh_on_after_a_dropout_runs_a_new_home_delay(self):
        room = _room()
        room.pick_preset(ECO, T0)
        _leave(room, T0)
        room.presence_reported(ON, T0 + 1000)
        room.presence_reported(UNAVAILABLE, T0 + 1010)
        room.timer_elapsed(HOME, T0 + 1030)

        effects = room.presence_reported(ON, T0 + 5000)
        assert effects.timers == {HOME: 30}
        assert room.active_preset == AWAY_PRESET

        effects = room.timer_elapsed(HOME, T0 + 5030)
        assert room.active_preset == ECO
        assert effects.cycle == "presence_home"

    def test_guest_mode_ends_away_while_the_sensor_stays_unavailable(self):
        """The way out while a dead sensor keeps the room in Away."""
        room = _room()
        room.pick_preset(ECO, T0)
        _leave(room, T0)
        room.presence_reported(ON, T0 + 1000)
        room.presence_reported(UNAVAILABLE, T0 + 1010)
        room.timer_elapsed(HOME, T0 + 1030)

        room.set_guest_mode(True, T0 + 2000)

        assert room.active_preset == ECO

    def test_repeated_reports_do_not_restart_the_away_delay(self):
        """Attribute updates (battery, link quality) and a brief unavailable."""
        room = _room()
        room.presence_reported(ON, T0)
        room.presence_reported(OFF, T0 + 10)

        assert room.presence_reported(OFF, T0 + 200).timers == {}
        assert room.presence_reported(UNAVAILABLE, T0 + 300).timers == {}
        assert room.presence_reported(OFF, T0 + 400).timers == {}

        room.timer_elapsed(AWAY, T0 + 610)
        assert room.active_preset == AWAY_PRESET

    def test_repeated_reports_do_not_restart_the_home_delay(self):
        room = _room()
        _leave(room, T0)
        room.presence_reported(ON, T0 + 1000)

        assert room.presence_reported(ON, T0 + 1005).timers == {}
        assert room.presence_reported(UNAVAILABLE, T0 + 1010).timers == {}
        assert room.presence_reported(ON, T0 + 1015).timers == {}

        room.timer_elapsed(HOME, T0 + 1030)
        assert room.active_preset == COMFORT

    def test_presence_zero_delays_act_right_away_without_a_timer(self):
        room = _room(away_delay_s=0, home_delay_s=0)
        room.presence_reported(ON, T0)

        effects = room.presence_reported(OFF, T0 + 10)
        assert effects.timers == {}
        assert room.active_preset == AWAY_PRESET
        assert effects.cycle == "presence_away"

        effects = room.presence_reported(ON, T0 + 60)
        assert effects.timers == {}
        assert room.active_preset == COMFORT
        assert effects.cycle == "presence_home"


# ---------------------------------------------------------------------------
# Layers together
# ---------------------------------------------------------------------------

class TestLayerRank:

    def test_open_window_outranks_nobody_home(self):
        room = _room()
        _leave(room, T0)

        _open_window(room, T0 + 1000)

        assert room.active_preset == FROST
        assert room.nobody_home_holds

    def test_window_opened_and_closed_while_nobody_is_home_leaves_the_room_in_away(self):
        room = _room()
        room.pick_preset(ECO, T0)
        _leave(room, T0)
        _open_window(room, T0 + 1000)

        _close_window(room, T0 + 1100)

        assert room.active_preset == AWAY_PRESET

    def test_preset_picked_while_nobody_is_home_takes_effect_once_someone_comes_home(self):
        room = _room()
        _leave(room, T0)

        effects = room.pick_preset(ECO, T0 + 1000)
        assert room.active_preset == AWAY_PRESET
        assert effects.cycle is None

        _come_home(room, T0 + 2000)
        assert room.active_preset == ECO

    def test_temperature_set_while_nobody_is_home_takes_effect_once_someone_comes_home(self):
        room = _room()
        _leave(room, T0)

        room.set_temperature(21.5, T0 + 1000)

        _come_home(room, T0 + 2000)
        assert room.active_preset == MANUAL
        assert room.room_target_c == 21.5

    def test_tapping_away_while_nobody_home_forces_it_changes_nothing(self):
        room = _room()
        room.pick_preset(ECO, T0)
        _leave(room, T0)

        effects = room.pick_preset(AWAY_PRESET, T0 + 1000)
        assert effects.changed is False

        _come_home(room, T0 + 2000)
        assert room.active_preset == ECO

    def test_away_picked_by_hand_stays_after_airing(self):
        room = _room()
        room.presence_reported(ON, T0)
        room.pick_preset(AWAY_PRESET, T0)
        _open_window(room, T0 + 100)

        _close_window(room, T0 + 200)

        assert room.active_preset == AWAY_PRESET
        assert room.user_choice.preset == AWAY_PRESET

    def test_change_during_the_close_delay_while_nobody_is_home_keeps_away(self):
        room = _room()
        _leave(room, T0)
        _open_window(room, T0 + 1000)
        room.window_reported(OFF, T0 + 1100)

        effects = room.pick_preset(ECO, T0 + 1150)

        assert room.active_preset == AWAY_PRESET
        assert not room.window_holds
        assert effects.timers == {WINDOW_CLOSE: None}
        _come_home(room, T0 + 2000)
        assert room.active_preset == ECO


# ---------------------------------------------------------------------------
# Guest mode
# ---------------------------------------------------------------------------

class TestGuestMode:

    def test_turning_guest_mode_on_ends_away_right_away(self):
        room = _room()
        room.pick_preset(ECO, T0)
        _leave(room, T0)

        effects = room.set_guest_mode(True, T0 + 1000)

        assert room.guest_mode
        assert room.active_preset == ECO
        assert not room.nobody_home_holds
        assert effects.cycle == "guest_mode"

    def test_guest_mode_keeps_nobody_home_from_holding_the_room(self):
        room = _room()
        room.presence_reported(ON, T0)
        room.set_guest_mode(True, T0)

        effects = room.presence_reported(OFF, T0 + 10)

        assert effects.timers == {}
        assert room.active_preset == COMFORT

    def test_turning_guest_mode_on_cancels_a_running_away_delay(self):
        room = _room()
        room.presence_reported(ON, T0)
        room.presence_reported(OFF, T0 + 10)

        effects = room.set_guest_mode(True, T0 + 100)

        assert effects.timers == {AWAY: None}

    def test_turning_guest_mode_off_while_nobody_is_home_starts_the_away_delay(self):
        room = _room()
        room.presence_reported(ON, T0)
        room.set_guest_mode(True, T0)
        room.presence_reported(OFF, T0 + 10)

        effects = room.set_guest_mode(False, T0 + 100)
        assert effects.timers == {AWAY: 600}
        assert room.active_preset == COMFORT

        room.timer_elapsed(AWAY, T0 + 700)
        assert room.active_preset == AWAY_PRESET

    def test_turning_guest_mode_off_while_someone_is_home_changes_nothing_else(self):
        room = _room()
        room.presence_reported(ON, T0)
        room.set_guest_mode(True, T0)

        effects = room.set_guest_mode(False, T0 + 100)

        assert effects.timers == {}
        assert not room.guest_mode

    def test_switching_guest_mode_to_what_it_already_is_changes_nothing(self):
        room = _room()
        _leave(room, T0)
        room.window_reported(OFF, T0)

        assert room.set_guest_mode(False, T0 + 1000).changed is False
        assert room.nobody_home_holds


# ---------------------------------------------------------------------------
# Boost layer
# ---------------------------------------------------------------------------

class TestBoost:

    def test_boost_runs_its_time_and_brings_back_the_user_choice(self):
        room = _room()
        room.pick_preset(ECO, T0)

        effects = room.pick_preset(BOOST_PRESET, T0 + 10)
        assert room.active_preset == BOOST_PRESET
        assert room.room_target_c == 25.0
        assert room.user_choice.preset == ECO
        assert effects.timers == {BOOST: 1800}
        assert effects.cycle == "preset_change"

        effects = room.timer_elapsed(BOOST, T0 + 1810)

        assert room.active_preset == ECO
        assert effects.cycle == "boost_expired"

    def test_picking_another_preset_ends_the_boost(self):
        room = _room()
        room.pick_preset(BOOST_PRESET, T0)

        effects = room.pick_preset(ECO, T0 + 60)

        assert room.active_preset == ECO
        assert effects.timers == {BOOST: None}

    def test_setting_a_temperature_ends_the_boost(self):
        room = _room()
        room.pick_preset(BOOST_PRESET, T0)

        effects = room.set_temperature(21.5, T0 + 60)

        assert room.active_preset == MANUAL
        assert effects.timers == {BOOST: None}

    def test_picking_boost_again_restarts_its_full_time(self):
        room = _room()
        room.pick_preset(BOOST_PRESET, T0)

        effects = room.pick_preset(BOOST_PRESET, T0 + 600)

        assert effects.timers == {BOOST: 1800}
        assert room.boost_remaining_min(T0 + 600) == 30

    def test_boost_interrupted_by_an_open_window_continues_after_it_closes(self):
        """Airing in the middle of a boost does not eat it up."""
        room = _room()
        room.pick_preset(BOOST_PRESET, T0)
        room.window_reported(ON, T0 + 600)

        effects = room.timer_elapsed(WINDOW_OPEN, T0 + 630)
        assert effects.timers == {BOOST: None}
        assert room.boost_remaining_min(T0 + 2000) == 20  # 1170 s left, standing still

        room.window_reported(OFF, T0 + 3000)
        effects = room.timer_elapsed(WINDOW_CLOSE, T0 + 3120)

        assert room.active_preset == BOOST_PRESET
        assert effects.timers == {BOOST: 1170}

    def test_boost_interrupted_by_nobody_home_continues_once_someone_comes_home(self):
        room = _room()
        room.presence_reported(ON, T0)
        room.pick_preset(BOOST_PRESET, T0)
        room.presence_reported(OFF, T0 + 100)

        effects = room.timer_elapsed(AWAY, T0 + 700)
        assert effects.timers == {BOOST: None}

        room.presence_reported(ON, T0 + 5000)
        effects = room.timer_elapsed(HOME, T0 + 5030)

        assert room.active_preset == BOOST_PRESET
        assert effects.timers == {BOOST: 1100}

    def test_boost_picked_while_the_window_is_open_runs_its_full_time_after_it_closes(self):
        room = _room()
        room.pick_preset(ECO, T0)
        _open_window(room, T0)

        effects = room.pick_preset(BOOST_PRESET, T0 + 100)
        assert room.active_preset == FROST
        assert effects.timers == {}
        assert room.boost_remaining_min(T0 + 100) == 30

        room.window_reported(OFF, T0 + 500)
        effects = room.timer_elapsed(WINDOW_CLOSE, T0 + 620)
        assert room.active_preset == BOOST_PRESET
        assert effects.timers == {BOOST: 1800}

        room.timer_elapsed(BOOST, T0 + 2420)
        assert room.active_preset == ECO

    def test_boost_picked_while_nobody_is_home_runs_its_full_time_once_someone_comes_home(self):
        room = _room()
        _leave(room, T0)

        room.pick_preset(BOOST_PRESET, T0 + 1000)
        assert room.active_preset == AWAY_PRESET

        room.presence_reported(ON, T0 + 5000)
        effects = room.timer_elapsed(HOME, T0 + 5030)

        assert room.active_preset == BOOST_PRESET
        assert effects.timers == {BOOST: 1800}

    def test_boost_whose_time_is_up_when_it_pauses_ends(self):
        """The window takes over just as the boost timer is due."""
        room = _room()
        room.pick_preset(ECO, T0)
        room.pick_preset(BOOST_PRESET, T0)
        room.window_reported(ON, T0 + 1790)

        effects = room.timer_elapsed(WINDOW_OPEN, T0 + 1820)
        assert effects.timers == {BOOST: None}
        assert room.boost_remaining_min(T0 + 1820) == 0

        _close_window(room, T0 + 2000)
        assert room.active_preset == ECO

    def test_boost_remaining_shows_the_minutes_left_rounded_up(self):
        room = _room()
        assert room.boost_remaining_min(T0) == 0

        room.pick_preset(BOOST_PRESET, T0)

        assert room.boost_remaining_min(T0) == 30
        assert room.boost_remaining_min(T0 + 125) == 28  # 1675 s

    def test_boost_picked_during_the_close_delay_ends_it_and_runs_right_away(self):
        room = _room()
        room.pick_preset(ECO, T0)
        _open_window(room, T0)
        room.window_reported(OFF, T0 + 100)

        effects = room.pick_preset(BOOST_PRESET, T0 + 130)

        assert room.active_preset == BOOST_PRESET
        assert effects.timers == {WINDOW_CLOSE: None, BOOST: 1800}
        room.timer_elapsed(BOOST, T0 + 1930)
        assert room.active_preset == ECO

    def test_new_choice_while_a_boost_waits_behind_the_window_ends_the_boost(self):
        room = _room()
        _open_window(room, T0)
        room.pick_preset(BOOST_PRESET, T0 + 100)

        room.pick_preset(ECO, T0 + 200)
        _close_window(room, T0 + 300)

        assert room.active_preset == ECO
        assert room.boost_remaining_min(T0 + 500) == 0

    def test_tapping_away_while_nobody_home_forces_it_still_ends_the_boost(self):
        """Confirming the layer is no user choice, but it is a change by a
        person: a paused boost must not come back to life on the return."""
        room = _room()
        room.pick_preset(ECO, T0)
        room.pick_preset(BOOST_PRESET, T0 + 10)
        _leave(room, T0 + 100)

        room.pick_preset(AWAY_PRESET, T0 + 1000)

        _come_home(room, T0 + 2000)
        assert room.active_preset == ECO
        assert room.boost_remaining_min(T0 + 2100) == 0

    def test_tapping_frost_protection_behind_an_open_window_ends_the_boost(self):
        """Same for the window layer: the boost waiting behind it is over."""
        room = _room()
        room.pick_preset(ECO, T0)
        room.pick_preset(BOOST_PRESET, T0 + 10)
        _open_window(room, T0 + 100)

        room.pick_preset(FROST, T0 + 200)

        _close_window(room, T0 + 300)
        assert room.active_preset == ECO
        assert room.boost_remaining_min(T0 + 500) == 0

    def test_guest_mode_lets_a_boost_paused_by_nobody_home_continue(self):
        room = _room()
        room.presence_reported(ON, T0)
        room.pick_preset(BOOST_PRESET, T0)
        room.presence_reported(OFF, T0 + 100)
        room.timer_elapsed(AWAY, T0 + 700)

        effects = room.set_guest_mode(True, T0 + 1000)

        assert room.active_preset == BOOST_PRESET
        assert effects.timers == {BOOST: 1100}


# ---------------------------------------------------------------------------
# Heating off and on
# ---------------------------------------------------------------------------

class TestHeatingOff:

    def test_boost_pauses_while_the_heating_is_off_and_continues_when_back_on(self):
        room = _room()
        room.pick_preset(BOOST_PRESET, T0)

        effects = room.heating_off(T0 + 600)
        assert effects.timers == {BOOST: None}
        assert room.active_preset == BOOST_PRESET
        assert room.boost_remaining_min(T0 + 5000) == 20

        effects = room.heating_on(T0 + 6000)
        assert effects.timers == {BOOST: 1200}

    def test_boost_picked_while_the_heating_is_off_starts_with_its_full_time_once_back_on(self):
        room = _room()
        room.heating_off(T0)

        effects = room.pick_preset(BOOST_PRESET, T0 + 100)
        assert effects.timers == {}

        effects = room.heating_on(T0 + 5000)
        assert effects.timers == {BOOST: 1800}

    def test_window_detection_keeps_running_while_the_heating_is_off(self):
        """Turning back on with the window still open goes straight to frost protection."""
        room = _room()
        room.heating_off(T0)

        _open_window(room, T0 + 100)
        assert room.active_preset == FROST

        room.heating_on(T0 + 500)
        assert room.active_preset == FROST
        assert room.window_holds

    def test_presence_detection_keeps_running_while_the_heating_is_off(self):
        room = _room()
        room.heating_off(T0)

        _leave(room, T0 + 100)

        assert room.active_preset == AWAY_PRESET

    def test_window_closing_while_the_heating_is_off_ends_its_layer_after_the_close_delay(self):
        room = _room()
        room.pick_preset(ECO, T0)
        _open_window(room, T0)
        room.heating_off(T0 + 100)

        _close_window(room, T0 + 200)

        assert room.active_preset == ECO
        assert not room.window_holds

    def test_turning_the_heating_off_leaves_an_open_window_in_charge(self):
        room = _room()
        _open_window(room, T0)

        effects = room.heating_off(T0 + 100)

        assert room.window_holds
        assert room.active_preset == FROST
        assert effects.timers == {}


# ---------------------------------------------------------------------------
# Changes on the valve (Follow Tado input)
# ---------------------------------------------------------------------------

class TestValveChange:

    def test_turning_the_dial_counts_like_setting_the_temperature(self):
        room = _room()

        effects = room.valve_changed(22.0, T0)

        assert room.active_preset == MANUAL
        assert room.room_target_c == 22.0
        assert effects.cycle == "follow_tado"

    def test_dial_change_beyond_the_limits_is_clamped(self):
        room = _room()

        room.valve_changed(31.0, T0)

        assert room.room_target_c == 30.0

    def test_dial_change_while_the_window_is_open_is_kept_for_when_it_closes(self):
        room = _room()
        _open_window(room, T0)

        room.valve_changed(22.0, T0 + 100)
        assert room.active_preset == FROST

        _close_window(room, T0 + 200)
        assert room.active_preset == MANUAL
        assert room.room_target_c == 22.0

    def test_dial_change_while_nobody_is_home_is_kept_for_the_return(self):
        room = _room()
        _leave(room, T0)

        room.valve_changed(22.0, T0 + 1000)
        assert room.active_preset == AWAY_PRESET

        _come_home(room, T0 + 2000)
        assert room.room_target_c == 22.0

    def test_dial_change_ends_a_running_boost(self):
        room = _room()
        room.pick_preset(BOOST_PRESET, T0)

        effects = room.valve_changed(22.0, T0 + 60)

        assert room.active_preset == MANUAL
        assert effects.timers == {BOOST: None}

    def test_dial_change_ends_a_running_close_delay(self):
        room = _room()
        _open_window(room, T0)
        room.window_reported(OFF, T0 + 100)

        room.valve_changed(22.0, T0 + 130)

        assert room.active_preset == MANUAL
        assert not room.close_delay_running


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

class TestOptions:

    def test_new_comfort_target_applies_right_away_while_comfort_is_active(self):
        room = _room()

        effects = room.options_changed(_config(comfort_c=22.0), T0)

        assert room.room_target_c == 22.0
        assert effects.cycle == "options_changed"

    def test_new_comfort_target_is_what_comfort_brings_back_later(self):
        room = _room()
        _open_window(room, T0)

        room.options_changed(_config(comfort_c=22.0), T0 + 100)
        _close_window(room, T0 + 200)

        assert room.active_preset == COMFORT
        assert room.room_target_c == 22.0

    def test_new_eco_target_applies_while_eco_is_active(self):
        room = _room()
        room.pick_preset(ECO, T0)

        room.options_changed(
            _config(presets=PresetConfig(eco_target_c=18.5)), T0 + 100
        )

        assert room.room_target_c == 18.5


# ---------------------------------------------------------------------------
# Window action "switch the valve off"
# ---------------------------------------------------------------------------

def _off_action_room() -> PresetLayers:
    return _room(window_action="off")


class TestValveOffAction:

    def test_valve_is_switched_off_while_the_window_holds_and_on_once_it_lets_go(self):
        room = _off_action_room()
        assert not room.valve_off_due

        effects = room.window_reported(ON, T0)
        effects = room.timer_elapsed(WINDOW_OPEN, T0 + 30)
        assert room.valve_off_due
        assert effects.cycle == "window_open"

        room.valve_switched_off(T0 + 31)
        assert not room.valve_off_due
        assert room.valve_held_off
        assert room.valve_off_by_window

        effects = room.window_reported(OFF, T0 + 100)
        assert room.valve_held_off  # still off during the close delay
        effects = room.timer_elapsed(WINDOW_CLOSE, T0 + 220)
        assert room.valve_on_due
        assert not room.valve_held_off
        assert effects.cycle == "window_closed"

        room.heat_command_sent(T0 + 221)
        assert not room.valve_on_due
        assert not room.valve_off_by_window

    def test_frost_protection_action_never_switches_the_valve_off(self):
        room = _room()

        _open_window(room, T0)

        assert not room.valve_off_due

    def test_failed_switch_off_falls_back_to_frost_protection_until_the_window_opens_again(self):
        """No off command every cycle."""
        room = _off_action_room()
        _open_window(room, T0)
        room.valve_switched_off(T0 + 31)

        room.valve_switch_off_failed(T0 + 32)

        assert room.active_preset == FROST
        assert not room.valve_off_due
        assert not room.valve_held_off
        assert not room.valve_off_by_window

        _close_window(room, T0 + 100)
        assert not room.valve_on_due
        _open_window(room, T0 + 300)
        assert room.valve_off_due

    def test_valve_is_never_switched_on_while_the_heating_is_off(self):
        room = _off_action_room()
        _open_window(room, T0)
        room.valve_switched_off(T0 + 31)
        room.heating_off(T0 + 100)

        _close_window(room, T0 + 200)
        assert not room.valve_on_due
        assert room.valve_off_by_window

        room.heating_on(T0 + 500)
        assert room.valve_on_due

    def test_valve_stays_off_when_the_heating_is_turned_on_while_the_window_holds(self):
        """No heat command followed by an off command."""
        room = _off_action_room()
        room.heating_off(T0)
        _open_window(room, T0 + 100)
        assert not room.valve_off_due

        room.heating_on(T0 + 500)

        assert room.valve_held_off
        assert not room.valve_off_due
        assert room.valve_off_by_window

    def test_turning_on_heating_that_is_already_on_does_not_pretend_the_valve_is_off(self):
        room = _off_action_room()
        _open_window(room, T0)

        room.heating_on(T0 + 100)

        assert room.valve_off_due
        assert not room.valve_off_by_window

    def test_window_closing_while_the_off_command_is_on_its_way_switches_the_valve_on(self):
        room = _off_action_room()
        _open_window(room, T0)
        room.valve_switched_off(T0 + 31)

        room.window_reported(OFF, T0 + 31)
        room.pick_preset(ECO, T0 + 31)  # ends the close delay

        assert room.valve_on_due

    def test_valve_feedback_from_the_regulation_cycle_asks_for_no_further_cycle(self):
        room = _off_action_room()
        _open_window(room, T0)

        assert room.valve_switched_off(T0 + 31).cycle is None
        assert room.valve_switch_off_failed(T0 + 32).cycle is None
        assert room.heat_command_sent(T0 + 33).cycle is None

    def test_switching_to_the_frost_protection_action_switches_the_valve_on(self):
        room = _off_action_room()
        _open_window(room, T0)
        room.valve_switched_off(T0 + 31)

        effects = room.options_changed(_config(window_action="frost_protection"), T0 + 100)

        assert room.valve_on_due
        assert effects.cycle == "options_changed"


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------

class TestAttributes:

    def test_attributes_always_show_the_user_choice(self):
        room = _room()
        room.pick_preset(ECO, T0)
        _open_window(room, T0)

        assert room.attributes()["user_preset"] == ECO

    def test_manual_temperature_is_shown_whenever_the_user_choice_is_manual(self):
        room = _room()
        assert "manual_target_c" not in room.attributes()

        room.set_temperature(21.5, T0)
        _leave(room, T0)

        attributes = room.attributes()
        assert attributes["user_preset"] == MANUAL
        assert attributes["manual_target_c"] == 21.5

    def test_layer_flags_keep_their_meaning(self):
        room = _room()
        room.set_guest_mode(True, T0)
        _open_window(room, T0)
        room.window_reported(OFF, T0 + 100)

        attributes = room.attributes()

        assert attributes["window_open_active"] is True
        assert attributes["window_close_delay_active"] is True
        assert attributes["presence_away_active"] is False
        assert attributes["guest_mode"] is True

    def test_presence_away_active_while_nobody_home_holds(self):
        room = _room()
        _leave(room, T0)

        assert room.attributes()["presence_away_active"] is True

    def test_window_trv_off_is_shown_while_the_window_has_the_valve_off(self):
        """Dashboards show "OFF · Window Open" and "Closing" from it."""
        room = _off_action_room()
        _open_window(room, T0)
        assert "window_trv_off" not in room.attributes()

        room.valve_switched_off(T0 + 31)
        room.window_reported(OFF, T0 + 100)
        assert room.attributes()["window_trv_off"] is True

        room.timer_elapsed(WINDOW_CLOSE, T0 + 220)
        room.heat_command_sent(T0 + 221)
        assert "window_trv_off" not in room.attributes()


# ---------------------------------------------------------------------------
# Restart and reload
# ---------------------------------------------------------------------------

def _restarted(snapshot, **changes) -> tuple[PresetLayers, object]:
    """A fresh module started from *snapshot*; returns it and the effects."""
    room = _room(**changes)
    effects = room.start(snapshot, T0)
    return room, effects


class TestRestart:

    def test_user_choice_with_its_manual_temperature_survives_a_restart(self):
        room, _ = _restarted(
            StartSnapshot(user_preset=MANUAL, manual_c=21.5, window_state=OFF, presence_state=ON)
        )

        assert room.active_preset == MANUAL
        assert room.room_target_c == 21.5

    def test_restart_while_nobody_is_home_brings_back_the_user_choice_on_return(self):
        room, effects = _restarted(
            StartSnapshot(user_preset=ECO, nobody_home_held=True, presence_state=OFF)
        )
        assert room.active_preset == AWAY_PRESET
        assert effects.timers == {}

        _come_home(room, T0 + 100)
        assert room.active_preset == ECO

    def test_restart_during_a_boost_brings_back_the_user_choice(self):
        room, _ = _restarted(
            StartSnapshot(preset_mode=BOOST_PRESET, user_preset=ECO, presence_state=ON)
        )

        assert room.active_preset == ECO
        assert room.boost_remaining_min(T0) == 0

    def test_window_open_before_and_still_open_holds_without_a_new_open_delay(self):
        room, effects = _restarted(
            StartSnapshot(user_preset=ECO, window_held=True, window_state=ON)
        )

        assert room.active_preset == FROST
        assert effects.timers == {}
        _close_window(room, T0 + 100)
        assert room.active_preset == ECO

    def test_window_sensor_not_reported_yet_counts_as_still_open_if_it_held_before(self):
        for state in (None, UNAVAILABLE, "unknown"):
            room, _ = _restarted(
                StartSnapshot(user_preset=ECO, window_held=True, window_state=state)
            )
            assert room.active_preset == FROST, state

    def test_late_window_sensor_reporting_closed_ends_the_layer_after_the_close_delay(self):
        room, _ = _restarted(
            StartSnapshot(user_preset=ECO, window_held=True, window_state=UNAVAILABLE)
        )

        effects = room.window_reported(OFF, T0 + 100)

        assert effects.timers == {WINDOW_CLOSE: 120}

    def test_window_closed_while_home_assistant_was_down_brings_back_the_user_choice(self):
        room, _ = _restarted(
            StartSnapshot(user_preset=ECO, window_held=True, window_state=OFF)
        )

        assert room.active_preset == ECO
        assert not room.window_holds

    def test_window_opened_while_home_assistant_was_down_starts_the_open_delay(self):
        room, effects = _restarted(StartSnapshot(user_preset=ECO, window_state=ON))

        assert room.active_preset == ECO
        assert effects.timers == {WINDOW_OPEN: 30}

    def test_away_continues_without_a_new_away_delay_if_it_held_before(self):
        for state in (OFF, None, UNAVAILABLE):
            room, effects = _restarted(
                StartSnapshot(user_preset=ECO, nobody_home_held=True, presence_state=state)
            )
            assert room.active_preset == AWAY_PRESET, state
            assert effects.timers == {}, state

    def test_sensor_reporting_on_after_a_restart_runs_the_home_delay(self):
        room, _ = _restarted(
            StartSnapshot(user_preset=ECO, nobody_home_held=True, presence_state=UNAVAILABLE)
        )

        effects = room.presence_reported(ON, T0 + 100)

        assert effects.timers == {HOME: 30}
        room.timer_elapsed(HOME, T0 + 130)
        assert room.active_preset == ECO

    def test_someone_home_after_the_restart_ends_away_at_once(self):
        room, _ = _restarted(
            StartSnapshot(user_preset=ECO, nobody_home_held=True, presence_state=ON)
        )

        assert room.active_preset == ECO

    def test_nobody_home_after_the_restart_starts_the_away_delay_if_away_did_not_hold(self):
        room, effects = _restarted(StartSnapshot(user_preset=ECO, presence_state=OFF))

        assert room.active_preset == ECO
        assert effects.timers == {AWAY: 600}

    def test_guest_mode_survives_a_restart(self):
        room, effects = _restarted(
            StartSnapshot(user_preset=ECO, guest_mode=True, nobody_home_held=True, presence_state=OFF)
        )

        assert room.guest_mode
        assert room.active_preset == ECO
        assert effects.timers == {}

    def test_heating_that_was_off_stays_off(self):
        room, _ = _restarted(StartSnapshot(user_preset=ECO, heating_on=False))

        room.pick_preset(BOOST_PRESET, T0 + 10)

        assert room.boost_remaining_min(T0 + 1000) == 30  # paused

    def test_layer_whose_sensor_was_removed_does_not_hold_after_the_restart(self):
        """Nothing would end it."""
        room, _ = _restarted(
            StartSnapshot(user_preset=ECO, window_held=True, nobody_home_held=True),
            has_window_sensor=False,
            has_presence_sensor=False,
        )

        assert room.active_preset == ECO

    def test_valve_the_window_had_switched_off_is_switched_on_if_the_window_closed_meanwhile(self):
        room, _ = _restarted(
            StartSnapshot(user_preset=ECO, window_held=True, valve_off_by_window=True, window_state=OFF),
            window_action="off",
        )

        assert room.valve_on_due
        assert room.attributes()["window_trv_off"] is True

    def test_valve_under_a_still_open_window_is_switched_off_once_more(self):
        """Its state is unknown after the restart."""
        room, _ = _restarted(
            StartSnapshot(user_preset=ECO, window_held=True, valve_off_by_window=True, window_state=ON),
            window_action="off",
        )

        assert room.valve_off_due
        assert not room.valve_on_due


class TestUpgradeFromV14:
    """States written by v1.4.x have no user choice attribute yet."""

    def test_preset_the_open_window_had_kept_becomes_the_user_choice(self):
        room, _ = _restarted(
            StartSnapshot(
                preset_mode=FROST, window_kept_preset=ECO, window_kept_c=20.0, window_state=ON
            )
        )
        assert room.active_preset == FROST
        assert room.user_choice.preset == ECO

        _close_window(room, T0 + 100)
        assert room.active_preset == ECO

    def test_manual_temperature_the_window_had_kept_comes_back(self):
        room, _ = _restarted(
            StartSnapshot(
                preset_mode=FROST, window_kept_preset=MANUAL, window_kept_c=21.5, window_state=OFF
            )
        )

        assert room.active_preset == MANUAL
        assert room.room_target_c == 21.5

    def test_away_the_window_had_kept_from_nobody_home_means_nobody_home_held_the_room(self):
        room, _ = _restarted(
            StartSnapshot(
                preset_mode=FROST,
                window_kept_preset=AWAY_PRESET,
                window_kept_c=20.0,
                nobody_home_held=True,
                window_state=ON,
                presence_state=OFF,
            )
        )
        assert room.active_preset == FROST
        assert room.nobody_home_holds

        _close_window(room, T0 + 100)
        assert room.active_preset == AWAY_PRESET
        _come_home(room, T0 + 1000)
        assert room.active_preset == COMFORT

    def test_away_from_nobody_home_comes_back_with_comfort_as_the_user_choice(self):
        """v1.4.x did not write what nobody home would bring back."""
        room, effects = _restarted(
            StartSnapshot(preset_mode=AWAY_PRESET, nobody_home_held=True, presence_state=UNAVAILABLE)
        )
        assert room.active_preset == AWAY_PRESET
        assert effects.timers == {}

        _come_home(room, T0 + 100)
        assert room.active_preset == COMFORT

    def test_away_picked_by_hand_stays_the_user_choice(self):
        """presence_away_active tells it apart from Away while nobody is home."""
        room, _ = _restarted(StartSnapshot(preset_mode=AWAY_PRESET, presence_state=ON))

        assert room.active_preset == AWAY_PRESET
        assert room.user_choice.preset == AWAY_PRESET
        assert not room.nobody_home_holds

    def test_away_picked_by_hand_and_kept_by_the_window_comes_back_after_airing(self):
        room, _ = _restarted(
            StartSnapshot(
                preset_mode=FROST,
                window_kept_preset=AWAY_PRESET,
                window_kept_c=16.0,
                window_state=ON,
                presence_state=ON,
            )
        )

        _close_window(room, T0 + 100)

        assert room.active_preset == AWAY_PRESET
        assert room.user_choice.preset == AWAY_PRESET

    def test_away_without_a_presence_sensor_stays_the_user_choice(self):
        room, _ = _restarted(StartSnapshot(preset_mode=AWAY_PRESET), has_presence_sensor=False)

        assert room.active_preset == AWAY_PRESET
        assert room.user_choice.preset == AWAY_PRESET
        assert not room.nobody_home_holds

    def test_boost_and_frost_protection_without_a_kept_preset_come_back_as_comfort(self):
        for preset in (BOOST_PRESET, FROST):
            room, _ = _restarted(StartSnapshot(preset_mode=preset, window_state=OFF))
            assert room.active_preset == COMFORT, preset

    def test_named_and_manual_presets_stay(self):
        room, _ = _restarted(StartSnapshot(preset_mode=ECO))
        assert room.active_preset == ECO

        room, _ = _restarted(StartSnapshot(preset_mode=MANUAL, manual_c=21.5))
        assert room.active_preset == MANUAL
        assert room.room_target_c == 21.5

    def test_manual_without_its_own_temperature_uses_the_shown_target_while_heating(self):
        """States written before manual_target_c existed."""
        room, _ = _restarted(StartSnapshot(preset_mode=MANUAL, target_c=21.5))
        assert room.room_target_c == 21.5

        # While off the shown target is the 5 °C of the off state, not a choice.
        room, _ = _restarted(StartSnapshot(preset_mode=MANUAL, target_c=5.0, heating_on=False))
        assert room.active_preset == COMFORT

    def test_no_state_at_all_starts_in_comfort(self):
        room, _ = _restarted(StartSnapshot())

        assert room.active_preset == COMFORT


# ---------------------------------------------------------------------------
# Reading the state written before a restart
# ---------------------------------------------------------------------------

def _written_state(room: PresetLayers) -> dict:
    """The attributes Home Assistant writes for the climate entity."""
    return {
        **room.attributes(),
        "preset_mode": room.active_preset,
        "temperature": room.room_target_c,
    }


class TestStateRoundTrip:

    def test_what_the_room_writes_is_what_it_starts_from(self):
        before = _off_action_room()
        before.set_temperature(21.5, T0)
        before.set_guest_mode(True, T0)
        _open_window(before, T0)
        before.valve_switched_off(T0 + 31)

        after = _room(window_action="off")
        after.start(
            StartSnapshot.from_state(
                "heat", _written_state(before), window_state=ON, presence_state=OFF
            ),
            T0 + 100,
        )

        assert after.user_choice == before.user_choice
        assert after.window_holds
        assert after.guest_mode
        assert after.active_preset == FROST

    def test_nobody_home_and_heating_off_are_read_back(self):
        before = _room()
        before.pick_preset(ECO, T0)
        _leave(before, T0)
        before.heating_off(T0 + 1000)

        after = _room()
        after.start(
            StartSnapshot.from_state(
                "off", _written_state(before), window_state=OFF, presence_state=OFF
            ),
            T0 + 2000,
        )
        after.pick_preset(BOOST_PRESET, T0 + 2000)

        assert after.active_preset == AWAY_PRESET
        _come_home(after, T0 + 3000)
        assert after.boost_remaining_min(T0 + 5000) == 30  # paused: heating is off

    def test_a_state_written_by_v14_is_upgraded(self):
        snapshot = StartSnapshot.from_state(
            "heat",
            {
                "preset_mode": FROST,
                "temperature": 7.0,
                "window_open_active": True,
                "window_restore_preset": MANUAL,
                "window_restore_target_c": 21.5,
                "presence_away_active": False,
                "guest_mode": False,
            },
            window_state=OFF,
            presence_state=ON,
        )

        room, _ = _restarted(snapshot)

        assert room.active_preset == MANUAL
        assert room.room_target_c == 21.5

    def test_unreadable_numbers_are_ignored(self):
        snapshot = StartSnapshot.from_state(
            "heat",
            {"preset_mode": MANUAL, "user_preset": MANUAL, "manual_target_c": "nan"},
            window_state=None,
            presence_state=None,
        )

        room, _ = _restarted(snapshot)

        assert room.active_preset == COMFORT

    def test_no_state_written_before_starts_in_comfort(self):
        snapshot = StartSnapshot.from_state(None, {}, window_state=ON, presence_state=ON)

        room, effects = _restarted(snapshot)

        assert room.active_preset == COMFORT
        assert effects.timers == {WINDOW_OPEN: 30}
