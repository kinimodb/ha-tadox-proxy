"""Central parameter defaults for tadox_proxy."""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Integration behaviour defaults
# ---------------------------------------------------------------------------

DEFAULT_CONTROL_INTERVAL_S: int = 60   # seconds between regulation cycles
FROST_PROTECT_C: float = 5.0           # target temperature when HVAC is OFF


# ---------------------------------------------------------------------------
# Correction tuning  (PI layer on top of feedforward)
# ---------------------------------------------------------------------------

@dataclass
class CorrectionTuning:
    """PI correction parameters applied on top of the feedforward offset.

    These are intentionally *small* – the feedforward does the heavy lifting.
    """

    kp: float = 0.8    # proportional gain for residual room-error correction
    ki: float = 0.003   # integral gain for slow steady-state error correction


# ---------------------------------------------------------------------------
# Full regulation config with safety rails
# ---------------------------------------------------------------------------

@dataclass
class RegulationConfig:
    """All regulation parameters and safety limits."""

    tuning: CorrectionTuning = field(default_factory=CorrectionTuning)

    # Absolute temperature limits for commands sent to Tado
    min_target_c: float = 5.0
    max_target_c: float = 25.0

    # Anti-windup limits for the integral correction term
    integral_min_c: float = -2.0
    integral_max_c: float = 2.0

    # Integral deadband: only accumulate integral when |error| < this value.
    # Outside this zone the integral decays, preventing buildup during gross
    # heating/cooling transients that would cause overshoot.
    integral_deadband_c: float = 0.3

    # Decay factor applied to the integral each cycle when |error| >= deadband.
    # 0.95 means ~5% reduction per 60s cycle → drains in ~15 min.
    integral_decay: float = 0.95

    # Rate limiting: minimum seconds between commands to Tado (battery saving)
    min_command_interval_s: float = 180.0

    # Minimum difference to current Tado setpoint before sending a new command
    min_change_threshold_c: float = 0.3
