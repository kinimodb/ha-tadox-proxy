"""Tests for the decision helpers in climate_controllers.

Which preset the room runs is tested in test_preset_layers.py.  Imports
climate_controllers directly via importlib so that no Home Assistant
bootstrap is needed.
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

FollowPhysicalController = _ctrl_mod.FollowPhysicalController
NeedsHeat = _ctrl_mod.NeedsHeat
decide_send = _ctrl_mod.decide_send
trv_drifted_setpoint = _ctrl_mod.trv_drifted_setpoint
preset_target_c = _ctrl_mod.preset_target_c
PresetConfig = _params_mod.PresetConfig


# ---------------------------------------------------------------------------
# Follow-physical helper
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

    def test_a_step_of_exactly_the_threshold_is_sent(self):
        """19.9 -> 19.6 is 0.3 K, although the float difference is 0.2999...

        Seen in the field: the TRV kept 19.9 for 24 minutes while the
        target stood at 19.6.
        """
        result = self._decide(
            target_c=19.6, baseline_c=19.9, last_command_ts=self.NOW - 200.0
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

    # --- TRV drifted away from our command (dial, Tado schedule) ---

    def test_drift_is_corrected_although_our_target_did_not_move(self):
        """The bug: the TRV holds 22 °C, we want frost – a cycle must send.

        Comparing only against our own last command made every cycle see
        "already_at_target" and the TRV kept heating.
        """
        result = self._decide(
            target_c=7.0,
            baseline_c=7.0,
            drifted_setpoint_c=22.0,
            last_command_ts=self.NOW - 200.0,
        )
        assert result == (True, "trv_drift")

    def test_drift_correction_waits_for_the_rate_limit(self):
        """A drifted TRV must not unlock the urgent-decrease bypass.

        Otherwise a TRV that never adopts our setpoint would be sent a
        command every cycle instead of every min_interval_s (battery).
        """
        result = self._decide(target_c=7.0, baseline_c=7.0, drifted_setpoint_c=22.0)
        assert result == (False, "rate_limited(90s)")

    def test_drift_within_change_threshold_is_already_at_target(self):
        """Close enough: the dialled value needs no correction."""
        result = self._decide(
            target_c=20.0,
            baseline_c=20.0,
            drifted_setpoint_c=20.2,
            last_command_ts=self.NOW - 200.0,
        )
        assert result == (False, "already_at_target")

    def test_own_target_change_stays_a_normal_update(self):
        """Both moved: our new target is the news, not the drift."""
        result = self._decide(
            target_c=22.0, drifted_setpoint_c=18.0, last_command_ts=self.NOW - 200.0
        )
        assert result == (True, "normal_update")

    def test_urgent_decrease_is_measured_against_our_own_command(self):
        """A dial change neither triggers nor suppresses the bypass."""
        assert self._decide(target_c=18.9, drifted_setpoint_c=25.0) == (True, "urgent_decrease")
        assert self._decide(target_c=19.5, drifted_setpoint_c=25.0) == (
            False,
            "rate_limited(90s)",
        )

    def test_a_drift_of_exactly_the_threshold_is_corrected(self):
        """The TRV holds 19.9 where 19.6 belongs: 0.3 K, a correction."""
        result = self._decide(
            target_c=19.6,
            baseline_c=19.6,
            drifted_setpoint_c=19.9,
            last_command_ts=self.NOW - 200.0,
        )
        assert result == (True, "trv_drift")

    def test_drift_keeps_the_overlay_refresh_from_resending_the_same_value(self):
        """A real correction is due, so it is not a refresh of the old value."""
        result = self._decide(
            target_c=20.0,
            baseline_c=20.0,
            drifted_setpoint_c=22.0,
            overlay_refresh_s=600,
            last_command_ts=self.NOW - 600.0,
        )
        assert result == (True, "trv_drift")

    # --- result type ---

    def test_result_has_named_fields(self):
        result = self._decide(target_c=22.0, last_command_ts=0.0)
        assert result.should_send is True
        assert result.reason == "normal_update"


# ---------------------------------------------------------------------------
# trv_drifted_setpoint (the TRV left our command on its own)
# ---------------------------------------------------------------------------

class TestTrvDriftedSetpoint:
    """Dial change vs. the echo of our own command.

    Defaults match BehaviourConfig: 0.5 °C drift threshold, 20 s grace.
    """

    NOW = 1_000_000.0

    def _drifted(self, **overrides):
        args = {
            "last_sent_c": 21.0,
            "trv_setpoint_c": 21.0,
            "last_command_ts": self.NOW - 100.0,
            "now": self.NOW,
            "drift_threshold_c": 0.5,
            "grace_s": 20.0,
        }
        args.update(overrides)
        return trv_drifted_setpoint(**args)

    def test_trv_on_our_setpoint_has_not_drifted(self):
        assert self._drifted() is None

    def test_difference_at_the_threshold_is_not_drift(self):
        """Rounding on the TRV's side must not count as a dial change."""
        assert self._drifted(trv_setpoint_c=21.5) is None

    def test_difference_beyond_the_threshold_is_the_drifted_setpoint(self):
        """Someone turned the dial: that is what the TRV holds now."""
        assert self._drifted(trv_setpoint_c=22.0) == 22.0

    def test_drift_downwards_is_reported_too(self):
        assert self._drifted(trv_setpoint_c=18.0) == 18.0

    def test_inside_the_grace_period_it_is_our_own_echo(self):
        """Tado acknowledging our command must not look like a dial change."""
        assert self._drifted(trv_setpoint_c=22.0, last_command_ts=self.NOW - 10.0) is None

    def test_without_an_own_command_nothing_can_drift(self):
        """The TRV's value is already the baseline then."""
        assert self._drifted(last_sent_c=None, trv_setpoint_c=20.0) is None

    def test_without_a_report_nothing_can_drift(self):
        """TRV off or unavailable: our own command is all we know."""
        assert self._drifted(trv_setpoint_c=None) is None

    def test_drift_detection_matches_follow_tado(self):
        """One definition of "the TRV moved on its own" for both features."""
        for trv_setpoint in (20.4, 20.5, 21.5, 22.0):
            follows = FollowPhysicalController.should_follow(
                tado_setpoint=trv_setpoint,
                last_sent=21.0,
                last_sent_ts=self.NOW - 100.0,
                threshold_c=0.5,
                grace_s=20.0,
                now=self.NOW,
            )
            drifted = self._drifted(trv_setpoint_c=trv_setpoint) is not None
            assert drifted is follows, f"{trv_setpoint}: follow={follows} drift={drifted}"


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



