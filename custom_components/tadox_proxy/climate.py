"""Climate entity for Tado X Proxy (Hybrid control).

Key design goals:
- Use external room temperature sensor as the controlled variable.
- Use Tado thermostat target temperature as the manipulated variable (valve is a black box).
- Provide robust, explainable hybrid regulation strategy (BOOST / HOLD / COAST + Bias).
- Apply command policies (min delta, rate limiting, step-up limit, urgent decrease) to avoid thrashing.

Notes:
- This is a proxy climate entity. It does not implement PID.
- It is inspired by patterns from versatile_thermostat but specialized for Tado X integration patterns.
- All I/O is async and should not block the event loop.
- Uses a DataUpdateCoordinator for update timing and a separate control loop for regulation ticks.

Telemetry:
- Extra state attributes include regulator state, command policy decisions, last send metadata.
- A "context" is generated for each send and stored so we can correlate call_service events.

Window handling:
- Supports an external binary sensor for window open detection (optional).
- When window is open, set a frost protection setpoint (override).

Author: ha-tadox-proxy
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from typing import Any, cast

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    CONF_NAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.service import async_call_from_config
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_HYBRID_BIAS,
    ATTR_HYBRID_CMD,
    ATTR_HYBRID_STATE,
    ATTR_ROOM_TEMPERATURE,
    ATTR_TADO_SETPOINT,
    ATTR_TADO_TEMPERATURE,
    CONF_ROOM_SENSOR_ENTITY_ID,
    CONF_TADO_CLIMATE_ENTITY_ID,
    CONF_TADO_TEMP_ENTITY_ID,
    CONF_WINDOW_SENSOR_ENTITY_ID,
    DEFAULT_CONTROL_INTERVAL_S,
    DEFAULT_MAX_TEMP_C,
    DEFAULT_MIN_COMMAND_INTERVAL_S,
    DEFAULT_MIN_SETPOINT_DELTA_C,
    DEFAULT_MIN_TEMP_C,
    DEFAULT_STEP_UP_LIMIT_C,
    DOMAIN,
    FAST_RECOVERY_MAX_C,
    WINDOW_FROST_TEMP_C,
)
from .hybrid_regulation import HybridConfig, HybridRegulator, HybridState, WindowMode
from .regulation import CommandPolicy

_LOGGER = logging.getLogger(__name__)


class TadoXProxyClimate(ClimateEntity, RestoreEntity):
    """Proxy climate entity controlling a Tado thermostat based on external room temperature."""

    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_min_temp = DEFAULT_MIN_TEMP_C
    _attr_max_temp = DEFAULT_MAX_TEMP_C
    _attr_temperature_unit = "°C"

    def __init__(self, hass: HomeAssistant, entry: Any, coordinator: Any) -> None:
        """Initialize the proxy thermostat."""
        self.hass = hass
        self._entry = entry
        self._coordinator = coordinator

        data = dict(entry.data or {})
        options = dict(entry.options or {})

        self._name: str = data.get(CONF_NAME) or entry.title or "Tado X Proxy"

        # Entities
        self._tado_entity_id: str | None = cast(str | None, data.get(CONF_TADO_CLIMATE_ENTITY_ID))
        self._room_sensor_entity_id: str | None = cast(str | None, data.get(CONF_ROOM_SENSOR_ENTITY_ID))
        self._tado_temp_entity_id: str | None = cast(str | None, data.get(CONF_TADO_TEMP_ENTITY_ID))
        self._window_
