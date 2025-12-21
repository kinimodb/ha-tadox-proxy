from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import DOMAIN, CONF_SOURCE_ENTITY_ID, CONF_NAME


class TadoxProxyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """UI setup for the integration."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            source_entity_id = user_input[CONF_SOURCE_ENTITY_ID]
            name = user_input[CONF_NAME].strip()

            state = self.hass.states.get(source_entity_id)
            if state is None:
                errors["base"] = "entity_not_found"
            elif not source_entity_id.startswith("climate."):
                errors["base"] = "not_a_climate_entity"
            else:
                # Prevent duplicates: one proxy per source entity
                await self.async_set_unique_id(source_entity_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_SOURCE_ENTITY_ID: source_entity_id,
                        CONF_NAME: name,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate")
                ),
                vol.Required(CONF_NAME, default="Tado X Proxy"): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
