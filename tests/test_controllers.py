"""Tests for climate state-machine controllers.

Imports climate_controllers directly via importlib so that no Home Assistant
bootstrap is needed.  HA's ``async_call_later`` is never invoked because every
test passes its own ``call_later`` stub via the keyword argument.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Module loading (HA-free)
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent.parent / "custom_components" / "tadox_proxy"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ctrl_mod = _load("climate_controllers")
_params_mod = _load("parameters")

WindowAutomationController = _ctrl_mod.WindowAutomationController
PresenceAutomationController = _ctrl_mod.PresenceAutomationController
BoostController = _ctrl_mod.BoostController
FollowPhysicalController = _ctrl_mod.FollowPhysicalController
SavedState = _ctrl_mod.SavedState
is_binary_sensor_transition = _ctrl_mod.is_binary_sensor_transition
decide_send = _ctrl_mod.decide_send
preset_target_c = _ctrl_mod.preset_target_c
preset_after_restart = _ctrl_mod.preset_after_restart
route_change = _ctrl_mod.route_change
preset_after_window = _ctrl_mod.preset_after_window
presence_counts_as_home = _ctrl_mod.presence_counts_as_home
CHANGE_APPLY = _ctrl_mod.CHANGE_APPLY
CHANGE_FOR_PRESENCE = _ctrl_mod.CHANGE_FOR_PRESENCE
CHANGE_FOR_WINDOW = _ctrl_mod.CHANGE_FOR_WINDOW
PresetConfig = _params_mod.PresetConfig


# ---------------------------------------------------------------------------
# Test stub for async_call_later
# ---------------------------------------------------------------------------

class FakeCallLater:
    """Records all scheduled callbacks; use .trigger(i) to fire one."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, hass, delay, callback):
        entry = {"delay": delay, "callback": callback, "cancelled": False}
        self.calls.append(entry)

        def cancel():
            entry["cancelled"] = True

        return cancel

    def trigger(self, index: int = 0, now=None) -> None:
        """Fire the callback at *index*."""
        self.calls[index]["callback"](now)

    @property
    def scheduled_count(self) -> int:
        return len(self.calls)

    @property
    def active_count(self) -> int:
        return sum(1 for c in self.calls if not c["cancelled"])


# ---------------------------------------------------------------------------
# WindowAutomationController
# ---------------------------------------------------------------------------

