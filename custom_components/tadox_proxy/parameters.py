"""Central parameter defaults for tadox_proxy.

Cleanup v0.3: Removed Tado Mapping logic.
"""
from __future__ import annotations
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Proxy / integration behavior defaults
# ---------------------------------------------------------------------------

DEFAULT_CONTROL_INTERVAL_S: int = 60  # 1 min
FROST_PROTECT_C: float = 5.0
WILL_HEAT_EPS_C: float = 0.05
RATE_LIMIT_DECREASE_EPS_C: float = 0.05


# ---------------------------------------------------------------------------
# PID tuning + regulation safety rails
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PidTuning:
    """PID tuning parameters."""
    # Aggressive P to overcome Tado internal heat offset
    kp: float = 7.0
    # Slow I to maintain holding temperature (approx 23min time constant)
    ki: float = 0.005
    # Strong D to brake overshoot
    kd: float = 600.0


@dataclass(frozen=True)
class RegulationConfig:
    """Regulation parameters and safety rails."""

    tuning: PidTuning = PidTuning()

    # Soft Deadband: PID keeps calculating I-term, but we don't send updates 
    # if error is small, unless I-term drift requires it.
    deadband_c: float = 0.20

    # Output limits (delta on top of setpoint)
    max_delta_c: float = 4.0

    # Absolute actuator limits
    min_target_c: float = 5.0
    max_target_c: float = 25.0

    # Rate limit (5 min to save battery)
    min_command_interval_s: float = 300.0

    # Anti short-cycling
    min_on_s: float = 300.0
    min_off_s: float = 300.0

    # Heating state thresholds
    heat_on_threshold_delta_c: float = 0.20
    heat_off_threshold_delta_c: float = 0.05

    # Anti-windup rail for Integral term
    integral_term_min_c: float = -2.0
    integral_term_max_c: float = 2.0

    # Derivative smoothing
    derivative_ema_alpha: float = 0.20
