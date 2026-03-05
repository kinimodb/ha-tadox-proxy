"""Config and Options flows for Tado X Proxy."""
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
    CONF_COMFORT_TARGET,
    CONF_ECO_TARGET,
    CONF_BOOST_TARGET,
    CONF_BOOST_DURATION,
    CONF_AWAY_TARGET,
    CONF_FROST_PROTECTION_TARGET,
    CONF_WINDOW_SENSOR_ID,
    CONF_WINDOW_DELAY_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_PRESENCE_AWAY_DELAY_S,
)
from .parameters import RegulationConfig


def _is_temperature_sensor_state(state) -> bool:
    """Best-effort validation for a temperature sensor entity state."""
    if state is None:
        return False
    device_class = state.attributes.get("device_class")
    return device_class in (None, "temperature")


class TadoxProxyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """UI setup for the integration (initial setup)."""

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


class TadoxProxyOptionsFlow(config_entries.OptionsFlow):
    """Per-entry options (gear icon) for tuning, presets & sensor selection."""

    async def async_step_init(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            ext_temp_entity_id = user_input.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
            if ext_temp_entity_id:
                temp_state = self.hass.states.get(ext_temp_entity_id)
                if temp_state is None:
                    errors["base"] = "temp_entity_not_found"
                elif not _is_temperature_sensor_state(temp_state):
                    errors["base"] = "temp_not_temperature"

            if not errors:
                # Strip empty optional sensor values so they're stored as absent
                cleaned = {k: v for k, v in user_input.items() if v not in (None, "")}
                # Reload is triggered by the update_listener in __init__.py
                # AFTER HA has persisted the new options, avoiding stale data.
                return self.async_create_entry(title="", data=cleaned)

        # Load current values (options > data > defaults)
        opts = self.config_entry.options
        data = self.config_entry.data
        defaults = RegulationConfig()

        current_ext_temp = opts.get(
            CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
            data.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID),
        )

        options_schema = vol.Schema(
            {
                # --- Correction tuning ---
                vol.Required(
                    "correction_kp",
                    default=opts.get("correction_kp", defaults.tuning.kp),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=5.0)),
                vol.Required(
                    "correction_ki",
                    default=opts.get("correction_ki", defaults.tuning.ki),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=0.1)),

                # --- Preset temperatures ---
                vol.Required(
                    CONF_COMFORT_TARGET,
                    default=opts.get(CONF_COMFORT_TARGET, 20.0),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
                vol.Required(
                    CONF_ECO_TARGET,
                    default=opts.get(CONF_ECO_TARGET, defaults.presets.eco_target_c),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
                vol.Required(
                    CONF_BOOST_TARGET,
                    default=opts.get(CONF_BOOST_TARGET, defaults.presets.boost_target_c),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
                vol.Required(
                    CONF_BOOST_DURATION,
                    default=opts.get(CONF_BOOST_DURATION, defaults.presets.boost_duration_min),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
                vol.Required(
                    CONF_AWAY_TARGET,
                    default=opts.get(CONF_AWAY_TARGET, defaults.presets.away_target_c),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
                vol.Required(
                    CONF_FROST_PROTECTION_TARGET,
                    default=opts.get(CONF_FROST_PROTECTION_TARGET, defaults.presets.frost_protection_target_c),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),

                # --- External sensor ---
                vol.Required(
                    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
                    default=current_ext_temp,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),

                # --- Window sensor (optional) ---
                vol.Optional(
                    CONF_WINDOW_SENSOR_ID,
                    description={"suggested_value": opts.get(CONF_WINDOW_SENSOR_ID)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Required(
                    CONF_WINDOW_DELAY_S,
                    default=opts.get(CONF_WINDOW_DELAY_S, 30),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),

                # --- Presence sensor (optional) ---
                vol.Optional(
                    CONF_PRESENCE_SENSOR_ID,
                    description={"suggested_value": opts.get(CONF_PRESENCE_SENSOR_ID)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Required(
                    CONF_PRESENCE_AWAY_DELAY_S,
                    default=opts.get(CONF_PRESENCE_AWAY_DELAY_S, 1800),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=7200)),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            errors=errors,
        )
