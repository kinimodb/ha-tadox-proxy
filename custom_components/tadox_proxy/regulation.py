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

Anti-windup: the integral freezes whenever the output is saturated (clamped
at max or min target) *in the same direction as the error*, so it cannot
build up during phases where the system is already at full heating or off.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .parameters import RegulationConfig

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State & result data classes
# ---------------------------------------------------------------------------

@dataclass
class RegulationState:
    """Mutable state carried between regulation cycles."""

    integral_c: float = 0.0
    last_room_temp_c: Optional[float] = None


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

        # 1. Feedforward – compensate for sensor-placement offset
        feedforward_offset = tado_internal_c - room_temp_c

        # 2. Room error
        error = setpoint_c - room_temp_c

        # 3. Proportional correction
        p_correction = self.cfg.tuning.kp * error

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
        is_saturated = abs(final_command - raw_command) > 0.01

        # 7. Anti-windup: only accumulate integral when NOT saturated
        #    in the *same direction* as the error.
        new_integral = state.integral_c
        if time_delta_s > 0:
            saturated_high = raw_command > self.cfg.max_target_c
            saturated_low = raw_command < self.cfg.min_target_c

            may_integrate = True
            if saturated_high and error > 0:
                may_integrate = False
            if saturated_low and error < 0:
                may_integrate = False

            if may_integrate:
                new_integral += error * self.cfg.tuning.ki * time_delta_s
                new_integral = max(
                    self.cfg.integral_min_c,
                    min(self.cfg.integral_max_c, new_integral),
                )

        # 8. Build result
        new_state = RegulationState(
            integral_c=new_integral,
            last_room_temp_c=room_temp_c,
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