class TestWindowController:

    def _ctrl(self) -> WindowAutomationController:
        return WindowAutomationController()

    # --- initial state ---

    def test_initial_not_active(self):
        assert not self._ctrl().is_active

    def test_initial_no_close_delay(self):
        assert not self._ctrl().close_delay_active

    # --- open handling ---

    def test_open_schedules_timer(self):
        c = self._ctrl()
        fake = FakeCallLater()
        actions = []
        c.handle_window_opened(None, 30, lambda now: actions.append(now), call_later=fake)
        assert fake.scheduled_count == 1
        assert fake.calls[0]["delay"] == 30
        assert not c.is_active  # not yet active – open delay still pending

    def test_second_open_keeps_pending_timer(self):
        """on → unavailable → on during the open delay must not restart it."""
        c = self._ctrl()
        fake = FakeCallLater()
        c.handle_window_opened(None, 30, lambda now: None, call_later=fake)
        c.handle_window_opened(None, 30, lambda now: None, call_later=fake)
        assert fake.scheduled_count == 1
        assert not fake.calls[0]["cancelled"]

    # --- activate / restore ---

    def test_activate_sets_active_and_saves_state(self):
        c = self._ctrl()
        c.activate("comfort", 20.5)
        assert c.is_active
        saved = c.get_saved()
        assert saved.preset == "comfort"
        assert saved.temp == 20.5

    def test_restore_returns_saved_and_clears_state(self):
        c = self._ctrl()
        c.activate("eco", 19.0)
        saved = c.restore()
        assert saved.preset == "eco"
        assert saved.temp == 19.0
        assert not c.is_active
        # Saved state is cleared after restore
        assert c.get_saved().preset is None

    def test_restore_without_activate_returns_empty(self):
        c = self._ctrl()
        saved = c.restore()
        assert saved.preset is None
        assert saved.temp is None

    # --- window action "off" ---

    def test_trv_off_due_while_active_with_off_action(self):
        c = self._ctrl()
        c.activate("eco", 19.0)
        assert c.trv_off_due("off") is True

    def test_trv_off_not_due_with_frost_action(self):
        c = self._ctrl()
        c.activate("eco", 19.0)
        assert c.trv_off_due("frost_protection") is False

    def test_trv_off_not_due_while_idle(self):
        assert self._ctrl().trv_off_due("off") is False

    def test_trv_off_not_due_once_off(self):
        c = self._ctrl()
        c.activate("eco", 19.0)
        c.mark_trv_off()
        assert c.trv_off_due("off") is False

    def test_trv_on_not_due_while_the_window_is_open(self):
        c = self._ctrl()
        c.activate("eco", 19.0)
        c.mark_trv_off()
        assert c.trv_on_due() is False

    def test_trv_on_due_once_the_window_closed(self):
        c = self._ctrl()
        c.activate("eco", 19.0)
        c.mark_trv_off()
        c.restore()
        assert c.trv_on_due() is True

    def test_trv_on_due_after_cancel(self):
        """E.g. a preset change during the close delay: the TRV must heat again."""
        c = self._ctrl()
        c.activate("eco", 19.0)
        c.mark_trv_off()
        c.cancel_all()
        assert c.trv_on_due() is True

    def test_mark_trv_on_ends_trv_on_due(self):
        c = self._ctrl()
        c.activate("eco", 19.0)
        c.mark_trv_off()
        c.restore()
        c.mark_trv_on()
        assert c.trv_on_due() is False

    def test_failed_trv_off_falls_back_to_frost_protection(self):
        c = self._ctrl()
        c.activate("eco", 19.0)
        c.mark_trv_off()
        c.fail_trv_off()
        assert c.trv_off is False
        assert c.trv_off_due("off") is False
        assert c.trv_on_due() is False

    def test_failed_trv_off_is_not_retried_until_the_next_opening(self):
        """Frost protection takes over instead of an off command every cycle."""
        c = self._ctrl()
        c.activate("eco", 19.0)
        c.mark_trv_off()
        c.fail_trv_off()
        c.restore()
        c.activate("eco", 19.0)
        assert c.trv_off_due("off") is True

    def test_failed_trv_off_after_the_window_closed_needs_no_trv_on(self):
        """The window closed while the off command was in flight, and it failed."""
        c = self._ctrl()
        c.activate("eco", 19.0)
        c.mark_trv_off()
        c.restore()
        c.fail_trv_off()
        assert c.trv_on_due() is False

    # --- close handling ---

    def test_close_schedules_timer_when_active(self):
        c = self._ctrl()
        c.activate("comfort", 20.0)
        fake = FakeCallLater()
        result = c.handle_window_closed(None, 120, lambda now: None, call_later=fake)
        assert result is False  # timer scheduled, not immediate
        assert fake.scheduled_count == 1
        assert fake.calls[0]["delay"] == 120
        assert c.close_delay_active

    def test_second_close_keeps_pending_close_timer(self):
        """off → unavailable → off during the close delay must not restart it."""
        c = self._ctrl()
        c.activate("comfort", 20.0)
        fake = FakeCallLater()
        c.handle_window_closed(None, 120, lambda now: None, call_later=fake)
        result = c.handle_window_closed(None, 120, lambda now: None, call_later=fake)
        assert result is False
        assert fake.scheduled_count == 1
        assert not fake.calls[0]["cancelled"]
        assert c.close_delay_active

    def test_close_no_timer_when_not_active(self):
        c = self._ctrl()
        fake = FakeCallLater()
        result = c.handle_window_closed(None, 120, lambda now: None, call_later=fake)
        assert result is False
        assert fake.scheduled_count == 0
        assert not c.close_delay_active

    def test_close_zero_delay_returns_true_when_active(self):
        c = self._ctrl()
        c.activate("comfort", 20.0)
        fake = FakeCallLater()
        result = c.handle_window_closed(None, 0, lambda now: None, call_later=fake)
        assert result is True
        assert fake.scheduled_count == 0

    def test_close_zero_delay_returns_false_when_not_active(self):
        c = self._ctrl()
        result = c.handle_window_closed(None, 0, lambda now: None, call_later=FakeCallLater())
        assert result is False

    def test_close_cancels_pending_open_timer(self):
        c = self._ctrl()
        open_fake = FakeCallLater()
        c.handle_window_opened(None, 30, lambda now: None, call_later=open_fake)
        close_fake = FakeCallLater()
        c.handle_window_closed(None, 120, lambda now: None, call_later=close_fake)
        assert open_fake.calls[0]["cancelled"]

    # --- reopen during close delay ---

    def test_reopen_during_close_delay_cancels_close_timer(self):
        c = self._ctrl()
        c.activate("comfort", 20.0)
        close_fake = FakeCallLater()
        c.handle_window_closed(None, 120, lambda now: None, call_later=close_fake)
        assert c.close_delay_active

        open_fake = FakeCallLater()
        c.handle_window_opened(None, 30, lambda now: None, call_later=open_fake)

        # Close timer must be cancelled
        assert close_fake.calls[0]["cancelled"]
        assert not c.close_delay_active
        # No new open timer – stay in frost protection
        assert open_fake.scheduled_count == 0
        # Still active (still in frost protection)
        assert c.is_active

    def test_open_event_while_active_keeps_saved_state(self):
        """Sensor flicker on→unavailable→on while frost is active.

        A new open timer would make the window action save FROST (→ COMFORT)
        as the pre-open preset, losing the user's original preset.
        """
        c = self._ctrl()
        c.activate("eco", 17.0)
        fake = FakeCallLater()
        c.handle_window_opened(None, 30, lambda now: None, call_later=fake)
        assert fake.scheduled_count == 0
        assert c.is_active
        assert c.get_saved().preset == "eco"
        assert c.get_saved().temp == 17.0

    def test_reopen_after_full_restore_reschedules_open_timer(self):
        """After a full close→restore cycle, a new open should schedule normally."""
        c = self._ctrl()
        c.activate("comfort", 20.0)
        c.restore()  # simulate close delay firing and restoring

        fake = FakeCallLater()
        c.handle_window_opened(None, 30, lambda now: None, call_later=fake)
        assert fake.scheduled_count == 1
        assert not c.is_active  # not active yet (open delay pending)

    # --- cancel_all ---

    def test_cancel_all_resets_everything(self):
        c = self._ctrl()
        open_fake = FakeCallLater()
        c.handle_window_opened(None, 30, lambda now: None, call_later=open_fake)
        c.activate("comfort", 20.0)
        close_fake = FakeCallLater()
        c.handle_window_closed(None, 120, lambda now: None, call_later=close_fake)

        c.cancel_all()

        assert not c.is_active
        assert not c.close_delay_active
        assert c.get_saved().preset is None
        assert close_fake.calls[0]["cancelled"]

    def test_cancel_all_on_idle_is_safe(self):
        c = self._ctrl()
        c.cancel_all()  # must not raise
        assert not c.is_active

    # --- end_close_delay (user change during the close delay) ---

    def _closing(self, preset: str, temp: float | None):
        """Active window, closed again: the close delay is running."""
        c = self._ctrl()
        c.activate(preset, temp)
        fake = FakeCallLater()
        c.handle_window_closed(None, 120, lambda now: None, call_later=fake)
        return c, fake

    def test_end_close_delay_returns_the_saved_state(self):
        """What the close delay would have restored comes back now."""
        c, fake = self._closing("away", 20.0)
        saved = c.end_close_delay()
        assert saved.preset == "away"
        assert saved.temp == 20.0
        assert fake.calls[0]["cancelled"]
        assert not c.is_active
        assert not c.close_delay_active
        assert c.get_saved().preset is None

    def test_end_close_delay_returns_the_preset_saved_while_open(self):
        """E.g. Away, saved by presence detection while the window was open."""
        c, _ = self._closing("comfort", 20.0)
        c.update_saved("away", 17.0)
        saved = c.end_close_delay()
        assert saved.preset == "away"
        assert saved.temp == 17.0

    def test_end_close_delay_keeps_an_open_window(self):
        """No close delay: frost protection stays with its saved preset."""
        c = self._ctrl()
        c.activate("eco", 17.0)
        assert c.end_close_delay() is None
        assert c.is_active
        assert c.get_saved().preset == "eco"

    def test_end_close_delay_while_idle_returns_none(self):
        assert self._ctrl().end_close_delay() is None

    def test_end_close_delay_keeps_a_pending_open_delay(self):
        """A window that opened again later is not cut short."""
        c = self._ctrl()
        fake = FakeCallLater()
        c.handle_window_opened(None, 30, lambda now: None, call_later=fake)
        assert c.end_close_delay() is None
        assert not fake.calls[0]["cancelled"]

    def test_end_close_delay_needs_the_trv_switched_on(self):
        """Window action "off": the TRV must heat again right away."""
        c, _ = self._closing("away", 20.0)
        c.mark_trv_off()
        c.end_close_delay()
        assert c.trv_on_due() is True

    # --- end (e.g. the heating is turned off) ---

    def test_end_returns_the_saved_state_of_an_open_window(self):
        c = self._ctrl()
        c.activate("eco", 17.0)
        assert c.end() == SavedState("eco", 17.0)
        assert not c.is_active
        assert c.get_saved().preset is None

    def test_end_cancels_a_running_close_delay(self):
        c, fake = self._closing("away", 20.0)
        assert c.end() == SavedState("away", 20.0)
        assert fake.calls[0]["cancelled"]
        assert not c.close_delay_active

    def test_end_cancels_a_pending_open_delay(self):
        """The window detection ends for that window; nothing to restore yet."""
        c = self._ctrl()
        fake = FakeCallLater()
        c.handle_window_opened(None, 30, lambda now: None, call_later=fake)
        assert c.end() == SavedState()
        assert fake.calls[0]["cancelled"]

    def test_end_needs_the_trv_switched_on(self):
        c = self._ctrl()
        c.activate("eco", 17.0)
        c.mark_trv_off()
        c.end()
        assert c.trv_on_due() is True

    def test_close_timer_after_end_close_delay_restores_nothing(self):
        """A stale timer callback finds nothing left to restore."""
        c, _ = self._closing("eco", 17.0)
        c.end_close_delay()
        assert c.restore().preset is None

    # --- update_saved ---

    def test_update_saved_changes_saved_state(self):
        c = self._ctrl()
        c.activate("comfort", 20.0)
        assert c.get_saved().preset == "comfort"

        c.update_saved("eco", 17.0)
        assert c.get_saved().preset == "eco"
        assert c.get_saved().temp == 17.0
        # Still active
        assert c.is_active

    def test_update_saved_then_restore(self):
        """After update_saved, restore should return the updated state."""
        c = self._ctrl()
        c.activate("comfort", 20.0)
        c.update_saved("away", 16.0)
        saved = c.restore()
        assert saved.preset == "away"
        assert saved.temp == 16.0
        assert not c.is_active

    def test_update_saved_while_not_active_still_works(self):
        """update_saved works even if not active (defensive)."""
        c = self._ctrl()
        c.update_saved("eco", 19.0)
        assert c.get_saved().preset == "eco"
        assert not c.is_active


