from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_SOURCE_ENTITY_ID,
    CONF_NAME,
    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
    CONF_EXTERNAL_HUMIDITY_ENTITY_ID,
    CONF_WINDOW_SENSOR_ENTITY_ID,
    CONF_PRESENCE_SENSOR_ENTITY_ID,
)


def _is_temperature_sensor_state(state) -> bool:
    """Best-effort validation for a temperature sensor entity state."""
    if state is None:
        return False
    device_class = state.attributes.get("device_class")
    # If device_class is present, require temperature; if absent, accept (some sensors don't set it cleanly).
    return device_class in (None, "temperature")


class TadoxProxyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """UI setup for the integration."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            source_entity_id = user_input[CONF_SOURCE_ENTITY_ID]
            ext_temp_entity_id = user_input[CONF_EXTERNAL_TEMPERATURE_ENTITY_ID]
            name = (user_input.get(CONF_NAME) or "").strip() or "Tado X Proxy"

            source_state = self.hass.states.get(source_entity_id)
            if source_state is None:
                errors["base"] = "entity_not_found"
            elif not source_entity_id.startswith("climate."):
                errors["base"] = "not_a_climate_entity"
            else:
                temp_state = self.hass.states.get(ext_temp_entity_id)
                if temp_state is None:
                    errors["base"] = "temp_entity_not_found"
                elif not ext_temp_entity_id.startswith("sensor."):
                    errors["base"] = "temp_not_a_sensor"
