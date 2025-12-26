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
from .parameters import RegulationConfig

# Helper to validate temperature sensors
def _is_temperature_sensor_state(state) -> bool:
    """Best-effort validation for a temperature sensor entity state."""
    if state is None:
        return False
    device_class = state.attributes.get("device_class")
    return device_class in (None, "temperature")


class TadoxProxyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """UI setup for the integration (Initial Setup)."""

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
                elif not _is_temperature_sensor_state(temp_state):
                    errors["base"] = "temp_not_temperature"
                else:
                    await self.async_set_unique_id(source_entity_id)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=name,
                        data={
                            CONF_SOURCE_ENTITY_ID: source_entity_id,
                            CONF_NAME: name,
                            CONF_EXTERNAL_TEMPERATURE_ENTITY_ID: ext_temp_entity_id,
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate")
                ),
                vol.Required(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Required(CONF_NAME, default="Tado X Proxy"): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return TadoxProxyOptionsFlow()


class TadoxProxyOptionsFlow(config_entries.OptionsFlowWithReload):
    """Per-entry options (gear icon) for PID tuning & sensors."""

    async def async_step_init(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate external sensor if changed
            ext_temp_entity_id = user_input.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
            if ext_temp_entity_id:
                temp_state = self.hass.states.get(ext_temp_entity_id)
                if temp_state is None:
                    errors["base"] = "temp_entity_not_found"
                elif not _is_temperature_sensor_state(temp_state):
                    errors["base"] = "temp_not_temperature"
            
            if not errors:
                return self.async_create_entry(title="", data=user_input)

        # Load current values (from options or fallback to defaults/data)
        current_options = self.config_entry.options
        current_data = self.config_entry.data
        defaults = RegulationConfig().tuning # Load defaults from parameters.py

        # Helper to get value: Option > Data > Default
        def get_val(key, default):
            return current_options.get(key, default)

        # Entity fallback
        current_ext_temp = current_options.get(
            CONF_EXTERNAL_TEMPERATURE_ENTITY_ID, 
            current_data.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
        )

        options_schema = vol.Schema(
            {
                # 1. PID Tuning Section
                vol.Required("kp", default=get_val("kp", defaults.kp)): 
                    vol.All(vol.Coerce(float), vol.Range(min=0.0, max=100.0)),
                vol.Required("ki", default=get_val("ki", defaults.ki)): 
                    vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
                vol.Required("kd", default=get_val("kd", defaults.kd)): 
                    vol.All(vol.Coerce(float), vol.Range(min=0.0, max=2000.0)),

                # 2. Sensor Configuration
                vol.Required(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID, default=current_ext_temp): 
                    selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                vol.Optional(CONF_EXTERNAL_HUMIDITY_ENTITY_ID, default=get_val(CONF_EXTERNAL_HUMIDITY_ENTITY_ID, None)): 
                    selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", device_class="humidity")
                    ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            errors=errors,
        )