# ---------------------------------------------------------------------------
# PresenceAutomationController
# ---------------------------------------------------------------------------

class TestPresenceController:

    def _ctrl(self) -> PresenceAutomationController:
        return PresenceAutomationController()

    # --- initial state ---

    def test_initial_not_active(self):
        assert not self._ctrl().is_active

    # --- away handling ---

    def test_away_schedules_timer(self):
        c = self._ctrl()
        fake = FakeCallLater()
        c.handle_presence_away(None, 1800, lambda now: None, call_later=fake)
        assert fake.scheduled_count == 1
        assert fake.calls[0]["delay"] == 1800

    def test_repeated_away_keeps_pending_timer(self):
        """off → unavailable → off during the away delay must not restart it."""
        c = self._ctrl()
        fake = FakeCallLater()
        c.handle_presence_away(None, 600, lambda now: None, call_later=fake)
        c.handle_presence_away(None, 600, lambda now: None, call_later=fake)
        assert fake.scheduled_count == 1
        assert not fake.calls[0]["cancelled"]

    # --- activate / restore ---

    def test_activate_sets_state(self):
        c = self._ctrl()
        c.activate("eco", 19.0)
        assert c.is_active
        assert c.restore().preset == "eco"

    def test_restore_clears_state(self):
        c = self._ctrl()
        c.activate("away", 16.0)
        saved = c.restore()
        assert saved.preset == "away"
        assert saved.temp == 16.0
        assert not c.is_active

    def test_restore_without_activate_is_safe(self):
        c = self._ctrl()
        saved = c.restore()
        assert saved.preset is None

    # --- home handling ---

    def test_home_cancels_pending_timer(self):
        c = self._ctrl()
        fake = FakeCallLater()
        c.handle_presence_away(None, 1800, lambda now: None, call_later=fake)
        result = c.handle_presence_home()
        assert fake.calls[0]["cancelled"]
        assert result is False  # not active (timer was pending, never fired)

    def test_home_returns_true_when_active(self):
        c = self._ctrl()
        c.activate("comfort", 21.0)
        result = c.handle_presence_home()
        assert result is True

    def test_home_returns_false_when_not_active(self):
        c = self._ctrl()
        result = c.handle_presence_home()
        assert result is False

    def test_home_while_timer_pending_and_not_active(self):
        """Home arrives before the away delay fires → no restore needed."""
        c = self._ctrl()
        fake = FakeCallLater()
        c.handle_presence_away(None, 1800, lambda now: None, call_later=fake)
        result = c.handle_presence_home()
        assert result is False
        assert fake.calls[0]["cancelled"]

    # --- cancel_timer ---

    def test_cancel_timer_without_timer_is_safe(self):
        c = self._ctrl()
        c.cancel_timer()  # must not raise
        assert not c.is_active

    def test_cancel_timer_does_not_change_active_flag(self):
        c = self._ctrl()
        c.activate("comfort", 20.0)
        c.cancel_timer()
        assert c.is_active  # active flag is preserved

    # --- interaction: away timer fires while already active ---

    def test_second_away_event_skips_timer_while_active(self):
        """When already active, a new away event must NOT schedule a timer.

        Starting a new away timer would cause the away-action to overwrite
        the saved preset with PRESET_AWAY, losing the original state.
        """
        c = self._ctrl()
        c.activate("comfort", 20.0)  # already away-active
        fake = FakeCallLater()
        c.handle_presence_away(None, 1800, lambda now: None, call_later=fake)
        assert fake.scheduled_count == 0  # no timer scheduled
        assert c.is_active  # active flag unchanged
        # Saved state must be preserved
        saved = c.restore()
        assert saved.preset == "comfort"
        assert saved.temp == 20.0

    # --- update_saved ---

    def test_update_saved_changes_saved_state(self):
        c = self._ctrl()
        c.activate("comfort", 20.0)
        c.update_saved("eco", 17.0)
        saved = c.restore()
        assert saved.preset == "eco"
        assert saved.temp == 17.0

    def test_update_saved_preserves_active_flag(self):
        c = self._ctrl()
        c.activate("comfort", 20.0)
        c.update_saved("eco", 17.0)
        assert c.is_active

    def test_update_saved_while_not_active(self):
        """update_saved works even if not active (defensive)."""
        c = self._ctrl()
        c.update_saved("eco", 19.0)
        saved = c.restore()
        assert saved.preset == "eco"
        assert not c.is_active

    def test_update_saved_multiple_times_keeps_last(self):
        """Multiple update_saved calls keep only the last one."""
        c = self._ctrl()
        c.activate("comfort", 20.0)
        c.update_saved("eco", 17.0)
        c.update_saved("boost", 25.0)
        saved = c.restore()
        assert saved.preset == "boost"
        assert saved.temp == 25.0

    # --- home delay (debounce) ---

    def test_home_delay_schedules_timer_when_active(self):
        """With delay > 0 and active, a home timer is scheduled, return False."""
        c = self._ctrl()
        c.activate("comfort", 21.0)
        fake = FakeCallLater()
        actions = []
        result = c.handle_presence_home(
            None, 30, lambda now: actions.append(now), call_later=fake
        )
        assert result is False  # not immediate restore
        assert fake.scheduled_count == 1
        assert fake.calls[0]["delay"] == 30
        assert c.is_active  # still active until timer fires

    def test_home_delay_zero_immediate_restore(self):
        """With delay == 0, behaves like before: returns True when active."""
        c = self._ctrl()
        c.activate("comfort", 21.0)
        result = c.handle_presence_home(None, 0, None)
        assert result is True

    def test_home_delay_not_active_no_timer(self):
        """When not active, no home timer is scheduled regardless of delay."""
        c = self._ctrl()
        fake = FakeCallLater()
        result = c.handle_presence_home(
            None, 30, lambda now: None, call_later=fake
        )
        assert result is False
        assert fake.scheduled_count == 0

    def test_away_cancels_home_timer_no_new_away_timer(self):
        """Away event during home-pending cancels the home timer.

        Since is_active is True, no new away timer should be scheduled –
        this prevents the away-action from overwriting the saved state.
        """
        c = self._ctrl()
        c.activate("comfort", 21.0)
        home_fake = FakeCallLater()
        c.handle_presence_home(
            None, 30, lambda now: None, call_later=home_fake
        )
        assert home_fake.active_count == 1

        away_fake = FakeCallLater()
        c.handle_presence_away(None, 600, lambda now: None, call_later=away_fake)

        # Home timer must be cancelled
        assert home_fake.calls[0]["cancelled"]
        # No new away timer (already active)
        assert away_fake.scheduled_count == 0
        # Still active (away mode persists)
        assert c.is_active
        # Saved state preserved
        saved = c.restore()
        assert saved.preset == "comfort"
        assert saved.temp == 21.0

    def test_flicker_scenario_no_restore(self):
        """Full flicker scenario: activate → home(delay) → away.

        The home timer should be cancelled, no new away timer scheduled
        (already active), and the original saved state must be preserved.
        """
        c = self._ctrl()
        c.activate("comfort", 21.0)

        home_fake = FakeCallLater()
        home_actions = []
        c.handle_presence_home(
            None, 30, lambda now: home_actions.append(now), call_later=home_fake
        )

        away_fake = FakeCallLater()
        c.handle_presence_away(None, 600, lambda now: None, call_later=away_fake)

        # Home callback never fired
        assert len(home_actions) == 0
        assert home_fake.calls[0]["cancelled"]
        # No new away timer (already active)
        assert away_fake.scheduled_count == 0
        # Controller still active
        assert c.is_active
        # Saved state preserved
        saved = c.restore()
        assert saved.preset == "comfort"
        assert saved.temp == 21.0

    def test_home_timer_fires_callback(self):
        """When the home delay timer fires, the callback is invoked."""
        c = self._ctrl()
        c.activate("comfort", 21.0)
        fake = FakeCallLater()
        actions = []
        c.handle_presence_home(
            None, 30, lambda now: actions.append("restored"), call_later=fake
        )
        fake.trigger(0, now=None)
        assert actions == ["restored"]

    def test_repeated_home_keeps_pending_timer(self):
        """Repeated on events keep the pending home delay."""
        c = self._ctrl()
        c.activate("comfort", 21.0)
        fake = FakeCallLater()
        c.handle_presence_home(
            None, 30, lambda now: None, call_later=fake
        )
        result = c.handle_presence_home(
            None, 30, lambda now: None, call_later=fake
        )
        assert result is False
        assert fake.scheduled_count == 1
        assert not fake.calls[0]["cancelled"]

    def test_sensor_loss_cancels_only_home_timer(self):
        """A missing sensor must not discard the saved preset or an away delay."""
        controller = self._ctrl()
        controller.activate("comfort", 21.0)
        home_fake = FakeCallLater()
        controller.handle_presence_home(
            None, 30, lambda now: None, call_later=home_fake
        )

        controller.cancel_home_timer()

        assert home_fake.calls[0]["cancelled"]
        assert controller.is_active
        assert controller.restore() == SavedState(preset="comfort", temp=21.0)

        away_fake = FakeCallLater()
        controller.handle_presence_away(None, 600, lambda now: None, call_later=away_fake)
        controller.cancel_home_timer()
        assert away_fake.active_count == 1

    def test_home_event_after_sensor_loss_starts_new_delay(self):
        controller = self._ctrl()
        controller.activate("comfort", 21.0)
        fake = FakeCallLater()
        controller.handle_presence_home(None, 30, lambda now: None, call_later=fake)
        controller.cancel_home_timer()

        result = controller.handle_presence_home(
            None, 30, lambda now: None, call_later=fake
        )

        assert result is False
        assert fake.scheduled_count == 2
        assert fake.calls[0]["cancelled"]
        assert not fake.calls[1]["cancelled"]

    def test_restore_cancels_home_timer(self):
        """Calling restore() defensively cancels any pending home timer."""
        c = self._ctrl()
        c.activate("comfort", 21.0)
        fake = FakeCallLater()
        c.handle_presence_home(
            None, 30, lambda now: None, call_later=fake
        )
        saved = c.restore()
        assert fake.calls[0]["cancelled"]
        assert saved.preset == "comfort"
        assert not c.is_active

    def test_cancel_timer_cancels_both_timers(self):
        """cancel_timer() cancels both away and home timers."""
        c = self._ctrl()
        away_fake = FakeCallLater()
        c.handle_presence_away(None, 600, lambda now: None, call_later=away_fake)
        c.activate("comfort", 21.0)
        home_fake = FakeCallLater()
        c.handle_presence_home(
            None, 30, lambda now: None, call_later=home_fake
        )
        c.cancel_timer()
        assert home_fake.calls[0]["cancelled"]

    def test_home_with_delay_cancels_pending_away_timer(self):
        """Home event cancels pending away timer (not yet fired).

        When the away timer is still pending and a home event arrives,
        the away timer must be cancelled.  Since the controller is not yet
        active, no home timer is scheduled (nothing to restore).
        """
        c = self._ctrl()
        away_fake = FakeCallLater()
        c.handle_presence_away(None, 600, lambda now: None, call_later=away_fake)

        # Home arrives before away timer fired
        result = c.handle_presence_home()
        # Away timer must be cancelled
        assert away_fake.calls[0]["cancelled"]
        # Not active → no restore needed
        assert result is False

    def test_away_while_active_preserves_saved_state(self):
        """Repeated away events while active must not overwrite saved state.

        This is the core bug fix: sensor flicker (Home → Away) while already
        in away mode must NOT schedule a new away timer, because the timer
        callback would save PRESET_AWAY as the 'previous' preset.
        """
        c = self._ctrl()
        c.activate("eco", 19.0)

        fake = FakeCallLater()
        c.handle_presence_away(None, 600, lambda now: None, call_later=fake)

        # No timer scheduled
        assert fake.scheduled_count == 0
        # Active flag and saved state unchanged
        assert c.is_active
        saved = c.restore()
        assert saved.preset == "eco"
        assert saved.temp == 19.0

    def test_multi_flicker_preserves_saved_state(self):
        """Multiple home/away flickers must preserve the original saved state.

        Scenario: active(comfort) → home → away → home → away → home → restore
        """
        c = self._ctrl()
        c.activate("comfort", 21.0)

        for _ in range(3):
            home_fake = FakeCallLater()
            c.handle_presence_home(
                None, 30, lambda now: None, call_later=home_fake
            )
            away_fake = FakeCallLater()
            c.handle_presence_away(None, 600, lambda now: None, call_later=away_fake)
            # Home timer cancelled, no new away timer
            assert home_fake.calls[0]["cancelled"]
            assert away_fake.scheduled_count == 0

        # After all flickers, saved state is still the original
        assert c.is_active
        saved = c.restore()
        assert saved.preset == "comfort"
        assert saved.temp == 21.0


