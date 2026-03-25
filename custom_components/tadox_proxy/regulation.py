"""
Feedforward + PI Regulation for Tado X Proxy.

Strategy
--------
Tado X thermostats have their own internal controller.  Instead of building
a second full PID that fights against it, we measure the *offset* between
Tado's built-in sensor (sitting on the hot radiator) and an external room
sensor, then use that offset as a **feedforward** term.  A small PI
correction handles any remaining steady-state error.

    command_to_tado = room_setpoint
                    + sensor_offset          (feedforward)
                    + kp * error             (proportional correction)
                    + integral               (integral correction)

Anti-windup (two mechanisms):
1. The integral freezes whenever the output is saturated (clamped at max or
   min target) in the same direction as the error.
2. The integral only accumulates when |error| < deadband (near target).
   Outside this zone the integral *decays* toward zero, preventing buildup
   during gross heating transients that would cause overshoot.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .parameters import RegulationConfig

_LOGGER = logging.getLogger(__name__)

# Output is considered "not saturated" when clamping error is below this threshold (°C).
# 0.01 °C is 1/10 of the Tado quantisation step (0.1 °C) – effectively zero.
_SATURATION_TOLERANCE_C = 0.01


# ---------------------------------------------------------------------------
# State & result data classes
# ---------------------------------------------------------------------------

@dataclass
class RegulationState:
    """Mutable state carried between regulation cycles."""

    integral_c: float = 0.0


@dataclass
class RegulationResult:
    """Output of a single regulation cycle."""

    target_for_tado_c: float       # absolute temperature to send to Tado
    feedforward_offset_c: float    # measured sensor offset  (diagnostic)
    p_correction_c: float          # proportional correction (diagnostic)
    i_correction_c: float          # integral correction     (diagnostic)
    error_c: float                 # room error: setpoint - room_temp
    is_saturated: bool             # True when output was clamped
    new_state: RegulationState


# ---------------------------------------------------------------------------
# Regulator
# ---------------------------------------------------------------------------

class FeedforwardPiRegulator:
    """Feedforward + PI regulator for Tado X proxy thermostats."""

    def __init__(self, config: RegulationConfig) -> None:
        self.cfg = config

    @staticmethod
    def _effective_kp(error_c: float, config: RegulationConfig) -> float:
        """Return the effective Kp, scaled by adaptive gain scheduling.

        When gain scheduling is enabled the proportional gain is amplified
        for large errors (cold-start) and attenuated near the setpoint
        (fine control).  In the transition zone a linear interpolation
        between the two multipliers is used.
        """
        if not config.gain_scheduling_enabled:
            return config.tuning.kp

        abs_error = abs(error_c)
        if abs_error > config.gain_startup_threshold_c:
            multiplier = config.gain_startup_multiplier
        elif abs_error < config.gain_fine_threshold_c:
            multiplier = config.gain_fine_multiplier
        else:
            # Linear interpolation between fine and 1.0 (base)
            span = config.gain_startup_threshold_c - config.gain_fine_threshold_c
            t = (abs_error - config.gain_fine_threshold_c) / span
            multiplier = config.gain_fine_multiplier + t * (1.0 - config.gain_fine_multiplier)

        return config.tuning.kp * multiplier

    def compute(
        self,
        setpoint_c: float,
        room_temp_c: float,
        tado_internal_c: float,
        time_delta_s: float,
        state: RegulationState,
    ) -> RegulationResult:
        """Run one regulation cycle and return the result.

        Parameters
        ----------
        setpoint_c:
            Desired room temperature set by the user.
        room_temp_c:
            Measured room temperature from the external sensor.
        tado_internal_c:
            Temperature reported by Tado's own sensor (on the radiator).
        time_delta_s:
            Seconds elapsed since the last cycle (0.0 on the very first run).
        state:
            Previous regulation state (integral accumulator, etc.).
        """

        # 0. Guard: reject NaN/Inf inputs – they would corrupt all calculations
        for label, value in (
            ("setpoint", setpoint_c),
            ("room_temp", room_temp_c),
            ("tado_internal", tado_internal_c),
        ):
            if not math.isfinite(value):
                _LOGGER.error(
                    "Regulation aborted: %s is %s (not a finite number)",
                    label, value,
                )
                safe_target = (
                    max(self.cfg.min_target_c, min(self.cfg.max_target_c, setpoint_c))
                    if math.isfinite(setpoint_c)
                    else self.cfg.min_target_c
                )
                return RegulationResult(
                    target_for_tado_c=safe_target,
                    feedforward_offset_c=0.0,
                    p_correction_c=0.0,
                    i_correction_c=state.integral_c,
                    error_c=0.0,
                    is_saturated=False,
                    new_state=state,
                )

        # 1. Feedforward – compensate for sensor-placement offset
        feedforward_offset = tado_internal_c - room_temp_c

        # 2. Room error
        error = setpoint_c - room_temp_c

        # 3. Proportional correction (adaptive gain scheduling)
        effective_kp = self._effective_kp(error, self.cfg)
        p_correction = effective_kp * error

        # 4. Integral correction (carried from previous cycles)
        i_correction = state.integral_c

        # 5. Combine: base target + corrections
        base_target = setpoint_c + feedforward_offset
        raw_command = base_target + p_correction + i_correction

        # 6. Clamp to safe actuator range
        final_command = max(
            self.cfg.min_target_c,
            min(self.cfg.max_target_c, raw_command),
        )
        is_saturated = abs(final_command - raw_command) > _SATURATION_TOLERANCE_C

        # 7. Anti-windup (two mechanisms)
        new_integral = state.integral_c
        if time_delta_s > 0:
            saturated_high = raw_command > self.cfg.max_target_c
            saturated_low = raw_command < self.cfg.min_target_c

            # Mechanism A: block integration during output saturation
            may_integrate = True
            if saturated_high and error > 0:
                may_integrate = False
            if saturated_low and error < 0:
                may_integrate = False

            # Mechanism B: only accumulate near target, decay otherwise
            near_target = abs(error) < self.cfg.integral_deadband_c

            if may_integrate and near_target:
                # Near target → accumulate integral for steady-state accuracy
                new_integral += error * self.cfg.tuning.ki * time_delta_s
                new_integral = max(
                    self.cfg.integral_min_c,
                    min(self.cfg.integral_max_c, new_integral),
                )
            elif not near_target:
                # Far from target → decay integral to prevent overshoot
                new_integral *= self.cfg.integral_decay

        # 8. Build result
        new_state = RegulationState(
            integral_c=new_integral,
        )

        return RegulationResult(
            target_for_tado_c=round(final_command, 1),
            feedforward_offset_c=round(feedforward_offset, 2),
            p_correction_c=round(p_correction, 2),
            i_correction_c=round(new_integral, 2),
            error_c=round(error, 2),
            is_saturated=is_saturated,
            new_state=new_state,
        )
