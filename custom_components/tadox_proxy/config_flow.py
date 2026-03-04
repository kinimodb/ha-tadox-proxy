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
                # NOTE: Do NOT reload here. async_create_entry must return first
                # so HA persists the new options. The climate entity picks up
                # changes via its config_entry update listener. A reload is only
                # needed when sensor entity IDs change (listener re-registration).
                # We use async_call_later(0.5) so the entry write is committed
                # before the reload starts.
                entry_id = self.config_entry.entry_id
                old_opts = self.config_entry.options
                sensor_keys = (
                    CONF_WINDOW_SENSOR_ID,
                    CONF_PRESENCE_SENSOR_ID,
                    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
                )
                sensors_changed = any(
                    cleaned.get(k) != old_opts.get(k) for k in sensor_keys
                )
                if sensors_changed:
                    from homeassistant.helpers.event import async_call_later

                    async def _deferred_reload(_now) -> None:
                        await self.hass.config_entries.async_reload(entry_id)

                    async_call_later(self.hass, 0.5, _deferred_reload)

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
