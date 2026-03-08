"""Central parameter defaults for tadox_proxy."""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Behavioural thresholds (climate-entity logic, independent of the PI engine)
# ---------------------------------------------------------------------------

@dataclass
class BehaviourConfig:
    """Thresholds for the follow-Tado and send-decision logic.

    These values can be overridden via config-entry options so operators can
    tune the integration's responsiveness without touching source code.
    """

    # Follow-Tado: min divergence from last-sent setpoint to treat as a
    # physical user change (°C).
    follow_threshold_c: float = 0.5

    # Follow-Tado: grace period (s) after our last command during which we
    # ignore Tado setpoint changes (Tado may still be acknowledging via
    # Thread/cloud).
    follow_grace_s: float = 20.0

    # Rate-limit bypass: immediately send if the new target is this many °C
    # below the current Tado setpoint (urgent cool-down).
    urgent_decrease_threshold_c: float = 1.0


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
# Preset defaults
# ---------------------------------------------------------------------------

@dataclass
class PresetConfig:
    """Temperature settings for each preset mode."""

    eco_target_c: float = 17.0       # fixed eco temperature (independent of comfort)
    boost_target_c: float = 25.0     # fixed target during boost
    boost_duration_min: int = 30     # auto-revert to comfort after this many minutes
    away_target_c: float = 17.0      # fixed target when away
    frost_protection_target_c: float = 7.0   # frost protection temperature


# ---------------------------------------------------------------------------
# Full regulation config with safety rails
# ---------------------------------------------------------------------------

@dataclass
class RegulationConfig:
    """All regulation parameters and safety limits."""

    tuning: CorrectionTuning = field(default_factory=CorrectionTuning)
    presets: PresetConfig = field(default_factory=PresetConfig)

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
