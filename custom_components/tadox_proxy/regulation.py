"""
PID Regulation Logic for Tado X Proxy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .parameters import RegulationConfig

_LOGGER = logging.getLogger(__name__)

@dataclass
class RegulationState:
    """State of the PID loop passed between cycles."""
    last_error_c: float = 0.0
    integral_term_c: float = 0.0
    # For smoothing derivative
    last_input_c: Optional[float] = None
    derivative_ema_c_per_s: float = 0.0

@dataclass
class RegulationResult:
    """Result of a regulation cycle."""
    output_delta_c: float
    p_term_c: float
    i_term_c: float
    d_term_c: float
    error_c: float
    deadband_active: bool
    new_state: RegulationState
    debug_info: dict

class PidRegulator:
    """Stateless PID calculator (state is passed in/out)."""

    def __init__(self, config: RegulationConfig):
        self.config = config

    def compute(
        self,
        setpoint_c: float,
        current_temp_c: float,
        time_delta_s: float,
        state: RegulationState,
    ) -> RegulationResult:
        """
        Calculate PID output.
        
        CRITICAL CHANGE v0.3: 
        Removed 'Hard Deadband'. The PID now calculates CONTINUOUSLY even if 
        the error is small. This allows the I-term to maintain a holding value 
        (equilibrium) to keep the valve slightly open, preventing the 
        'pendulum effect' (sawtooth) caused by shutting off completely at target.
        """
        
        # 1. Calculate Error
        error = setpoint_c - current_temp_c

        # 2. Proportional Term
        # Immediate reaction to error.
        p_term = self.config.tuning.kp * error

        # 3. Integral Term
        # Accumulates error over time to overcome static offsets (heat loss, valve offset).
        # We accumulate even inside the 'deadband' zone to find equilibrium.
        new_integral = state.integral_term_c + (error * self.config.tuning.ki * time_delta_s)
        
        # Anti-Windup: Clamp the I-term absolute value
        new_integral = max(
            self.config.integral_term_min_c,
            min(self.config.integral_term_max_c, new_integral)
        )
        i_term = new_integral

        # 4. Derivative Term (on Measurement, not Error, to avoid setpoint kick)
        # d(Error)/dt = d(Setpoint - Input)/dt = - d(Input)/dt (assuming constant setpoint)
        d_term = 0.0
        new_derivative_ema = state.derivative_ema_c_per_s

        if time_delta_s > 0 and state.last_input_c is not None:
            # Raw slope: -(current - last) / dt
            input_slope = (current_temp_c - state.last_input_c) / time_delta_s
            
            # Apply EMA Filter to slope
            alpha = self.config.derivative_ema_alpha
            new_derivative_ema = (alpha * input_slope) + ((1.0 - alpha) * state.derivative_ema_c_per_s)
            
            # D-Term tries to oppose the movement (braking)
            # D = - Kd * slope
            d_term = -1.0 * self.config.tuning.kd * new_derivative_ema

        # 5. Total Output
        # Base PID output
        raw_output = p_term + i_term + d_term

        # 6. Deadband Logic (Soft Mode)
        # We report if we are inside deadband, but we DO NOT force output to 0.
        # This allows the logic to "hold" the temperature.
        in_deadband = abs(error) < self.config.deadband_c

        # 7. Safety Clamping
        # Limit the authority of the proxy (e.g., +/- 4°C on top of setpoint)
        final_output = max(
            -self.config.max_delta_c,
            min(self.config.max_delta_c, raw_output)
        )

        # Update State
        new_state = RegulationState(
            last_error_c=error,
            integral_term_c=new_integral,
            last_input_c=current_temp_c,
            derivative_ema_c_per_s=new_derivative_ema
        )

        return RegulationResult(
            output_delta_c=final_output,
            p_term_c=p_term,
            i_term_c=i_term,
            d_term_c=d_term,
            error_c=error,
            deadband_active=in_deadband, # Status info only, logic proceeds
            new_state=new_state,
            debug_info={
                "raw_p": p_term,
                "raw_i": new_integral,
                "raw_d": d_term,
                "raw_sum": raw_output
            }
        )