class TestPresenceGuestMode:
    """Guest mode: presence-away is suppressed so guests get normal heating."""

    def _ctrl(self) -> PresenceAutomationController:
        return PresenceAutomationController()

    def test_guest_mode_off_by_default(self):
        assert self._ctrl().guest_mode is False

    def test_enable_sets_flag(self):
        c = self._ctrl()
        c.set_guest_mode(True)
        assert c.guest_mode is True

    def test_disable_clears_flag(self):
        c = self._ctrl()
        c.set_guest_mode(True)
        c.set_guest_mode(False)
        assert c.guest_mode is False

    def test_away_event_ignored_while_guest_mode(self):
        """Nobody home but guests present → no away timer is scheduled."""
        c = self._ctrl()
        c.set_guest_mode(True)
        fake = FakeCallLater()
        c.handle_presence_away(None, 600, lambda now: None, call_later=fake)
        assert fake.scheduled_count == 0
        assert not c.is_active

    def test_enable_cancels_pending_away_timer(self):
        """Guest mode switched on during the away delay → AWAY never fires."""
        c = self._ctrl()
        fake = FakeCallLater()
        c.handle_presence_away(None, 600, lambda now: None, call_later=fake)
        c.set_guest_mode(True)
        assert fake.calls[0]["cancelled"]

    def test_enable_cancels_pending_home_timer(self):
        c = self._ctrl()
        c.activate("comfort", 21.0)
        fake = FakeCallLater()
        c.handle_presence_home(None, 30, lambda now: None, call_later=fake)
        c.set_guest_mode(True)
        assert fake.calls[0]["cancelled"]

    def test_enable_while_away_active_requests_restore(self):
        """Already in AWAY → enabling returns True so the caller restores now."""
        c = self._ctrl()
        c.activate("eco", 17.0)
        assert c.set_guest_mode(True) is True
        # Saved state is still available for the caller's restore
        saved = c.restore()
        assert saved.preset == "eco"
        assert saved.temp == 17.0

    def test_enable_while_not_active_requests_no_restore(self):
        c = self._ctrl()
        assert c.set_guest_mode(True) is False

    def test_disable_never_requests_restore(self):
        c = self._ctrl()
        c.set_guest_mode(True)
        assert c.set_guest_mode(False) is False

    def test_away_event_scheduled_again_after_disable(self):
        """Guests leave → guest mode off → away automation works again."""
        c = self._ctrl()
        c.set_guest_mode(True)
        c.set_guest_mode(False)
        fake = FakeCallLater()
        c.handle_presence_away(None, 600, lambda now: None, call_later=fake)
        assert fake.scheduled_count == 1
        assert fake.calls[0]["delay"] == 600

    def test_home_event_harmless_while_guest_mode(self):
        c = self._ctrl()
        c.set_guest_mode(True)
        assert c.handle_presence_home() is False
        assert c.guest_mode is True


