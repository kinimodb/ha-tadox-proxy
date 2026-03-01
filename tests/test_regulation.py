"""Tests for the Feedforward + PI regulation engine.

These tests validate the pure regulation logic without requiring
Home Assistant to be installed.  We import the modules directly
to avoid triggering the HA-dependent __init__.py.
"""
import importlib
import sys
import os
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

        # Offset = 0, Error = 4°C, P = 0.8 * 4 = 3.2
        # Command = 21 + 0 + 3.2 = 24.2°C
        assert result.target_for_tado_c == 24.2
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
        # Raw = 21 + 7 + 1.6 = 29.6 → clamped to 25.0
        assert result.target_for_tado_c == 25.0
        assert result.is_saturated is True

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

        # Offset = 1°C, Error = -0.5°C, P = -0.4
        # Command = 21 + 1 - 0.4 = 21.6
        assert result.target_for_tado_c == 21.6
        assert result.error_c == -0.5


# -----------------------------------------------------------------------
# PI correction
# -----------------------------------------------------------------------

class TestPiCorrection:
    """The PI layer should slowly correct persistent errors."""

    def test_integral_accumulates_over_time(self):
        """Integral should grow when there is a persistent error."""
        reg = make_regulator()
        state = RegulationState()

        # Simulate 5 cycles of 60s each with 0.5°C persistent undershoot
        for _ in range(5):
            result = reg.compute(
                setpoint_c=21.0,
                room_temp_c=20.5,
                tado_internal_c=22.0,
                time_delta_s=60.0,
                state=state,
            )
            state = result.new_state

        # Expected integral: 5 * (0.5 * 0.003 * 60) = 0.45
        assert abs(result.i_correction_c - 0.45) < 0.01

    def test_integral_decays_on_overshoot(self):
        """Integral should decrease when room overshoots."""
        reg = make_regulator()
        # Start with some integral built up
        state = RegulationState(integral_c=1.0)

        # Room overshoots by 0.3°C
        result = reg.compute(
            setpoint_c=21.0,
            room_temp_c=21.3,
            tado_internal_c=22.0,
            time_delta_s=60.0,
            state=state,
        )

        # Error = -0.3, integral change = -0.3 * 0.003 * 60 = -0.054
        # New integral should be ~0.946
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

        # Large error for many cycles to push integral up
        for _ in range(20):
            result = reg.compute(
                setpoint_c=21.0,
                room_temp_c=20.0,
                tado_internal_c=21.0,  # small offset to avoid max saturation
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
