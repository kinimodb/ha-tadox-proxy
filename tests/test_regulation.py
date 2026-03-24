"""Tests for the Feedforward + PI regulation engine.

These tests validate the pure regulation logic without requiring
Home Assistant to be installed.  We import the modules directly
to avoid triggering the HA-dependent __init__.py.
"""
import importlib
import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Import regulation.py and parameters.py WITHOUT triggering __init__.py
# (which depends on homeassistant)
# ---------------------------------------------------------------------------

_COMP_DIR = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "tadox_proxy"
)

def _load_module(name: str, path: str) -> types.ModuleType:
    """Load a single Python module by file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# Load parameters first (no HA dependency), then regulation (depends on parameters)
_params = _load_module(
    "tadox_proxy.parameters",
    os.path.join(_COMP_DIR, "parameters.py"),
)
_reg = _load_module(
    "tadox_proxy.regulation",
    os.path.join(_COMP_DIR, "regulation.py"),
)

RegulationConfig = _params.RegulationConfig
CorrectionTuning = _params.CorrectionTuning
PresetConfig = _params.PresetConfig
FeedforwardPiRegulator = _reg.FeedforwardPiRegulator
RegulationState = _reg.RegulationState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_regulator(**overrides) -> FeedforwardPiRegulator:
    """Create a regulator with default config, optionally overridden."""
    config = RegulationConfig(**overrides)
    return FeedforwardPiRegulator(config)


# -----------------------------------------------------------------------
# Feedforward basics
# -----------------------------------------------------------------------

class TestFeedforward:
    """The feedforward term should compensate for the sensor offset."""

    def test_offset_compensation_in_steady_state(self):
        """At target temp, command should equal tado_internal (offset only)."""
        reg = make_regulator()
        state = RegulationState()

        # Room at target, Tado sensor reads 2°C higher (radiator heat)
        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=21.0,
            tado_internal_c=23.0,
            time_delta_s=0.0,
            state=state,
        )

        # Feedforward offset = 23 - 21 = 2°C
        assert result.feedforward_offset_c == 2.0
        # Error = 0, so P correction = 0
        assert result.p_correction_c == 0.0
        # Command = 21 + 2 + 0 + 0 = 23°C
        assert result.target_for_tado_c == 23.0

    def test_cold_start_gives_high_target(self):
        """When room is cold, command should be high to trigger heating."""
        reg = make_regulator()
        state = RegulationState()

        # Cold room, cold radiator
        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=17.0,
            tado_internal_c=17.0,
            time_delta_s=0.0,
            state=state,
        )

        # Offset = 0, Error = 4°C, |error| > 2.0 → Kp = 0.8 * 1.5 = 1.2
        # P = 1.2 * 4 = 4.8, Command = 21 + 0 + 4.8 = 25.8°C
        assert result.target_for_tado_c == 25.8
        assert result.error_c == 4.0

    def test_heating_phase_with_hot_radiator(self):
        """During heating the offset grows, pushing command toward max."""
        reg = make_regulator()
        state = RegulationState()

        # Room warming up, radiator is hot
        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=19.0,
            tado_internal_c=26.0,
            time_delta_s=0.0,
            state=state,
        )

        # Offset = 7°C, Error = 2°C, P = 1.6
        # Raw = 21 + 7 + 1.6 = 29.6 → clamped to 30.0 (max_target_c)
        assert result.target_for_tado_c == 29.6
        assert result.is_saturated is False

    def test_overshoot_reduces_command(self):
        """When room exceeds setpoint, command should drop below tado_internal."""
        reg = make_regulator()
        state = RegulationState()

        # Slight overshoot
        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=21.5,
            tado_internal_c=22.5,
            time_delta_s=0.0,
            state=state,
        )

        # Offset = 1°C, Error = -0.5°C, |error| < 0.5 → Kp = 0.8 * 0.7 = 0.56
        # P = 0.56 * -0.5 = -0.28, Command = 21 + 1 - 0.28 = 21.72 → 21.7
        assert result.target_for_tado_c == 21.7
        assert result.error_c == -0.5


# -----------------------------------------------------------------------
# PI correction
# -----------------------------------------------------------------------

class TestPiCorrection:
    """The PI layer should slowly correct persistent errors."""

    def test_integral_accumulates_near_target(self):
        """Integral should grow when error is within deadband (near target)."""
        reg = make_regulator()
        state = RegulationState()

        # Simulate 5 cycles of 60s each with 0.2°C persistent undershoot
        # (within the 0.3°C integral deadband)
        for _ in range(5):
            result = reg.compute(
                setpoint_c=21.0,
                room_temp_c=20.8,
                tado_internal_c=22.0,
                time_delta_s=60.0,
                state=state,
            )
            state = result.new_state

        # Expected integral: 5 * (0.2 * 0.003 * 60) = 0.18
        assert abs(result.i_correction_c - 0.18) < 0.01

    def test_integral_does_not_accumulate_far_from_target(self):
        """Integral should decay (not grow) when error is outside deadband."""
        reg = make_regulator()
        state = RegulationState()

        # Simulate 10 cycles with 1.0°C error (far outside 0.3°C deadband)
        for _ in range(10):
            result = reg.compute(
                setpoint_c=21.0,
                room_temp_c=20.0,
                tado_internal_c=21.0,
                time_delta_s=60.0,
                state=state,
            )
            state = result.new_state

        # Integral should be near 0 (decayed, not accumulated)
        assert abs(result.i_correction_c) < 0.01

    def test_integral_decays_on_overshoot(self):
        """Integral should decrease when room overshoots."""
        reg = make_regulator()
        # Start with some integral built up
        state = RegulationState(integral_c=1.0)

        # Room overshoots by 0.2°C (within deadband → normal integration)
        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=21.2,
            tado_internal_c=22.0,
            time_delta_s=60.0,
            state=state,
        )

        # Error = -0.2 (within deadband), integral decreases via normal accumulation
        # integral change = -0.2 * 0.003 * 60 = -0.036
        # New integral should be ~0.964
        assert result.i_correction_c < 1.0
        assert result.i_correction_c > 0.9

    def test_first_cycle_no_integration(self):
        """On the very first cycle (dt=0), integral should not change."""
        reg = make_regulator()
        state = RegulationState()

        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=19.0,
            tado_internal_c=20.0,
            time_delta_s=0.0,
            state=state,
        )

        assert result.i_correction_c == 0.0


# -----------------------------------------------------------------------
# Anti-windup
# -----------------------------------------------------------------------

class TestAntiWindup:
    """Anti-windup should prevent integral buildup during saturation."""

    def test_integral_freezes_at_max_saturation(self):
        """When output is clamped at max and error > 0, integral must not grow."""
        reg = make_regulator()
        state = RegulationState()

        # Force saturation: huge offset + positive error
        for _ in range(10):
            result = reg.compute(
                setpoint_c=21.0,
                room_temp_c=17.0,
                tado_internal_c=26.0,
                time_delta_s=60.0,
                state=state,
            )
            state = result.new_state

        # Output is saturated at 25°C, integral should stay at 0
        assert result.is_saturated is True
        assert result.i_correction_c == 0.0

    def test_integral_allowed_to_decrease_during_high_saturation(self):
        """Even when saturated high, negative error should unwind integral."""
        reg = make_regulator()
        state = RegulationState(integral_c=1.5)

        # Saturated high, but room overshoots → error < 0
        # This happens if offset is very large
        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=21.5,
            tado_internal_c=27.0,     # huge offset → raw will exceed max
            time_delta_s=60.0,
            state=state,
        )

        # error = -0.5, saturated_high = True, but error < 0 → may_integrate = True
        # Integral should decrease
        assert result.new_state.integral_c < 1.5

    def test_integral_clamped_at_limits(self):
        """Integral should never exceed configured bounds."""
        config = RegulationConfig(integral_max_c=1.0, integral_min_c=-1.0)
        reg = FeedforwardPiRegulator(config)
        state = RegulationState(integral_c=0.95)

        # Small error within deadband for many cycles to push integral up
        for _ in range(20):
            result = reg.compute(
                setpoint_c=21.0,
                room_temp_c=20.8,       # 0.2°C error within deadband
                tado_internal_c=21.5,   # small offset to avoid max saturation
                time_delta_s=60.0,
                state=state,
            )
            state = result.new_state

        assert result.i_correction_c <= 1.0


# -----------------------------------------------------------------------
# Safety clamping
# -----------------------------------------------------------------------

class TestSafetyClamping:
    """Output should always be clamped to safe bounds."""

    def test_max_target_clamping(self):
        """Command should not exceed max_target_c."""
        reg = make_regulator(max_target_c=25.0)
        state = RegulationState()

        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=15.0,
            tado_internal_c=25.0,
            time_delta_s=0.0,
            state=state,
        )

        assert result.target_for_tado_c <= 25.0

    def test_min_target_clamping(self):
        """Command should not go below min_target_c."""
        reg = make_regulator(min_target_c=5.0)
        state = RegulationState()

        # Room way above setpoint → negative command
        result = reg.compute(
            setpoint_c=5.0,
            room_temp_c=25.0,
            tado_internal_c=5.0,
            time_delta_s=0.0,
            state=state,
        )

        assert result.target_for_tado_c >= 5.0


# -----------------------------------------------------------------------
# Full scenario: cold start to steady state
# -----------------------------------------------------------------------

class TestFullScenario:
    """Simulate a realistic heating cycle and verify stable behaviour."""

    def test_cold_start_to_steady_state(self):
        """Command should start high and converge as room reaches target."""
        reg = make_regulator()
        state = RegulationState()

        room = 17.0
        tado_internal = 17.0
        commands = []

        # Simulate 90 minutes (90 cycles of 60s)
        for _ in range(90):
            result = reg.compute(
                setpoint_c=21.0,
                room_temp_c=round(room, 2),
                tado_internal_c=round(tado_internal, 2),
                time_delta_s=60.0,
                state=state,
            )
            state = result.new_state
            commands.append(result.target_for_tado_c)

            # Simple thermal model:
            # Tado_internal responds to the command (radiator heats)
            heating_power = max(0, result.target_for_tado_c - tado_internal) * 0.15
            tado_internal += heating_power - 0.08  # radiator heat loss
            # Room follows radiator with thermal lag
            room += (tado_internal - room) * 0.05 - 0.02  # room heat loss

        # Room should be close to target after 90 min
        assert room > 20.0, f"Room should be warm by now, got {room:.1f}"

        # Final commands should have settled (not stuck at max)
        assert commands[-1] < 25.0, f"Command should have settled, got {commands[-1]}"

    def test_integral_does_not_cause_overshoot(self):
        """Reproduce the real-world overshoot: integral must not build up
        during heating, so the command drops quickly at target."""
        reg = make_regulator()
        state = RegulationState()

        # Simulate heating from 16.3°C toward 17.5°C (like the test room)
        room = 16.3
        tado_internal = 16.4
        target = 17.5

        for _ in range(25):  # ~25 minutes of heating
            result = reg.compute(
                setpoint_c=target,
                room_temp_c=round(room, 2),
                tado_internal_c=round(tado_internal, 2),
                time_delta_s=60.0,
                state=state,
            )
            state = result.new_state

            # Simple thermal model
            heating_power = max(0, result.target_for_tado_c - tado_internal) * 0.08
            tado_internal += heating_power - 0.03
            room += (tado_internal - room) * 0.04 - 0.005

        # Room should be near target now
        assert room > 17.0

        # Key check: integral should be near 0 because error was > deadband
        # during most of the heating phase
        assert abs(result.i_correction_c) < 0.3, (
            f"Integral should be small after heating, got {result.i_correction_c}"
        )

        # The command should be close to tado_internal (not 2-3°C above)
        # because feedforward handles the offset and integral is near 0
        overshoot_margin = result.target_for_tado_c - tado_internal
        assert overshoot_margin < 2.0, (
            f"Command should not be far above tado_internal, margin={overshoot_margin}"
        )

    def test_no_overshoot_on_setpoint_change(self):
        """Lowering setpoint should immediately lower the command."""
        reg = make_regulator()
        state = RegulationState()

        # Start at steady state: room at 21°C
        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=21.0,
            tado_internal_c=22.5,
            time_delta_s=60.0,
            state=state,
        )
        state = result.new_state
        high_command = result.target_for_tado_c

        # User lowers setpoint to 19°C
        result = reg.compute(
            setpoint_c=19.0,
            room_temp_c=21.0,
            tado_internal_c=22.5,
            time_delta_s=60.0,
            state=state,
        )

        # Command should drop significantly
        assert result.target_for_tado_c < high_command
        # Error is now negative (room too warm for new setpoint)
        assert result.error_c < 0


# -----------------------------------------------------------------------
# Preset configuration
# -----------------------------------------------------------------------

class TestPresetConfig:
    """Verify PresetConfig defaults and integration with RegulationConfig."""

    def test_default_preset_values(self):
        """Default presets should have sensible values."""
        presets = PresetConfig()
        assert presets.eco_target_c == 17.0
        assert presets.boost_target_c == 25.0
        assert presets.boost_duration_min == 30
        assert presets.away_target_c == 17.0
        assert presets.frost_protection_target_c == 7.0

    def test_preset_config_in_regulation_config(self):
        """RegulationConfig should carry PresetConfig defaults."""
        config = RegulationConfig()
        assert config.presets.eco_target_c == 17.0
        assert config.presets.boost_target_c == 25.0

    def test_custom_preset_values(self):
        """Custom preset values should override defaults."""
        presets = PresetConfig(eco_target_c=17.0, away_target_c=14.0)
        config = RegulationConfig(presets=presets)
        assert config.presets.eco_target_c == 17.0
        assert config.presets.away_target_c == 14.0
        # Others stay default
        assert config.presets.boost_target_c == 25.0

    def test_eco_setpoint_is_fixed(self):
        """Eco mode uses a fixed temperature independent of comfort target."""
        presets = PresetConfig(eco_target_c=19.0)
        assert presets.eco_target_c == 19.0

    def test_regulation_with_eco_setpoint(self):
        """Regulation engine should produce lower command for eco setpoint."""
        reg = make_regulator()
        state = RegulationState()

        # Comfort: setpoint 21°C
        result_comfort = reg.compute(
            setpoint_c=21.0,
            room_temp_c=20.0,
            tado_internal_c=22.0,
            time_delta_s=0.0,
            state=state,
        )

        # Eco: fixed setpoint 19°C (independent of comfort)
        result_eco = reg.compute(
            setpoint_c=19.0,
            room_temp_c=20.0,
            tado_internal_c=22.0,
            time_delta_s=0.0,
            state=state,
        )

        # Eco command should be lower than comfort
        assert result_eco.target_for_tado_c < result_comfort.target_for_tado_c
        # In eco, room is already above target → negative error
        assert result_eco.error_c < 0

    def test_regulation_with_boost_setpoint(self):
        """Boost uses max temperature → highest possible command."""
        reg = make_regulator()
        state = RegulationState()

        # Boost: setpoint 25°C
        result = reg.compute(
            setpoint_c=25.0,
            room_temp_c=18.0,
            tado_internal_c=20.0,
            time_delta_s=0.0,
            state=state,
        )

        # Raw = 25 + 2 + 5.6 = 32.6 → clamped to 30.0 (max_target_c)
        assert result.target_for_tado_c == 30.0
        assert result.is_saturated is True

    def test_regulation_with_frost_protection_setpoint(self):
        """Frost protection preset → minimal heating."""
        reg = make_regulator()
        state = RegulationState()

        # Vacation: setpoint 5°C, room at 18°C → way above target
        result = reg.compute(
            setpoint_c=5.0,
            room_temp_c=18.0,
            tado_internal_c=20.0,
            time_delta_s=0.0,
            state=state,
        )

        # Command should be at minimum (frost protection)
        assert result.target_for_tado_c == 5.0
        assert result.error_c == -13.0


# ---------------------------------------------------------------------------
# Edge-case tests added during v0.10.1 audit
# ---------------------------------------------------------------------------


class TestNegativeFeedforward:
    """Tado's sensor reads *lower* than the room sensor.

    This is unusual (TRV near a cold window / draught) but valid.  The
    feedforward offset becomes negative, which should lower the command
    sent to Tado rather than raise it.
    """

    def test_negative_offset_lowers_command(self):
        """Negative offset → command below setpoint."""
        reg = make_regulator()
        state = RegulationState()

        # tado_internal (17°C) < room_temp (20°C) → offset = -3°C
        result = reg.compute(
            setpoint_c=20.0,
            room_temp_c=20.0,
            tado_internal_c=17.0,
            time_delta_s=0.0,
            state=state,
        )

        assert result.feedforward_offset_c == -3.0
        # Error = 0, P = 0, I = 0 → command = 20 + (-3) = 17°C
        assert result.target_for_tado_c == 17.0

    def test_negative_offset_with_room_error(self):
        """Negative offset combined with a positive room error."""
        reg = make_regulator()
        state = RegulationState()

        # Room is 1°C below setpoint, Tado sensor is 2°C below room
        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=20.0,
            tado_internal_c=18.0,
            time_delta_s=0.0,
            state=state,
        )

        # offset = 18 - 20 = -2.0; error = 21 - 20 = 1.0
        # |error|=1.0 in interpolation zone: t=(1.0-0.5)/(2.0-0.5)=1/3
        # multiplier = 0.7 + 1/3 * 0.3 = 0.8; Kp = 0.8 * 0.8 = 0.64
        # P = 0.64; command = 21 + (-2) + 0.64 = 19.64°C
        assert result.feedforward_offset_c == -2.0
        assert result.p_correction_c == pytest.approx(0.64, abs=0.01)
        assert result.target_for_tado_c == pytest.approx(19.6, abs=0.05)

    def test_negative_offset_clamped_at_minimum(self):
        """A very large negative offset should be clamped at min_target_c."""
        reg = make_regulator(min_target_c=5.0)
        state = RegulationState()

        # Extreme: Tado sensor 20°C below room temp
        result = reg.compute(
            setpoint_c=20.0,
            room_temp_c=20.0,
            tado_internal_c=0.0,
            time_delta_s=0.0,
            state=state,
        )

        # raw = 20 + (-20) = 0°C → clamped to 5°C
        assert result.target_for_tado_c == 5.0
        assert result.is_saturated is True


class TestTimeDeltaEdgeCases:
    """Validate integral behaviour at extreme time-delta values."""

    def test_zero_time_delta_does_not_change_integral(self):
        """time_delta_s=0 must never update the integral (first cycle guard)."""
        reg = make_regulator()
        initial_integral = 1.5
        state = RegulationState(integral_c=initial_integral)

        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=20.0,  # error = 1.0, within deadband
            tado_internal_c=22.0,
            time_delta_s=0.0,
            state=state,
        )

        # Integral must be unchanged on first cycle
        assert result.new_state.integral_c == pytest.approx(initial_integral)

    def test_large_time_delta_integral_stays_bounded(self):
        """A very long gap (600s) must not push integral past its clamps."""
        reg = make_regulator(integral_max_c=2.0, integral_min_c=-2.0)
        state = RegulationState(integral_c=0.0)

        # Run 100 consecutive "long-gap" cycles near target so integral
        # can accumulate freely (within deadband, not saturated).
        for _ in range(100):
            result = reg.compute(
                setpoint_c=21.0,
                room_temp_c=20.8,   # error = 0.2 < deadband (0.3)
                tado_internal_c=22.0,
                time_delta_s=600.0,  # 10-minute gap
                state=state,
            )
            state = result.new_state

        # Integral must stay within the configured clamps
        assert state.integral_c <= 2.0
        assert state.integral_c >= -2.0

    def test_decay_applied_outside_deadband(self):
        """Integral must shrink each cycle when |error| exceeds deadband."""
        reg = make_regulator(integral_decay=0.9, integral_deadband_c=0.3)
        initial_integral = 1.0
        state = RegulationState(integral_c=initial_integral)

        # error = 1.0°C, well outside deadband
        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=20.0,
            tado_internal_c=22.0,
            time_delta_s=60.0,
            state=state,
        )

        # Integral must have decayed (multiplied by 0.9)
        assert result.new_state.integral_c == pytest.approx(
            initial_integral * 0.9, abs=0.001
        )


# ---------------------------------------------------------------------------
# NaN / Inf guard tests (added during Phase 2 audit)
# ---------------------------------------------------------------------------


class TestNanInfGuard:
    """Regulation must reject NaN/Inf inputs without corrupting state."""

    def test_nan_setpoint_returns_safe_fallback(self):
        """NaN setpoint → regulation aborted, state preserved."""
        reg = make_regulator()
        state = RegulationState(integral_c=0.5)

        result = reg.compute(
            setpoint_c=float("nan"),
            room_temp_c=20.0,
            tado_internal_c=22.0,
            time_delta_s=60.0,
            state=state,
        )

        # Should return a safe result, not NaN
        assert result.target_for_tado_c == 5.0  # min_target_c fallback
        # State must be preserved (not corrupted)
        assert result.new_state.integral_c == 0.5

    def test_nan_room_temp_returns_safe_fallback(self):
        """NaN room_temp → regulation aborted."""
        reg = make_regulator()
        state = RegulationState()

        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=float("nan"),
            tado_internal_c=22.0,
            time_delta_s=60.0,
            state=state,
        )

        # setpoint is valid, so use it as fallback
        assert result.target_for_tado_c == 21.0
        assert result.error_c == 0.0

    def test_inf_tado_internal_returns_safe_fallback(self):
        """Inf tado_internal → regulation aborted."""
        reg = make_regulator()
        state = RegulationState()

        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=20.0,
            tado_internal_c=float("inf"),
            time_delta_s=60.0,
            state=state,
        )

        assert result.target_for_tado_c == 21.0

    def test_negative_inf_rejected(self):
        """Negative infinity must also be rejected."""
        reg = make_regulator()
        state = RegulationState()

        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=float("-inf"),
            tado_internal_c=22.0,
            time_delta_s=60.0,
            state=state,
        )

        assert result.target_for_tado_c == 21.0

    def test_nan_fallback_clamped_to_bounds(self):
        """When sensor is NaN and setpoint exceeds max_target_c, fallback must be clamped."""
        reg = make_regulator()  # max_target_c = 30.0
        state = RegulationState()

        result = reg.compute(
            setpoint_c=32.0,
            room_temp_c=float("nan"),
            tado_internal_c=22.0,
            time_delta_s=60.0,
            state=state,
        )

        # 32.0 exceeds max_target_c (30.0), must be clamped
        assert result.target_for_tado_c == 30.0

    def test_valid_inputs_still_work_normally(self):
        """Normal inputs must not be affected by the NaN guard."""
        reg = make_regulator()
        state = RegulationState()

        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=20.0,
            tado_internal_c=22.0,
            time_delta_s=60.0,
            state=state,
        )

        # Normal computation: offset=2, error=1
        # |error|=1.0 → interpolation: multiplier=0.8, Kp=0.64, P=0.64
        # command = 21 + 2 + 0.64 = 23.64 → 23.6
        assert result.target_for_tado_c == 23.6
        assert result.error_c == 1.0


# ---------------------------------------------------------------------------
# Adaptive gain scheduling
# ---------------------------------------------------------------------------


class TestAdaptiveGainScheduling:
    """Verify that Kp is scaled based on error magnitude."""

    def test_large_error_uses_startup_multiplier(self):
        """Error > startup_threshold → Kp × startup_multiplier (1.5)."""
        config = RegulationConfig()
        kp = FeedforwardPiRegulator._effective_kp(5.0, config)
        assert kp == pytest.approx(config.tuning.kp * 1.5, abs=0.001)

    def test_small_error_uses_fine_multiplier(self):
        """Error < fine_threshold → Kp × fine_multiplier (0.7)."""
        config = RegulationConfig()
        kp = FeedforwardPiRegulator._effective_kp(0.2, config)
        assert kp == pytest.approx(config.tuning.kp * 0.7, abs=0.001)

    def test_mid_error_linear_interpolation(self):
        """Error between fine and startup thresholds → linear interpolation."""
        config = RegulationConfig()
        # |error| = 1.0, fine=0.5, startup=2.0
        # t = (1.0 - 0.5) / (2.0 - 0.5) = 0.5/1.5 = 1/3
        # multiplier = 0.7 + 1/3 * (1.0 - 0.7) = 0.7 + 0.1 = 0.8
        expected_kp = config.tuning.kp * 0.8
        kp = FeedforwardPiRegulator._effective_kp(1.0, config)
        assert kp == pytest.approx(expected_kp, abs=0.001)

    def test_scheduling_disabled_returns_base_kp(self):
        """When gain_scheduling_enabled=False → Kp unchanged."""
        config = RegulationConfig(gain_scheduling_enabled=False)
        kp = FeedforwardPiRegulator._effective_kp(5.0, config)
        assert kp == pytest.approx(config.tuning.kp, abs=0.001)

    def test_cold_start_adaptive_faster_than_static(self):
        """Cold start (15→20°C): adaptive scheduling produces higher initial command."""
        config_adaptive = RegulationConfig(gain_scheduling_enabled=True)
        config_static = RegulationConfig(gain_scheduling_enabled=False)
        reg_adaptive = FeedforwardPiRegulator(config_adaptive)
        reg_static = FeedforwardPiRegulator(config_static)
        state = RegulationState()

        # Cold start: room 15°C, target 20°C, error=5°C → startup zone
        result_adaptive = reg_adaptive.compute(
            setpoint_c=20.0, room_temp_c=15.0,
            tado_internal_c=15.0, time_delta_s=0.0, state=state,
        )
        result_static = reg_static.compute(
            setpoint_c=20.0, room_temp_c=15.0,
            tado_internal_c=15.0, time_delta_s=0.0, state=state,
        )

        # Adaptive should command higher (more aggressive P-term)
        assert result_adaptive.target_for_tado_c > result_static.target_for_tado_c
        # P-correction should be 1.5× larger
        assert result_adaptive.p_correction_c == pytest.approx(
            result_static.p_correction_c * 1.5, abs=0.1
        )

    def test_steady_state_adaptive_gentler_than_static(self):
        """Near target (19.8→20°C): adaptive scheduling produces lower P-correction."""
        config_adaptive = RegulationConfig(gain_scheduling_enabled=True)
        config_static = RegulationConfig(gain_scheduling_enabled=False)
        reg_adaptive = FeedforwardPiRegulator(config_adaptive)
        reg_static = FeedforwardPiRegulator(config_static)
        state = RegulationState()

        # Near target: room 19.8°C, target 20°C, error=0.2°C → fine zone
        result_adaptive = reg_adaptive.compute(
            setpoint_c=20.0, room_temp_c=19.8,
            tado_internal_c=21.0, time_delta_s=0.0, state=state,
        )
        result_static = reg_static.compute(
            setpoint_c=20.0, room_temp_c=19.8,
            tado_internal_c=21.0, time_delta_s=0.0, state=state,
        )

        # Adaptive should have smaller P-correction (0.7×)
        assert abs(result_adaptive.p_correction_c) < abs(result_static.p_correction_c)
        assert result_adaptive.p_correction_c == pytest.approx(
            result_static.p_correction_c * 0.7, abs=0.05
        )

    def test_negative_error_uses_abs_value(self):
        """Negative error (overshoot) should use |error| for threshold comparison."""
        config = RegulationConfig()
        # error = -3.0, |error| = 3.0 > startup_threshold → startup multiplier
        kp = FeedforwardPiRegulator._effective_kp(-3.0, config)
        assert kp == pytest.approx(config.tuning.kp * 1.5, abs=0.001)

    def test_exact_threshold_boundaries(self):
        """Error exactly at thresholds should be handled correctly."""
        config = RegulationConfig()
        # At fine_threshold (0.5) → fine zone (< check is strict)
        kp_at_fine = FeedforwardPiRegulator._effective_kp(0.5, config)
        # 0.5 is NOT < 0.5, so it falls into interpolation zone
        # t = (0.5 - 0.5) / (2.0 - 0.5) = 0 → multiplier = 0.7
        assert kp_at_fine == pytest.approx(config.tuning.kp * 0.7, abs=0.001)

        # At startup_threshold (2.0) → NOT > 2.0, so interpolation zone
        # t = (2.0 - 0.5) / (2.0 - 0.5) = 1.0 → multiplier = 0.7 + 1.0*(1.0-0.7) = 1.0
        kp_at_startup = FeedforwardPiRegulator._effective_kp(2.0, config)
        assert kp_at_startup == pytest.approx(config.tuning.kp * 1.0, abs=0.001)


# ---------------------------------------------------------------------------
# Temperature range limits
# ---------------------------------------------------------------------------

class TestTemperatureRangeLimits:
    """Verify that RegulationConfig defaults match Tado X TRV limits (5–30°C)."""

    def test_default_min_target(self):
        cfg = RegulationConfig()
        assert cfg.min_target_c == 5.0

    def test_default_max_target(self):
        cfg = RegulationConfig()
        assert cfg.max_target_c == 30.0

    def test_regulation_clamps_to_min(self):
        """Computed target below min_target_c is clamped to 5.0."""
        cfg = RegulationConfig()
        reg = FeedforwardPiRegulator(cfg)
        state = RegulationState()
        result = reg.compute(
            setpoint_c=5.0,
            room_temp_c=10.0,   # room much warmer → negative correction
            tado_internal_c=10.0,
            time_delta_s=60.0,
            state=state,
        )
        assert result.target_for_tado_c >= cfg.min_target_c

    def test_regulation_clamps_to_max(self):
        """Computed target above max_target_c is clamped to 30.0."""
        cfg = RegulationConfig()
        reg = FeedforwardPiRegulator(cfg)
        state = RegulationState()
        result = reg.compute(
            setpoint_c=30.0,
            room_temp_c=15.0,   # room much colder → large positive correction
            tado_internal_c=20.0,
            time_delta_s=60.0,
            state=state,
        )
        assert result.target_for_tado_c <= cfg.max_target_c
