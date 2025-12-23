from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN, CONF_SOURCE_ENTITY_ID, CONF_NAME

# (Vorläufig) Optionen/Config-Keys – wir ziehen die später in const.py, wenn wir sie in climate.py nutzen
CONF_EXTERNAL_TEMPERATURE_ENTITY_ID = "external_temperature_entity_id"
CONF_EXTERNAL_HUMIDITY_ENTITY_ID = "external_humidity_entity_id"
CONF_WINDOW_SENSOR_ENTITY_ID = "window_sensor_entity_id"
CONF_PRESENCE_SENSOR_ENTITY_ID = "presence_sensor_entity_id"


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
                elif not _is_temperature_sensor_state(temp_state):
                    errors["base"] = "temp_not_temperature"
                else:
                    # Prevent duplicates: one proxy per source entity
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
        """Create the options flow."""
        return TadoxProxyOptionsFlow()


class TadoxProxyOptionsFlow(config_entries.OptionsFlowWithReload):
    """Per-entry options (gear icon) for the proxy thermostat."""

    async def async_step_init(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            ext_temp_entity_id = user_input[CONF_EXTERNAL_TEMPERATURE_ENTITY_ID]
            temp_state = self.hass.states.get(ext_temp_entity_id)

            if temp_state is None:
                errors["base"] = "temp_entity_not_found"
            elif not ext_temp_entity_id.startswith("sensor."):
                errors["base"] = "temp_not_a_sensor"
            elif not _is_temperature_sensor_state(temp_state):
                errors["base"] = "temp_not_temperature"
            else:
                return self.async_create_entry(title="", data=user_input)

        # Suggested values: prefer existing options; fall back to entry.data for the temperature sensor
        suggested = dict(self.config_entry.options)
        if (
            CONF_EXTERNAL_TEMPERATURE_ENTITY_ID not in suggested
            and CONF_EXTERNAL_TEMPERATURE_ENTITY_ID in self.config_entry.data
        ):
            suggested[CONF_EXTERNAL_TEMPERATURE_ENTITY_ID] = self.config_entry.data[
                CONF_EXTERNAL_TEMPERATURE_ENTITY_ID
            ]

        options_schema = vol.Schema(
            {
                vol.Required(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                # Für später: schon sauber im UI vorbereitet (noch ohne Wirkung in der Logik)
                vol.Optional(CONF_EXTERNAL_HUMIDITY_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="humidity")
                ),
                vol.Optional(CONF_WINDOW_SENSOR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Optional(CONF_PRESENCE_SENSOR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["binary_sensor", "device_tracker"])
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(options_schema, suggested),
            errors=errors,
        )