# ---------------------------------------------------------------------------
# Binary sensor event filter
# ---------------------------------------------------------------------------

class TestBinarySensorTransition:
    """Only real on/off changes may drive window/presence automation."""

    def test_off_to_on_is_transition(self):
        assert is_binary_sensor_transition("off", "on") is True

    def test_on_to_off_is_transition(self):
        assert is_binary_sensor_transition("on", "off") is True

    def test_attribute_only_update_is_ignored(self):
        """Zigbee sensors re-publish battery/linkquality with the same state.

        Treating those as new events would restart the open/away delay
        timers again and again, so frost/away would never trigger.
        """
        assert is_binary_sensor_transition("on", "on") is False
        assert is_binary_sensor_transition("off", "off") is False

    def test_unavailable_new_state_is_ignored(self):
        assert is_binary_sensor_transition("on", "unavailable") is False
        assert is_binary_sensor_transition("off", "unknown") is False

    def test_missing_new_state_is_ignored(self):
        assert is_binary_sensor_transition("on", None) is False

    def test_recovery_from_unavailable_is_transition(self):
        """unavailable → on must still be processed (controller guards dedupe)."""
        assert is_binary_sensor_transition("unavailable", "on") is True

    def test_first_state_is_transition(self):
        assert is_binary_sensor_transition(None, "off") is True


# ---------------------------------------------------------------------------
# FollowPhysicalController
# ---------------------------------------------------------------------------

