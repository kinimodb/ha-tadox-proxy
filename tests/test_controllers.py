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
decide_send = _ctrl_mod.decide_send
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