# ---------------------------------------------------------------------------
# NeedsHeat (hysteresis on error_c, for a boiler that runs on demand)
# ---------------------------------------------------------------------------

class TestNeedsHeat:
    """On above the switch-on gap, off at the switch-off gap, unchanged between.

    Thresholds match the option defaults: on above 0.3 °C, off at 0.0 °C.
    """

    def _needs_heat(self, *errors, on_above_c=0.3, off_at_c=0.0):
        """Feed *errors* in order, heating on and sensor fine; return the decider."""
        needs_heat = NeedsHeat(on_above_c=on_above_c, off_at_c=off_at_c)
        for error_c in errors:
            needs_heat.update(error_c, heating=True, degraded=False)
        return needs_heat

    def test_off_before_the_first_cycle(self):
        assert self._needs_heat().is_on is False

    def test_on_once_the_gap_rises_above_the_switch_on_value(self):
        assert self._needs_heat(0.31).is_on is True

    def test_a_gap_of_exactly_the_switch_on_value_is_not_above_it(self):
        """Target 20.3 °C, room 20.0 °C: 0.3 K, although the float is 0.3000...07."""
        assert self._needs_heat(20.3 - 20.0).is_on is False

    def test_keeps_running_between_the_two_values(self):
        """No stop for a room that is still 0.2 K short: it would never get there."""
        assert self._needs_heat(0.5, 0.2).is_on is True

    def test_stays_off_between_the_two_values(self):
        """No start for a small gap."""
        assert self._needs_heat(0.2).is_on is False

    def test_off_once_the_room_has_reached_its_target(self):
        assert self._needs_heat(0.5, 0.0).is_on is False

    def test_off_once_the_room_is_above_its_target(self):
        assert self._needs_heat(0.5, -0.2).is_on is False

    def test_a_gap_of_exactly_the_switch_off_value_switches_off(self):
        """Target 20.1 °C, room 20.0 °C: 0.1 K, although the float is 0.1000...01."""
        assert self._needs_heat(0.5, 20.1 - 20.0, off_at_c=0.1).is_on is False

    def test_off_while_the_heating_is_off(self):
        needs_heat = self._needs_heat(0.5)

        assert needs_heat.update(0.5, heating=False, degraded=False) is False
        assert needs_heat.is_on is False

    def test_off_while_the_room_sensor_is_degraded(self):
        """A stale room reading must not keep the boiler running."""
        needs_heat = self._needs_heat(0.5)

        assert needs_heat.update(0.5, heating=True, degraded=True) is False

    def test_off_without_a_regulation_value(self):
        """No error_c: waiting for sensors, the TRV in charge, the window's valve off."""
        needs_heat = self._needs_heat(0.5)

        assert needs_heat.update(None, heating=True, degraded=False) is False

    def test_starts_from_off_once_the_heating_is_back(self):
        """After a pause the small gap of 0.2 K does not start the boiler."""
        needs_heat = self._needs_heat(0.5)
        needs_heat.update(None, heating=False, degraded=False)

        assert needs_heat.update(0.2, heating=True, degraded=False) is False

    def test_changed_values_apply_to_the_next_decision(self):
        """The options can change while the room runs."""
        needs_heat = self._needs_heat(0.2)

        needs_heat.on_above_c = 0.1
        assert needs_heat.update(0.2, heating=True, degraded=False) is True
        needs_heat.on_above_c, needs_heat.off_at_c = 0.5, 0.2
        assert needs_heat.update(0.2, heating=True, degraded=False) is False
