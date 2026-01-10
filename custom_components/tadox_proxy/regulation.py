"""
PID Regulation Logic for Tado X Proxy.

This module contains:
- A classic PID regulator (delta output) used for legacy / alternative control paths.
- A command hygiene policy (CommandPolicy) used by the hybrid-control climate entity.

The hybrid controller itself lives in hybrid_regulation.py. This module only helps
with "how to send" (rate limiting, min delta, step-up limiting).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .parameters import RegulationConfig

_LOGGER = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Legacy / alternative: PID delta controller
# -----------------------------------------------------------------------------

@dataclass
class RegulationState:
    """State of the PID loop passed between cycles."""
    last_error_c: float = 0.0
    integral_term_c: float = 0.0
    # Derivative EMA (smoothed derivative)
    derivative_ema_c: float = 0.0


@dataclass
class RegulationResult:
    """Result of the PID calculation."""
    output_delta_c: float
    p_term_c: float
    i_term_c: float
    d_term_c: float
    error_c: float
    deadband_active: bool
    new_state: RegulationState
    debug_info: dict


class PIDRegulator:
    """PID regulator producing a delta on top of a baseline setpoint."""

    def __init__(self, config: RegulationConfig) -> None:
        self._cfg = config

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

        cfg = self._cfg

        # Defensive dt
        dt = max(1.0, float(time_delta_s))

        # 1. Calculate Error
        error = float(setpoint_c) - float(current_temp_c)

        # 2. Proportional term
        p_term = cfg.tuning.kp * error

        # 3. Integral term (with anti-windup clamp)
        new_integral = state.integral_term_c + (cfg.tuning.ki * error * (dt / 60.0))
        new_integral = max(cfg.integral_term_min_c, min(cfg.integral_term_max_c, new_integral))
        i_term = new_integral

        # 4. Derivative term (EMA smoothed)
        raw_derivative = (error - state.last_error_c) / dt
        derivative_ema = (cfg.derivative_ema_alpha * raw_derivative) + (
            (1.0 - cfg.derivative_ema_alpha) * state.derivative_ema_c
        )
        d_term = cfg.tuning.kd * derivative_ema

        # 5. Sum raw output
        raw_output = p_term + i_term + d_term

        # 6. Clamp output delta
        final_output = max(-cfg.max_delta_c, min(cfg.max_delta_c, raw_output))

        # 7. Soft deadband flag only (do NOT stop integration, just report)
        in_deadband = abs(error) < cfg.deadband_c

        new_state = RegulationState(
            last_error_c=error,
            integral_term_c=new_integral,
            derivative_ema_c=derivative_ema,
        )

        return RegulationResult(
            output_delta_c=final_output,
            p_term_c=p_term,
            i_term_c=i_term,
            d_term_c=d_term,
            error_c=error,
            deadband_active=in_deadband,  # Status info only, logic proceeds
            new_state=new_state,
            debug_info={
                "raw_p": p_term,
                "raw_i": new_integral,
                "raw_d": d_term,
                "raw_sum": raw_output,
            },
        )


# -----------------------------------------------------------------------------
# Hybrid-control: command hygiene policy expected by climate.py
# -----------------------------------------------------------------------------

class RegulationMode(str, Enum):
    """High-level regulation mode (compatibility layer)."""
    AUTO = "auto"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Decision returned by CommandPolicy.apply()."""
    send: bool
    setpoint: float
    reason: str


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    """Command send policy / hygiene.

    - First send always sends.
    - Ignore tiny changes (min delta).
    - Decreases are urgent (send immediately).
    - Increases are rate limited and step-limited.
    """

    min_command_interval_s: float = 60.0
    min_setpoint_delta_c: float = 0.5
    step_up_limit_c: float = 2.0
    fast_recovery_max_c: float = 0.0
    fast_recovery_duration_s: float = 0.0

    def apply(
        self,
        *,
        desired_setpoint: float,
        last_sent_setpoint: Optional[float],
        last_sent_ts: Optional[float],
        now_ts: float,
    ) -> PolicyDecision:
        desired = float(desired_setpoint)

        # First ever send
        if last_sent_setpoint is None or last_sent_ts is None:
            decision = PolicyDecision(True, desired, "first_send")
            _LOGGER.debug(
                "CommandPolicy decision=%s desired=%.3f last=None dt=None delta=None",
                decision.reason,
                desired,
            )
            return decision

        last = float(last_sent_setpoint)
        dt = float(now_ts - last_sent_ts)
        delta = desired - last

        # Noise guard
        if abs(delta) < self.min_setpoint_delta_c:
            decision = PolicyDecision(False, last, "below_min_delta")
            _LOGGER.debug(
                "CommandPolicy decision=%s desired=%.3f last=%.3f dt=%.1f delta=%.3f "
                "(min_delta=%.3f min_interval=%.1f step_up_limit=%.3f)",
                decision.reason,
                desired,
                last,
                dt,
                delta,
                self.min_setpoint_delta_c,
                self.min_command_interval_s,
                self.step_up_limit_c,
            )
            return decision

        # Urgent decrease bypasses rate limit
        if delta <= -self.min_setpoint_delta_c:
            decision = PolicyDecision(True, desired, "urgent_decrease")
            _LOGGER.debug(
                "CommandPolicy decision=%s desired=%.3f last=%.3f dt=%.1f delta=%.3f "
                "(min_delta=%.3f min_interval=%.1f step_up_limit=%.3f)",
                decision.reason,
                desired,
                last,
                dt,
                delta,
                self.min_setpoint_delta_c,
                self.min_command_interval_s,
                self.step_up_limit_c,
            )
            return decision

        # Rate limit for increases
        if dt < self.min_command_interval_s:
            decision = PolicyDecision(False, last, "rate_limited")
            _LOGGER.debug(
                "CommandPolicy decision=%s desired=%.3f last=%.3f dt=%.1f delta=%.3f "
                "(min_delta=%.3f min_interval=%.1f step_up_limit=%.3f)",
                decision.reason,
                desired,
                last,
                dt,
                delta,
                self.min_setpoint_delta_c,
                self.min_command_interval_s,
                self.step_up_limit_c,
            )
            return decision

        # Step-up limit for increases
        if delta > self.step_up_limit_c:
            limited = last + self.step_up_limit_c
            if abs(limited - last) < self.min_setpoint_delta_c:
                decision = PolicyDecision(False, last, "step_up_too_small")
                _LOGGER.debug(
                    "CommandPolicy decision=%s desired=%.3f last=%.3f dt=%.1f delta=%.3f limited=%.3f "
                    "(min_delta=%.3f min_interval=%.1f step_up_limit=%.3f)",
                    decision.reason,
                    desired,
                    last,
                    dt,
                    delta,
                    limited,
                    self.min_setpoint_delta_c,
                    self.min_command_interval_s,
                    self.step_up_limit_c,
                )
                return decision

            decision = PolicyDecision(True, limited, "step_up_limited")
            _LOGGER.debug(
                "CommandPolicy decision=%s desired=%.3f last=%.3f dt=%.1f delta=%.3f limited=%.3f "
                "(min_delta=%.3f min_interval=%.1f step_up_limit=%.3f)",
                decision.reason,
                desired,
                last,
                dt,
                delta,
                limited,
                self.min_setpoint_delta_c,
                self.min_command_interval_s,
                self.step_up_limit_c,
            )
            return decision

        decision = PolicyDecision(True, desired, "send")
        _LOGGER.debug(
            "CommandPolicy decision=%s desired=%.3f last=%.3f dt=%.1f delta=%.3f "
            "(min_delta=%.3f min_interval=%.1f step_up_limit=%.3f)",
            decision.reason,
            desired,
            last,
            dt,
            delta,
            self.min_setpoint_delta_c,
            self.min_command_interval_s,
            self.step_up_limit_c,
        )
        return decision
