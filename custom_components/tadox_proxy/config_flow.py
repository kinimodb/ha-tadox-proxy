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


class TadoxProxyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """UI setup for the integration (initial setup).

    Split into two steps so entity selectors render reliably.
    Step 1 (user):    source climate entity + name
    Step 2 (sensors): external temperature sensor
    """

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_step_user(self, user_input=None):
        """Step 1 – select the source climate entity and a name."""
        errors: dict[str, str] = {}

        if user_input is not None:
            source_entity_id = user_input.get(CONF_SOURCE_ENTITY_ID, "")
            name = (user_input.get(CONF_NAME) or "").strip() or "Tado X Proxy"

            if not source_entity_id:
                errors["base"] = "entity_not_found"
            else:
                self._data = {
                    CONF_SOURCE_ENTITY_ID: source_entity_id,
                    CONF_NAME: name,
                }
                return await self.async_step_sensors()

        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate")
                ),
                vol.Required(CONF_NAME, default="Tado X Proxy"): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_sensors(self, user_input=None):
        """Step 2 – select the external temperature sensor."""
        errors: dict[str, str] = {}

        if user_input is not None:
            ext_temp_entity_id = user_input.get(
                CONF_EXTERNAL_TEMPERATURE_ENTITY_ID, ""
            )

            if not ext_temp_entity_id:
                errors["base"] = "temp_entity_not_found"
            else:
                await self.async_set_unique_id(self._data[CONF_SOURCE_ENTITY_ID])
                self._abort_if_unique_id_configured()

                self._data[CONF_EXTERNAL_TEMPERATURE_ENTITY_ID] = ext_temp_entity_id
                return self.async_create_entry(
                    title=self._data[CONF_NAME],
                    data=self._data,
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor", device_class="temperature"
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="sensors", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return TadoxProxyOptionsFlow()


class TadoxProxyOptionsFlow(config_entries.OptionsFlow):
    """Per-entry options (gear icon) for tuning, presets & sensor selection.

    Split into two steps so entity selectors render reliably.
    Step 1 (init):    regulation tuning + preset temperatures
    Step 2 (sensors): external temp sensor, window sensor, presence sensor
    """

    def __init__(self) -> None:
        self._options: dict = {}

    async def async_step_init(self, user_input=None):
        """Step 1 – regulation tuning and preset temperatures."""
        opts = self.config_entry.options
        defaults = RegulationConfig()

        if user_input is not None:
            self._options = dict(user_input)
            return await self.async_step_sensors()

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
                    default=opts.get(
                        CONF_BOOST_DURATION, defaults.presets.boost_duration_min
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
                vol.Required(
                    CONF_AWAY_TARGET,
                    default=opts.get(CONF_AWAY_TARGET, defaults.presets.away_target_c),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
                vol.Required(
                    CONF_FROST_PROTECTION_TARGET,
                    default=opts.get(
                        CONF_FROST_PROTECTION_TARGET,
                        defaults.presets.frost_protection_target_c,
                    ),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )

    async def async_step_sensors(self, user_input=None):
        """Step 2 – sensor selection (entity selectors on a separate page)."""
        opts = self.config_entry.options
        data = self.config_entry.data

        current_ext_temp = opts.get(
            CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
            data.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID),
        )

        if user_input is not None:
            # Merge tuning/preset options from step 1 with sensor options
            merged = {**self._options, **user_input}
            # Strip empty optional sensor values so they're stored as absent
            cleaned = {k: v for k, v in merged.items() if v not in (None, "")}
            # Reload is triggered by the update_listener in __init__.py
            # AFTER HA has persisted the new options, avoiding stale data.
            return self.async_create_entry(title="", data=cleaned)

        sensors_schema = vol.Schema(
            {
                # --- External temperature sensor ---
                vol.Required(
                    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
                    default=current_ext_temp,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor", device_class="temperature"
                    )
                ),
                # --- Window sensor (optional) ---
                vol.Optional(
                    CONF_WINDOW_SENSOR_ID,
                    description={
                        "suggested_value": opts.get(CONF_WINDOW_SENSOR_ID)
                    },
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
                    description={
                        "suggested_value": opts.get(CONF_PRESENCE_SENSOR_ID)
                    },
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
            step_id="sensors",
            data_schema=sensors_schema,
        )
