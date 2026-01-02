"""Entry-derived configuration for the proxy thermostat.

This module provides a compatibility layer used by climate.py. It keeps
all "what comes from the config entry" logic in one place, without changing the
controller behavior.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_NAME,
    CONF_WINDOW_OPEN_ENABLED,
    CONF_WINDOW_SENSOR_ENTITY_ID,
    CONF_WINDOW_OPEN_DELAY_MIN,
    CONF_WINDOW_CLOSE_DELAY_MIN,
)
from .parameters import PidTuning


@dataclass(frozen=True, slots=True)
class RegulationConfig:
    """Runtime configuration assembled from ConfigEntry data + options."""

    # Display / identity
    name: str

    # Command bounds (aligned with HybridConfig defaults)
    min_target_c: float = 5.0
    max_target_c: float = 25.0
    default_target_c: float = 21.0

    # Command hygiene
    min_command_interval_s: float = 60.0

    # OptionsFlow tuning keys ("kp", "ki", "kd") mapped into HybridConfig kp/ki_small
    tuning: PidTuning = field(default_factory=PidTuning)

    # Window handling options (OptionsFlow)
    window_open_enabled: bool = False
    window_sensor_entity_id: str | None = None
    window_open_delay_min: int = 0
    window_close_delay_min: int = 0

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> "RegulationConfig":
        """Build config from config entry data + options (defensive)."""
        data = dict(entry.data or {})
        opts = dict(entry.options or {})

        name = (data.get(CONF_NAME) or entry.title or "Tado X Proxy").strip() or "Tado X Proxy"

        defaults = PidTuning()
        tuning = PidTuning(
            kp=float(opts.get("kp", defaults.kp)),
            ki=float(opts.get("ki", defaults.ki)),
            kd=float(opts.get("kd", defaults.kd)),
        )

        return cls(
            name=name,
            tuning=tuning,
            window_open_enabled=bool(opts.get(CONF_WINDOW_OPEN_ENABLED, False)),
            window_sensor_entity_id=opts.get(CONF_WINDOW_SENSOR_ENTITY_ID),
            window_open_delay_min=int(opts.get(CONF_WINDOW_OPEN_DELAY_MIN, 0) or 0),
            window_close_delay_min=int(opts.get(CONF_WINDOW_CLOSE_DELAY_MIN, 0) or 0),
        )
