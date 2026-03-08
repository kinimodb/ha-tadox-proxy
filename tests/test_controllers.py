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

WindowAutomationController = _ctrl_mod.WindowAutomationController
PresenceAutomationController = _ctrl_mod.PresenceAutomationController
FollowPhysicalController = _ctrl_mod.FollowPhysicalController
SavedState = _ctrl_mod.SavedState


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

    def test_second_open_cancels_first_timer(self):
        c = self._ctrl()
        fake = FakeCallLater()
        c.handle_window_opened(None, 30, lambda now: None, call_later=fake)
        c.handle_window_opened(None, 30, lambda now: None, call_later=fake)
        assert fake.calls[0]["cancelled"]
        assert not fake.calls[1]["cancelled"]
        assert fake.active_count == 1

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

    def test_repeated_away_replaces_timer(self):
        c = self._ctrl()
        fake = FakeCallLater()
        c.handle_presence_away(None, 1800, lambda now: None, call_later=fake)
        c.handle_presence_away(None, 900, lambda now: None, call_later=fake)
        assert fake.calls[0]["cancelled"]
        assert not fake.calls[1]["cancelled"]
        assert fake.calls[1]["delay"] == 900

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

    def test_second_away_event_resets_timer_while_active(self):
        c = self._ctrl()
        c.activate("comfort", 20.0)  # already away-active
        fake = FakeCallLater()
        c.handle_presence_away(None, 1800, lambda now: None, call_later=fake)
        assert fake.scheduled_count == 1
        assert c.is_active  # active flag unchanged


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