class TestFollowPhysicalController:

    def _call(self, **kwargs):
        return FollowPhysicalController.should_follow(**kwargs)

    # --- no baseline ---

    def test_no_baseline_returns_false(self):
        assert not self._call(
            tado_setpoint=21.0,
            last_sent=None,
            last_sent_ts=0.0,
            threshold_c=0.5,
            grace_s=20.0,
            now=1000.0,
        )

    # --- threshold ---

    def test_within_threshold_returns_false(self):
        assert not self._call(
            tado_setpoint=20.3,
            last_sent=20.0,
            last_sent_ts=0.0,
            threshold_c=0.5,
            grace_s=20.0,
            now=1000.0,
        )

    def test_exactly_at_threshold_returns_false(self):
        assert not self._call(
            tado_setpoint=20.5,
            last_sent=20.0,
            last_sent_ts=0.0,
            threshold_c=0.5,
            grace_s=20.0,
            now=1000.0,
        )

    def test_just_above_threshold_returns_true(self):
        assert self._call(
            tado_setpoint=20.6,
            last_sent=20.0,
            last_sent_ts=0.0,
            threshold_c=0.5,
            grace_s=20.0,
            now=1000.0,
        )

    def test_negative_divergence_above_threshold_returns_true(self):
        """Change in the cooling direction should also be detected."""
        assert self._call(
            tado_setpoint=19.4,
            last_sent=20.0,
            last_sent_ts=0.0,
            threshold_c=0.5,
            grace_s=20.0,
            now=1000.0,
        )

    # --- grace period ---

    def test_within_grace_returns_false(self):
        now = time.time()
        assert not self._call(
            tado_setpoint=21.0,
            last_sent=20.0,
            last_sent_ts=now - 5,  # 5s ago → within 20s grace
            threshold_c=0.5,
            grace_s=20.0,
        )

    def test_exactly_at_grace_boundary_returns_true(self):
        """Exactly at grace_s means the grace period has expired → follow."""
        now = 1000.0
        assert self._call(
            tado_setpoint=21.0,
            last_sent=20.0,
            last_sent_ts=980.0,  # exactly 20.0s ago (not strictly < 20.0)
            threshold_c=0.5,
            grace_s=20.0,
            now=now,
        )

    def test_just_inside_grace_returns_false(self):
        """0.1s before grace expires → still within grace period → no follow."""
        now = 1000.0
        assert not self._call(
            tado_setpoint=21.0,
            last_sent=20.0,
            last_sent_ts=980.1,  # 19.9s ago → within 20s grace
            threshold_c=0.5,
            grace_s=20.0,
            now=now,
        )

    def test_just_past_grace_returns_true(self):
        now = 1000.0
        assert self._call(
            tado_setpoint=21.0,
            last_sent=20.0,
            last_sent_ts=979.0,  # 21s ago → past grace
            threshold_c=0.5,
            grace_s=20.0,
            now=now,
        )

    # --- all conditions met ---

    def test_should_follow_when_all_conditions_met(self):
        assert self._call(
            tado_setpoint=22.0,
            last_sent=20.0,
            last_sent_ts=0.0,
            threshold_c=0.5,
            grace_s=20.0,
            now=1000.0,
        )

    # --- now defaults to time.time() ---

    def test_now_defaults_to_current_time(self):
        """Without explicit now, should_follow uses time.time(); old command → True."""
        assert self._call(
            tado_setpoint=22.0,
            last_sent=20.0,
            last_sent_ts=0.0,  # very old → well past grace
            threshold_c=0.5,
            grace_s=20.0,
            # no 'now' → uses time.time()
        )


# ---------------------------------------------------------------------------
# SavedState
# ---------------------------------------------------------------------------

class TestSavedState:

    def test_defaults_are_none(self):
        s = SavedState()
        assert s.preset is None
        assert s.temp is None

    def test_fields_set_correctly(self):
        s = SavedState(preset="eco", temp=19.5)
        assert s.preset == "eco"
        assert s.temp == 19.5


# ---------------------------------------------------------------------------
# BoostController
# ---------------------------------------------------------------------------

class TestBoostController:

    def _started(self, duration_s=1800.0, now=1000.0):
        bc = BoostController()
        cl = FakeCallLater()
        bc.start(None, duration_s, lambda _now: None, call_later=cl, now=now)
        return bc, cl

    # --- initial state ---

    def test_initial_state(self):
        bc = BoostController()
        assert not bc.is_active
        assert bc.end_ts == 0.0
        assert bc.remaining_minutes(now=1000.0) == 0
        assert bc.saved == SavedState(preset="comfort", temp=None)

    # --- start ---

    def test_start_schedules_timer_and_sets_end_ts(self):
        bc, cl = self._started(duration_s=1800.0, now=1000.0)
        assert bc.is_active
        assert bc.end_ts == 2800.0
        assert cl.scheduled_count == 1
        assert cl.calls[0]["delay"] == 1800.0

    def test_start_replaces_running_timer(self):
        bc, cl = self._started(now=1000.0)
        bc.start(None, 600.0, lambda _now: None, call_later=cl, now=2000.0)
        assert cl.calls[0]["cancelled"]
        assert cl.active_count == 1
        assert bc.end_ts == 2600.0

    def test_start_keeps_saved_state(self):
        bc = BoostController()
        bc.save("eco", 17.0)
        bc.start(None, 60.0, lambda _now: None, call_later=FakeCallLater(), now=0.0)
        assert bc.saved == SavedState(preset="eco", temp=17.0)

    # --- remaining minutes ---

    def test_remaining_minutes_rounds_up(self):
        bc, _ = self._started(duration_s=1800.0, now=1000.0)
        assert bc.remaining_minutes(now=1000.0) == 30
        assert bc.remaining_minutes(now=1001.0) == 30   # 29.98 min -> 30
        assert bc.remaining_minutes(now=2740.0) == 1    # 60 s left
        assert bc.remaining_minutes(now=2741.0) == 1    # 59 s left -> 1

    def test_remaining_minutes_never_negative(self):
        bc, _ = self._started(duration_s=60.0, now=1000.0)
        assert bc.remaining_minutes(now=5000.0) == 0

    # --- cancel ---

    def test_cancel_stops_timer(self):
        bc, cl = self._started()
        bc.cancel()
        assert not bc.is_active
        assert bc.end_ts == 0.0
        assert cl.calls[0]["cancelled"]
        assert bc.remaining_minutes(now=1000.0) == 0

    def test_cancel_when_inactive_is_noop(self):
        bc = BoostController()
        bc.cancel()
        assert not bc.is_active

    def test_cancel_keeps_saved_state(self):
        bc, _ = self._started()
        bc.save("eco", 17.0)
        bc.cancel()
        assert bc.saved == SavedState(preset="eco", temp=17.0)

    # --- interrupt (window/presence takes over) ---

    def test_interrupt_without_boost_returns_current_state(self):
        bc = BoostController()
        bc.save("eco", 17.0)
        assert bc.interrupt("comfort", 21.0) == SavedState(preset="comfort", temp=21.0)

    def test_interrupt_running_boost_returns_pre_boost_state(self):
        bc, cl = self._started()
        bc.save("eco", 17.0)
        saved = bc.interrupt("boost", 21.0)
        assert saved == SavedState(preset="eco", temp=17.0)
        assert not bc.is_active
        assert bc.end_ts == 0.0
        assert cl.calls[0]["cancelled"]

    # --- expire ---

    def test_expire_returns_saved_and_deactivates(self):
        bc, cl = self._started()
        bc.save("none", 19.5)
        saved = bc.expire()
        assert saved == SavedState(preset="none", temp=19.5)
        assert not bc.is_active
        assert bc.end_ts == 0.0
        # The timer already fired, so its cancel callback is not invoked.
        assert not cl.calls[0]["cancelled"]

    def test_expire_returns_copy(self):
        bc = BoostController()
        saved = bc.expire()
        saved.preset = "eco"
        assert bc.saved.preset == "comfort"


