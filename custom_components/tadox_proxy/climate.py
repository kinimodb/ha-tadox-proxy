"""Proxy climate entity for tadox_proxy.

This is the user-facing entity that appears as climate.<name>_proxy in HA.

It exposes HVAC modes, setpoint and current temperature, and internally manages
the PID-based regulation cycle, which sets the temperature on the source thermostat
(e.g. climate.livingroom_tado).
"""

from __future__ import annotations

from datetime import timedelta
import logging
import time

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate.const import (
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .regulation import PidRegulator, RegulationConfig, PidState

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=300)


class TadoxProxyClimate(CoordinatorEntity, ClimateEntity):
    """Proxy thermostat for Tado X using PID regulation."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_max_temp = DEFAULT_MAX_TEMP
    _attr_min_temp = DEFAULT_MIN_TEMP
    _attr_should_poll = False

    def __init__(self, coordinator, name: str, source_entity_id: str) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{source_entity_id}_proxy"
        self._attr_target_temperature = 21.0
        self._attr_current_temperature = None
        self._attr_hvac_mode = HVACMode.HEAT
        self._source_entity_id = source_entity_id

        # Regulation core
        self._regulator = PidRegulator(RegulationConfig())

    async def async_set_temperature(self, **kwargs):
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = temp
            await self._async_run_regulation_cycle()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode):
        self._attr_hvac_mode = hvac_mode
        await self._async_run_regulation_cycle()

    async def _async_run_regulation_cycle(self):
        """Main regulation cycle triggered periodically or on user interaction."""
        if self._attr_hvac_mode == HVACMode.OFF:
            target = 5.0  # frost protection
            reason = "hvac_off"
        else:
            now = time.monotonic()
            user_target = self._attr_target_temperature or 21.0
            measured = self._attr_current_temperature
            if measured is None:
                _LOGGER.warning("No measured temperature available, skipping regulation")
                return

            result = self._regulator.step(
                user_setpoint_c=user_target,
                measured_temp_c=measured,
                now_ts_s=now,
            )
            target = result.target_c
            reason = result.reason

        _LOGGER.info("Applying target %.2f°C to %s (%s)", target, self._source_entity_id, reason)
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {
                "entity_id": self._source_entity_id,
                "temperature": target,
            },
            blocking=True,
            context=self._context,
        )

    async def async_update(self):
        """Called periodically (via SCAN_INTERVAL)."""
        # In real use, pull temperature from external sensor or coordinator
        # For now, simulate measurement
        self._attr_current_temperature = 20.0
        await self._async_run_regulation_cycle()