# ---------------------------------------------------------------------------
# decide_send (send / rate-limit decision)
# ---------------------------------------------------------------------------

class TestDecideSend:
    """Every branch with the exact regulation_reason strings.

    Defaults match RegulationConfig/BehaviourConfig: 180 s interval,
    0.3 °C change threshold, 1.0 °C urgent decrease, overlay refresh off.
    """

    NOW = 1_000_000.0

    def _decide(self, **overrides):
        args = {
            "target_c": 21.0,
            "baseline_c": 20.0,
            "last_command_ts": self.NOW - 90.0,
            "now": self.NOW,
            "min_interval_s": 180.0,
            "min_change_c": 0.3,
            "urgent_decrease_c": 1.0,
            "overlay_refresh_s": 0,
        }
        args.update(overrides)
        return decide_send(**args)

    # --- no baseline ---

    def test_no_baseline_first_attempt_sends(self):
        assert self._decide(baseline_c=None, last_command_ts=0.0) == (True, "no_baseline")

    def test_no_baseline_retry_is_rate_limited(self):
        assert self._decide(baseline_c=None) == (False, "rate_limited(90s)")

    def test_no_baseline_retry_after_interval_sends(self):
        result = self._decide(baseline_c=None, last_command_ts=self.NOW - 180.0)
        assert result == (True, "no_baseline")

    # --- below change threshold ---

    def test_small_change_is_already_at_target(self):
        assert self._decide(target_c=20.2) == (False, "already_at_target")

    def test_change_at_threshold_is_not_small(self):
        result = self._decide(
            target_c=20.25, min_change_c=0.25, last_command_ts=self.NOW - 200.0
        )
        assert result == (True, "normal_update")

    def test_small_change_ignores_rate_limit_expiry(self):
        result = self._decide(target_c=20.2, last_command_ts=self.NOW - 10_000.0)
        assert result == (False, "already_at_target")

    # --- overlay refresh ---

    def test_overlay_refresh_due_resends(self):
        result = self._decide(
            target_c=20.0, overlay_refresh_s=600, last_command_ts=self.NOW - 600.0
        )
        assert result == (True, "overlay_refresh")

    def test_overlay_refresh_not_yet_due(self):
        result = self._decide(
            target_c=20.0, overlay_refresh_s=600, last_command_ts=self.NOW - 599.0
        )
        assert result == (False, "already_at_target")

    def test_overlay_refresh_only_for_unchanged_target(self):
        """A real change after the refresh interval is a normal update."""
        result = self._decide(
            target_c=21.0, overlay_refresh_s=600, last_command_ts=self.NOW - 600.0
        )
        assert result == (True, "normal_update")

    def test_overlay_refresh_ignored_without_baseline(self):
        result = self._decide(baseline_c=None, overlay_refresh_s=60)
        assert result == (False, "rate_limited(90s)")

    # --- rate limit ---

    def test_increase_within_interval_is_rate_limited(self):
        assert self._decide(target_c=22.0) == (False, "rate_limited(90s)")

    def test_rate_limited_reason_truncates_seconds(self):
        result = self._decide(target_c=22.0, last_command_ts=self.NOW - 100.6)
        assert result == (False, "rate_limited(79s)")

    def test_decrease_at_urgent_threshold_is_rate_limited(self):
        assert self._decide(target_c=19.0) == (False, "rate_limited(90s)")

    def test_urgent_decrease_bypasses_rate_limit(self):
        assert self._decide(target_c=18.9) == (True, "urgent_decrease")

    def test_rate_limit_ends_at_interval(self):
        result = self._decide(target_c=22.0, last_command_ts=self.NOW - 180.0)
        assert result == (True, "normal_update")

    # --- result type ---

    def test_result_has_named_fields(self):
        result = self._decide(target_c=22.0, last_command_ts=0.0)
        assert result.should_send is True
        assert result.reason == "normal_update"


# ---------------------------------------------------------------------------
# preset_target_c (preset -> room target)
# ---------------------------------------------------------------------------

class TestPresetTarget:

    PRESETS = PresetConfig(
        comfort_target_c=20.0,
        eco_target_c=17.5,
        boost_target_c=25.0,
        away_target_c=16.0,
        frost_protection_target_c=7.0,
    )

    def _target(self, preset):
        return preset_target_c(preset, self.PRESETS, comfort_c=21.5, manual_c=19.0)

    def test_comfort_uses_comfort_value(self):
        assert self._target("comfort") == 21.5

    def test_manual_uses_manual_value(self):
        assert self._target("none") == 19.0

    def test_eco(self):
        assert self._target("eco") == 17.5

    def test_boost(self):
        assert self._target("boost") == 25.0

    def test_away(self):
        assert self._target("away") == 16.0

    def test_frost_protection(self):
        assert self._target("frost_protection") == 7.0

    def test_unknown_preset_falls_back_to_frost_protection(self):
        assert self._target("sleep") == 7.0

    def test_comfort_ignores_preset_config_default(self):
        """The comfort target is resolved by the caller (options/base target)."""
        assert preset_target_c(
            "comfort", PresetConfig(), comfort_c=22.0, manual_c=19.0
        ) == 22.0


class TestRouteChange:
    """Where a preset or temperature change goes while an automation holds the room."""

    def test_change_applies_without_automation(self):
        assert route_change("eco", presence_away=False, window_open=False) == CHANGE_APPLY

    def test_change_while_away_is_saved_for_the_return(self):
        assert route_change("eco", presence_away=True, window_open=False) == CHANGE_FOR_PRESENCE

    def test_picking_away_while_away_applies(self):
        assert route_change("away", presence_away=True, window_open=False) == CHANGE_APPLY

    def test_change_while_the_window_is_open_is_saved_for_the_close(self):
        assert route_change("eco", presence_away=False, window_open=True) == CHANGE_FOR_WINDOW

    def test_picking_frost_protection_while_the_window_is_open_applies(self):
        assert (
            route_change("frost_protection", presence_away=False, window_open=True)
            == CHANGE_APPLY
        )

    def test_presence_comes_before_the_window(self):
        """Nobody home and a window open: the window keeps Away to restore."""
        assert route_change("eco", presence_away=True, window_open=True) == CHANGE_FOR_PRESENCE

    def test_temperature_while_away_with_an_open_window_waits_for_the_return(self):
        """A temperature is the manual preset ("none")."""
        assert route_change("none", presence_away=True, window_open=True) == CHANGE_FOR_PRESENCE

    def test_picking_away_while_away_with_an_open_window_waits_for_the_close(self):
        assert route_change("away", presence_away=True, window_open=True) == CHANGE_FOR_WINDOW


class TestPresetAfterWindow:
    """What an ending window automation brings back (comfort target 21.5 °C)."""

    def _after(self, preset, temp, *, presence_home=False, heating_off=False):
        return preset_after_window(
            SavedState(preset, temp),
            presence_home=presence_home,
            comfort_c=21.5,
            heating_off=heating_off,
        )

    def test_saved_preset_comes_back_with_its_target(self):
        assert self._after("eco", 17.0) == SavedState("eco", 17.0)

    def test_manual_temperature_comes_back(self):
        assert self._after("none", 22.5) == SavedState("none", 22.5)

    def test_comfort_comes_back_with_the_current_comfort_target(self):
        """The comfort target may have changed while the window was open."""
        assert self._after("comfort", 20.0) == SavedState("comfort", 21.5)

    def test_frost_protection_never_comes_back(self):
        assert self._after("frost_protection", 7.0) == SavedState("comfort", 21.5)

    def test_nothing_saved_comes_back_as_comfort(self):
        assert self._after(None, None) == SavedState("comfort", 21.5)

    def test_away_comes_back_while_nobody_is_home(self):
        assert self._after("away", 20.0) == SavedState("away", 20.0)

    def test_away_gives_way_to_comfort_when_someone_is_home(self):
        assert self._after("away", 20.0, presence_home=True) == SavedState("comfort", 21.5)

    def test_other_presets_stay_when_someone_is_home(self):
        assert self._after("eco", 17.0, presence_home=True) == SavedState("eco", 17.0)

    def test_boost_comes_back(self):
        """Its timer starts again – that is the caller's part."""
        assert self._after("boost", 20.0) == SavedState("boost", 20.0)

    def test_heating_off_brings_back_comfort_for_a_boost(self):
        """The boost timer would run out while the heating is off."""
        assert self._after("boost", 20.0, heating_off=True) == SavedState("comfort", 21.5)

    def test_heating_off_uses_the_same_rules_otherwise(self):
        assert self._after(
            "away", 20.0, presence_home=True, heating_off=True
        ) == SavedState("comfort", 21.5)
        assert self._after("eco", 17.0, heating_off=True) == SavedState("eco", 17.0)


class TestPresenceCountsAsHome:

    def test_someone_home(self):
        assert presence_counts_as_home("on", guest_mode=False) is True

    def test_nobody_home(self):
        assert presence_counts_as_home("off", guest_mode=False) is False

    def test_unavailable_or_unknown_sensor_is_not_home(self):
        for state in ("unavailable", "unknown"):
            assert presence_counts_as_home(state, guest_mode=False) is False

    def test_no_sensor_is_not_home(self):
        assert presence_counts_as_home(None, guest_mode=False) is False

    def test_guest_mode_counts_as_home(self):
        assert presence_counts_as_home("off", guest_mode=True) is True
        assert presence_counts_as_home(None, guest_mode=True) is True


class TestPresetAfterRestart:
    """Which preset a restart (or reload) starts with."""

    NO_WINDOW = SavedState()

    def test_window_frost_gives_way_to_the_saved_preset(self):
        r = preset_after_restart(
            "frost_protection", None, SavedState("eco", 20.0), window_open=False
        )
        assert r.preset == "eco"
        assert r.resume_window is False

    def test_window_still_open_resumes_frost_protection(self):
        r = preset_after_restart(
            "frost_protection", None, SavedState("eco", 20.0), window_open=True
        )
        assert r.preset == "eco"
        assert r.resume_window is True

    def test_window_saved_manual_preset_keeps_its_temperature(self):
        r = preset_after_restart(
            "frost_protection", None, SavedState("none", 21.5), window_open=False
        )
        assert (r.preset, r.manual_c) == ("none", 21.5)

    def test_boost_comes_back_as_comfort(self):
        """The boost timer does not survive a restart."""
        r = preset_after_restart("boost", None, self.NO_WINDOW, window_open=False)
        assert r.preset == "comfort"

    def test_frost_without_saved_preset_comes_back_as_comfort(self):
        """E.g. a state written before the saved preset was kept."""
        r = preset_after_restart("frost_protection", None, self.NO_WINDOW, window_open=True)
        assert (r.preset, r.resume_window) == ("comfort", False)

    def test_missing_or_unknown_preset_comes_back_as_comfort(self):
        for preset in (None, "sleep"):
            r = preset_after_restart(preset, 19.0, self.NO_WINDOW, window_open=False)
            assert (r.preset, r.manual_c) == ("comfort", None)

    def test_manual_preset_keeps_its_temperature(self):
        r = preset_after_restart("none", 21.5, self.NO_WINDOW, window_open=False)
        assert (r.preset, r.manual_c) == ("none", 21.5)

    def test_named_preset_is_kept_without_a_manual_temperature(self):
        r = preset_after_restart("eco", 21.5, self.NO_WINDOW, window_open=True)
        assert (r.preset, r.manual_c, r.resume_window) == ("eco", None, False)

    def test_window_sensor_not_reported_yet_keeps_frost_protection(self):
        """Like a sensor that drops out while open: no news is not "closed"."""
        r = preset_after_restart(
            "frost_protection", None, SavedState("eco", 20.0), window_open=None
        )
        assert (r.preset, r.resume_window) == ("eco", True)

    def test_trv_switched_off_by_a_closed_window_is_switched_on(self):
        r = preset_after_restart(
            "frost_protection", None, SavedState("eco", 20.0), window_open=False, trv_off=True
        )
        assert r.switch_trv_on is True

    def test_trv_under_a_still_open_window_stays_off(self):
        r = preset_after_restart(
            "frost_protection", None, SavedState("eco", 20.0), window_open=True, trv_off=True
        )
        assert (r.resume_window, r.switch_trv_on) == (True, False)
